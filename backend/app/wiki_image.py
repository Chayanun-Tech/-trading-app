"""ค้นรูปจริงของบริษัท/สินค้าจาก Wikipedia ให้ Ecosystem Map ใช้ประกอบเนื้อหา

ทำไมใช้ Wikipedia แทนการดึงรูปจากเว็บทั่วไป: รูปบน Wikipedia มาจาก Wikimedia Commons ซึ่ง
เจ้าของภาพตั้งใจปล่อยให้ใช้ซ้ำได้ตามสัญญาอนุญาต (CC-BY-SA/public domain) ต่างจากรูปสินค้าที่
scrape มาจากเว็บข่าว/เว็บบริษัทเองซึ่งมักสงวนลิขสิทธิ์ — ใช้ REST summary API ฟรี ไม่ต้อง key.

cache ผลลงดิสก์ยาว (รูปบริษัท/สินค้าแทบไม่เปลี่ยน) รวมกรณี "หาไม่เจอ" ด้วย กัน retry ซ้ำที่
query เดิมทุกครั้งที่ generate ธีมใหม่.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.parse import quote

import httpx

_CACHE_DIR = Path(__file__).resolve().parents[1].parent / "data" / "financials" / "wiki_images"
_TTL = 365 * 24 * 3600
# Wikimedia ขอ User-Agent ที่ระบุตัวตนแอป + ช่องทางติดต่อ ไม่งั้นเสี่ยงโดน throttle/บล็อก
_HEADERS = {"User-Agent": "ChayanunOperating-trading-app/ecosystem-map (chayanun250841@gmail.com)"}
_SEARCH_URL = "https://en.wikipedia.org/w/api.php"
_SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"

# ชื่อบริษัทหลายตัวเป็นคำธรรมดา (เช่น "Apple" ชนกับผลไม้, "Target" ชนกับคำทั่วไป) ค้นตรงๆ
# อาจได้บทความผิดเรื่อง — เช็ค description จาก Wikidata ว่า "ดูเหมือนบริษัทจริง" ก่อนเชื่อ
# ถ้าไม่ผ่านค่อย retry ด้วยคำค้นที่เติม 'company' ต่อท้ายเพื่อบังคับ disambiguate
_COMPANY_HINTS = (
    "company", "corporation", "corp", "business", "manufacturer", "brand", "conglomerate",
    "retailer", "retail", "developer", "technology", "telecommunications", "telecom",
    "bank", "insurer", "insurance", "airline", "automaker", "automotive", "semiconductor",
    "pharmaceutical", "biotechnology", "enterprise", "firm", "producer", "chain", "holding",
    "group", "industries", "industrial", "energy", "utility", "mining", "chemical",
    "electronics", "software", "hardware", "publisher", "broadcaster", "multinational",
)


def _looks_like_company(description: str | None) -> bool:
    if not description:
        return False
    d = description.lower()
    return any(h in d for h in _COMPANY_HINTS)


def _cache_path(query: str, kind: str) -> Path:
    # namespace ตาม kind (company/topic) กันชนกัน — คำค้นเดียวกันอาจได้ผลต่างกันเพราะกฎ
    # disambiguate ต่างกันระหว่างค้นหา "บริษัท" กับค้นหา "สินค้า/หัวข้อทั่วไป"
    safe = "".join(c if c.isalnum() else "_" for c in query.strip().lower())[:80] or "blank"
    return _CACHE_DIR / f"{kind}_{safe}.json"


def _load_cache(query: str, kind: str) -> dict | None:
    path = _cache_path(query, kind)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if time.time() - data.get("cached_at", 0) < _TTL:
        return data
    return None


def _save_cache(query: str, kind: str, result: dict | None) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(query, kind).write_text(
        json.dumps({"cached_at": time.time(), "result": result}, ensure_ascii=False), encoding="utf-8")


async def _search_and_summarize(client: httpx.AsyncClient, search_term: str) -> dict | None:
    """ค้นชื่อ → เอาไตเติลอันดับ 1 → ดึง summary (description + thumbnail) คืน None ถ้าไม่เจอ/ไม่มีรูป."""
    r = await client.get(_SEARCH_URL, params={
        "action": "query", "list": "search", "srsearch": search_term,
        "format": "json", "srlimit": 1,
    })
    r.raise_for_status()
    hits = (r.json().get("query") or {}).get("search") or []
    if not hits:
        return None
    title = hits[0]["title"]
    r2 = await client.get(_SUMMARY_URL.format(title=quote(title, safe="")))
    if r2.status_code != 200:
        return None
    js = r2.json()
    thumb = js.get("originalimage") or js.get("thumbnail")
    if not thumb or not thumb.get("source"):
        return None
    return {
        "image_url": thumb["source"],
        "page_url": ((js.get("content_urls") or {}).get("desktop") or {}).get("page"),
        "title": js.get("title"),
        "description": js.get("description"),
    }


async def fetch_company_image(query: str) -> dict | None:
    """หารูปจริงของบริษัท/สินค้าจาก Wikipedia — คืน {image_url, page_url, title} หรือ None ถ้าไม่เจอ.

    ชื่อบริษัทสั้นๆ ที่ชนกับคำธรรมดา (Apple, Target ฯลฯ) ค้นตรงๆ อาจได้บทความผิดเรื่อง — ลองค้น
    ตรงๆ ก่อน เช็ค description ว่า 'ดูเหมือนบริษัทจริง' ถ้าไม่ผ่านค่อย retry ด้วยคำค้นที่เติม
    'company' ต่อท้ายบังคับ disambiguate (ข้ามขั้นนี้ถ้าชื่อมีคำแบบ Inc./Corp./Ltd. อยู่แล้ว
    เพราะเติมซ้ำจะพาไปหน้าอื่นที่ผิดยิ่งกว่าเดิม เช่น TSMC ชื่อเต็มมี 'Company' อยู่แล้ว)."""
    if not query or not query.strip():
        return None
    cached = _load_cache(query, "company")
    if cached is not None:
        return cached.get("result")

    result = None
    try:
        async with httpx.AsyncClient(timeout=10, headers=_HEADERS) as client:
            hit = await _search_and_summarize(client, query)
            # retry ด้วยคำค้นเติม 'company' เมื่อ: (1) รอบแรกไม่เจอเลย (เช่นไปชนหน้า disambiguation
            # ที่ไม่มีรูป) หรือ (2) เจอแต่ description ไม่เหมือนบริษัท — ยกเว้นชื่อมี Inc./Corp./Ltd.
            # อยู่แล้วเพราะเติมซ้ำจะพาไปหน้าอื่นที่ผิดยิ่งกว่าเดิม (เช่น TSMC ชื่อเต็มมี 'Company' อยู่แล้ว)
            needs_retry = hit is None or not _looks_like_company(hit.get("description"))
            if needs_retry:
                has_corp_suffix = any(s in query.lower() for s in
                                      ("inc", "corp", "company", "ltd", "llc", "plc", "group", "holdings"))
                if not has_corp_suffix:
                    retry_hit = await _search_and_summarize(client, f"{query} company")
                    hit = retry_hit if (retry_hit and _looks_like_company(retry_hit.get("description"))) else None
                else:
                    hit = None if hit and not _looks_like_company(hit.get("description")) else hit
            if hit:
                result = {"image_url": hit["image_url"], "page_url": hit["page_url"], "title": hit["title"]}
    except Exception:  # noqa: BLE001 — หารูปไม่ได้ไม่ควรทำให้ทั้งธีมพัง แค่ไม่มีรูปประกอบ
        result = None

    _save_cache(query, "company", result)
    return result


async def fetch_topic_image(query: str) -> dict | None:
    """หารูปจริงประกอบ 'เรื่องราว/สินค้า/component' (ไม่ใช่ชื่อบริษัท) จาก Wikipedia — เช่น
    'smartphone chipset', 'OLED display panel', 'NVIDIA H100 GPU' เพื่อให้เห็นภาพจริงของสิ่งที่
    เนื้อหากำลังอธิบาย ไม่ใช่แค่โลโก้บริษัท. ไม่มีการเช็ค 'ดูเหมือนบริษัทจริง' แบบ fetch_company_image
    เพราะหัวข้อพวกนี้ไม่ใช่ชื่อบริษัท — คืน None เงียบๆ ถ้าหาไม่เจอ (ไม่มีรูปดีกว่ารูปผิดเรื่อง)."""
    if not query or not query.strip():
        return None
    cached = _load_cache(query, "topic")
    if cached is not None:
        return cached.get("result")

    result = None
    try:
        async with httpx.AsyncClient(timeout=10, headers=_HEADERS) as client:
            hit = await _search_and_summarize(client, query)
            if hit:
                result = {"image_url": hit["image_url"], "page_url": hit["page_url"], "title": hit["title"]}
    except Exception:  # noqa: BLE001
        result = None

    _save_cache(query, "topic", result)
    return result
