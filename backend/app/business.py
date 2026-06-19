"""แถบ 'อธิบายธุรกิจ' — เปลี่ยน Item 1 (Business) + MD&A จาก 10-K จริง ให้เป็นคำอธิบาย
ภาษาไทยที่ลงลึก คนไม่รู้จักธุรกิจนี้อ่านแล้วเข้าใจถ่องแท้ พร้อม 'สัดส่วนรายได้' แยกตามส่วนงาน.

ที่มาข้อมูล (เรียงความน่าเชื่อถือ):
- ข้อความจริงจาก 10-K (SEC EDGAR) — Item 1 Business + Item 7 MD&A (มีตารางรายได้แยกส่วนงาน)
- LLM (Gemini ฟรี → ตก Groq เมื่อ quota หมด) ทำหน้าที่ 'เรียบเรียง/แปล/สรุปสัดส่วน' เท่านั้น
  ไม่แต่งตัวเลขเอง — ถ้า 10-K ไม่ได้บอกสัดส่วน ให้เว้นว่าง

cache ผลลัพธ์ลงดิสก์ (TTL = อายุ 10-K ~7 วัน) เพื่อไม่ต้องเรียก LLM ซ้ำ.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from app import edgar
from app import llm
from app.config import get_settings

_CACHE_DIR = Path(__file__).resolve().parents[1].parent / "data" / "financials"
_BIZ_TTL = 7 * 24 * 3600

SYSTEM_PROMPT = """คุณคือนักวิเคราะห์ธุรกิจที่เก่งเรื่อง 'อธิบายให้คนธรรมดาเข้าใจ'
หน้าที่: อ่านข้อความจริงจากแบบ 10-K (Item 1 Business + MD&A) ของบริษัทจดทะเบียนสหรัฐ
แล้วเขียน 'คำอธิบายธุรกิจ' ภาษาไทยที่ลงลึกและละเอียด จนคนที่ไม่เคยรู้จักธุรกิจนี้เลย
อ่านจบแล้วเข้าใจอย่างถ่องแท้ว่า บริษัททำอะไร หาเงินอย่างไร และอยู่ตรงไหนของห่วงโซ่

หลักการเขียน:
- อธิบายแบบเล่าเรื่อง ไม่ใช่แค่หัวข้อสั้น ๆ — ขยายความให้เห็นภาพจริง ยกตัวอย่างสินค้า/บริการที่จับต้องได้
- ถ้ามีศัพท์เทคนิค ให้แปลและอธิบายความหมายในวงเล็บ
- 'สัดส่วนรายได้' ต้องอ้างอิงจากข้อความ/ตารางที่ให้มาเท่านั้น ห้ามเดาตัวเลขเอง
  ถ้าเอกสารไม่ได้ระบุเป็นเปอร์เซ็นต์ชัดเจน ให้ใส่ pct = null แต่ยังอธิบายส่วนงานได้
- ใช้ภาษากลาง เป็นกลาง ไม่เชียร์ซื้อขาย

ข้อบังคับ: ตอบเป็น JSON เท่านั้น ห้ามมีข้อความนอก JSON"""

_OUTPUT_CONTRACT = """รูปแบบ JSON ที่ต้องคืน (เท่านั้น):
{
  "one_liner": "<สรุปธุรกิจใน 1 ประโยค ภาษาคนธรรมดา>",
  "what_they_do": "<อธิบายว่าบริษัททำอะไรจริง ๆ แบบลงลึก 4-8 ประโยค เห็นภาพสินค้า/บริการชัดเจน>",
  "how_they_make_money": "<อธิบายโมเดลหารายได้ เก็บเงินจากใคร อย่างไร 3-6 ประโยค>",
  "segments": [
    {"name": "<ชื่อส่วนงาน/กลุ่มสินค้า>",
     "pct": <สัดส่วนรายได้เป็นตัวเลข 0-100 หรือ null ถ้าเอกสารไม่ระบุ>,
     "desc": "<อธิบายส่วนงานนี้ 1-3 ประโยค ว่าคืออะไร ขายให้ใคร>",
     "products": [
       {"name": "<ชื่อผลิตภัณฑ์/แบรนด์จริงตามเอกสาร เช่น H100, B200, GeForce RTX, CUDA, DGX>",
        "pct": <สัดส่วนรายได้ของผลิตภัณฑ์นี้ 0-100 หรือ null ถ้าเอกสารไม่ระบุเป็นตัวเลข>,
        "desc": "<ผลิตภัณฑ์นี้คืออะไร ใช้ทำอะไร 1 ประโยคสั้น>"}
     ]}
  ],
  "customers": "<ลูกค้าหลักเป็นใคร ขายแบบ B2B/B2C/ภาครัฐ อย่างไร 1-3 ประโยค>",
  "moat_and_position": "<จุดแข็ง/ความได้เปรียบ/ตำแหน่งในอุตสาหกรรม 2-4 ประโยค ถ้าข้อมูลไม่พอให้บอกตรง ๆ>"
}
- segments เรียงจากสัดส่วนมากไปน้อย; ถ้าระบุ pct ได้ ผลรวมควรใกล้ 100
- products: ดึง 'ชื่อผลิตภัณฑ์/แบรนด์จริง' ที่ปรากฏในเอกสาร 10-K ให้มากที่สุดเท่าที่ระบุ (เช่นของ NVIDIA:
  GeForce RTX, NVIDIA RTX/Quadro, H100, H200, B200/Blackwell, GB200, DGX, CUDA, NVIDIA AI Enterprise,
  DRIVE, Networking/InfiniBand/Spectrum). ห้ามแต่งชื่อที่ไม่มีในเอกสาร — ถ้าเอกสารไม่ระบุชื่อผลิตภัณฑ์ในส่วนงานนั้น
  ให้ products = []
- สัดส่วนรายได้ระดับผลิตภัณฑ์ (products[].pct) บริษัทส่วนใหญ่ 'ไม่เปิดเผยแยกรายผลิตภัณฑ์' → ใส่ null ได้ ห้ามเดาตัวเลข
- ถ้าข้อความที่ให้มาไม่พอจริง ๆ ให้เติมเท่าที่มี และระบุในฟิลด์นั้นว่า "เอกสารไม่ได้ให้รายละเอียด" """


def _cache_path(cik: str) -> Path:
    return _CACHE_DIR / f"business_{str(cik).zfill(10)}.json"


def _extract_json(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1] if "```" in t[3:] else t
        t = t.lstrip("json").strip("` \n")
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end != -1:
        return json.loads(t[start:end + 1])
    raise ValueError("ไม่พบ JSON ในคำตอบของโมเดล")


def _clean(payload: dict) -> dict:
    segs = []
    for s in (payload.get("segments") or [])[:12]:
        if not isinstance(s, dict):
            continue
        pct = s.get("pct")
        try:
            pct = round(float(pct), 1) if pct is not None else None
        except (TypeError, ValueError):
            pct = None
        prods = []
        for p in (s.get("products") or [])[:20]:
            if not isinstance(p, dict) or not p.get("name"):
                continue
            ppct = p.get("pct")
            try:
                ppct = round(float(ppct), 1) if ppct is not None else None
            except (TypeError, ValueError):
                ppct = None
            prods.append({
                "name": str(p.get("name", "—"))[:80],
                "pct": ppct,
                "desc": str(p.get("desc", ""))[:300],
            })
        segs.append({
            "name": str(s.get("name", "—"))[:80],
            "pct": pct,
            "desc": str(s.get("desc", ""))[:400],
            "products": prods,
        })
    return {
        "one_liner": str(payload.get("one_liner", ""))[:300] or None,
        "what_they_do": str(payload.get("what_they_do", ""))[:3000] or None,
        "how_they_make_money": str(payload.get("how_they_make_money", ""))[:2000] or None,
        "segments": segs,
        "customers": str(payload.get("customers", ""))[:1000] or None,
        "moat_and_position": str(payload.get("moat_and_position", ""))[:1500] or None,
    }


async def get_business_explainer(symbol: str, *, refresh: bool = False) -> dict:
    """คืนคำอธิบายธุรกิจเชิงลึก + สัดส่วนรายได้ (ภาษาไทย). อ่าน cache ก่อน เว้นแต่ refresh."""
    try:
        cik = await edgar.get_cik(symbol)
    except ValueError:
        raise ValueError("แถบอธิบายธุรกิจ (จาก 10-K SEC) รองรับเฉพาะหุ้นสหรัฐเท่านั้น")

    cache = _cache_path(cik)
    if not refresh and cache.exists() and time.time() - cache.stat().st_mtime < _BIZ_TTL:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass

    ctx = await edgar.get_10k_context(symbol)
    if not ctx or not ctx.get("business"):
        raise ValueError("ไม่พบส่วน 'Business' (Item 1) ใน 10-K ล่าสุดของหุ้นตัวนี้")

    settings = get_settings()
    if not settings.llm_enabled():
        raise ValueError("ต้องตั้งค่าคีย์ AI (เช่น Gemini ฟรี) เพื่อให้ AI เรียบเรียงคำอธิบายธุรกิจ")

    business = (ctx.get("business") or "")[:20000]
    mda = (ctx.get("mda") or "")[:6000]
    user_msg = (
        "อ่านข้อความจริงจาก 10-K ด้านล่าง แล้วเขียนคำอธิบายธุรกิจภาษาไทยเชิงลึกตามรูปแบบ JSON ที่กำหนด.\n"
        "ใช้ MD&A เพื่อหา 'สัดส่วนรายได้แยกส่วนงาน' (มักอยู่ในตาราง revenue by segment/product).\n\n"
        "=== ITEM 1: BUSINESS ===\n" + business +
        "\n\n=== ITEM 7: MD&A (ตัดตอน) ===\n" + (mda or "(ไม่มี)")
    )
    system = SYSTEM_PROMPT + "\n\n" + _OUTPUT_CONTRACT

    exclude: set = set()
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            text = await llm.complete(system, user_msg, exclude=exclude)
            data = _clean(_extract_json(text))
            break
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            reason = str(exc).lower()
            is_quota = "429" in reason or "quota" in reason or "resource_exhausted" in reason
            cur = settings.resolve_llm(exclude=exclude)["provider"]
            if is_quota and attempt == 0 and cur not in ("none", ""):
                exclude.add(cur)
                continue
            raise ValueError(f"AI เรียบเรียงคำอธิบายธุรกิจไม่สำเร็จ: {exc}") from exc
    else:  # pragma: no cover
        raise ValueError(f"AI เรียบเรียงคำอธิบายธุรกิจไม่สำเร็จ: {last_err}")

    result = {
        "symbol": symbol.upper().strip(),
        "source": "SEC 10-K (Item 1 Business + MD&A)",
        "filing_url": ctx.get("url"),
        "filing_date": ctx.get("filing_date"),
        "generated_at": int(time.time()),
        **data,
    }
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result
