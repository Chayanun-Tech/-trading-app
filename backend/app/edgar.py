"""SEC EDGAR client — ดึงงบการเงินย้อนหลังลึก (companyfacts XBRL) ของหุ้น US.

ฟรี ไม่ใช้คีย์ แต่ SEC บังคับ:
- ตั้ง header `User-Agent` (ชื่อแอป + อีเมลติดต่อ) ไม่งั้นคืน 403
- จำกัด ≤10 req/วินาที (เราเรียกแค่ไม่กี่ครั้งต่อหุ้น + cache)

ใช้ httpx (ssl ของ Python จัดการ path ภาษาไทยได้ ไม่เจอปัญหา libcurl เหมือน yfinance).
cache companyfacts ลงดิสก์ (TTL 7 วัน เพราะงบออกไม่บ่อย).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

# SEC ขอให้ระบุชื่อแอป + อีเมลจริงใน User-Agent
_HEADERS = {
    "User-Agent": "ChayanunOperating-trading-app chayanun250841@gmail.com",
    "Accept-Encoding": "gzip, deflate",
}
_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

_CACHE_DIR = Path(__file__).resolve().parents[1].parent / "data" / "financials"
_FACTS_TTL = 7 * 24 * 3600       # 7 วัน
_TICKERS_TTL = 30 * 24 * 3600    # 30 วัน

_ticker_map: dict[str, str] | None = None


async def _load_ticker_map() -> dict[str, str]:
    """โหลดตาราง ticker→CIK (cache memory + disk)."""
    global _ticker_map
    if _ticker_map is not None:
        return _ticker_map
    cache = _CACHE_DIR / "company_tickers.json"
    data = None
    if cache.exists() and time.time() - cache.stat().st_mtime < _TICKERS_TTL:
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
    if data is None:
        async with httpx.AsyncClient(timeout=30, headers=_HEADERS) as client:
            res = await client.get(_TICKERS_URL)
            res.raise_for_status()
            data = res.json()
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data), encoding="utf-8")
    m: dict[str, str] = {}
    for row in data.values():
        m[str(row["ticker"]).upper()] = str(row["cik_str"]).zfill(10)
    _ticker_map = m
    return m


async def get_cik(symbol: str) -> str:
    m = await _load_ticker_map()
    cik = m.get((symbol or "").upper().strip())
    if not cik:
        raise ValueError(f"ไม่พบ CIK ของ {symbol} ใน SEC (อาจไม่ใช่หุ้นจดทะเบียนในสหรัฐ)")
    return cik


async def get_company_facts(symbol: str, *, force_refresh: bool = False) -> dict:
    """ดึง companyfacts (XBRL ทั้งหมด) ของหุ้น — cache ดิสก์ TTL 7 วัน."""
    cik = await get_cik(symbol)
    cache = _CACHE_DIR / f"facts_{cik}.json"
    if not force_refresh and cache.exists() and time.time() - cache.stat().st_mtime < _FACTS_TTL:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    async with httpx.AsyncClient(timeout=60, headers=_HEADERS) as client:
        res = await client.get(_FACTS_URL.format(cik=cik))
        res.raise_for_status()
        data = res.json()
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data), encoding="utf-8")
    return data


_SUBS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_FRAME_URL = "https://data.sec.gov/api/xbrl/frames/us-gaap/{concept}/{unit}/{period}.json"
_SUBS_TTL = 12 * 3600       # ประวัติการยื่นเปลี่ยนบ่อยกว่า → 12 ชม.
_FRAME_TTL = 7 * 24 * 3600  # frame ทั้งตลาดใหญ่ + เปลี่ยนช้า → 7 วัน


async def _cached_json(url: str, cache_name: str, ttl: int, timeout: float = 60) -> dict:
    cache = _CACHE_DIR / cache_name
    if cache.exists() and time.time() - cache.stat().st_mtime < ttl:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    async with httpx.AsyncClient(timeout=timeout, headers=_HEADERS) as client:
        res = await client.get(url)
        res.raise_for_status()
        data = res.json()
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data), encoding="utf-8")
    return data


async def get_submissions(symbol: str) -> dict:
    """ประวัติการยื่นเอกสารทั้งหมดของบริษัท (data.sec.gov/submissions)."""
    cik = await get_cik(symbol)
    return await _cached_json(_SUBS_URL.format(cik=cik), f"subs_{cik}.json", _SUBS_TTL)


def _doc_url(cik: str, accession: str, primary_doc: str) -> str | None:
    if not primary_doc:
        return None
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{primary_doc}"


async def recent_filings(symbol: str, limit: int = 25) -> list[dict]:
    """รายการยื่นล่าสุด (form/วันที่/รหัสเหตุการณ์ 8-K/ลิงก์เอกสารจริง)."""
    data = await get_submissions(symbol)
    cik = str(data.get("cik") or "").zfill(10)
    rec = data.get("filings", {}).get("recent", {})
    forms = rec.get("form", [])
    out: list[dict] = []
    for i in range(min(limit, len(forms))):
        out.append({
            "form": forms[i],
            "date": rec.get("filingDate", [None] * len(forms))[i],
            "items": (rec.get("items") or [""] * len(forms))[i],
            "doc": (rec.get("primaryDocument") or [""] * len(forms))[i],
            "url": _doc_url(cik, rec.get("accessionNumber", [""] * len(forms))[i],
                            (rec.get("primaryDocument") or [""] * len(forms))[i]),
        })
    return out


async def latest_filing_url(symbol: str, form: str = "10-K") -> tuple[str | None, str | None]:
    """คืน (URL เอกสารหลัก, วันที่ยื่น) ของเอกสารฟอร์มล่าสุด."""
    data = await get_submissions(symbol)
    cik = str(data.get("cik") or "").zfill(10)
    rec = data.get("filings", {}).get("recent", {})
    forms = rec.get("form", [])
    for i, f in enumerate(forms):
        if f == form:
            url = _doc_url(cik, rec["accessionNumber"][i], rec.get("primaryDocument", [""])[i])
            return url, rec.get("filingDate", [None])[i]
    return None, None


async def fetch_document_text(url: str, max_chars: int = 4_000_000) -> str:
    """ดึงเอกสาร (HTML) จาก EDGAR Archives แล้วถอดเป็นข้อความล้วน."""
    async with httpx.AsyncClient(timeout=60, headers=_HEADERS, follow_redirects=True) as client:
        res = await client.get(url)
        res.raise_for_status()
        html = res.text[:max_chars]
    try:
        from bs4 import BeautifulSoup
        text = BeautifulSoup(html, "html.parser").get_text(" ")
    except Exception:  # noqa: BLE001
        import re
        text = re.sub(r"<[^>]+>", " ", html)
    import re
    return re.sub(r"\s+", " ", text).strip()


async def get_frame(concept: str, unit: str, period: str) -> dict:
    """ค่าหนึ่ง concept ของ 'ทุกบริษัท' ในงวดเดียว (xbrl/frames) — ใช้จัดอันดับเทียบตลาด."""
    return await _cached_json(_FRAME_URL.format(concept=concept, unit=unit, period=period),
                              f"frame_{concept}_{unit}_{period}.json", _FRAME_TTL)


def _section(text: str, start_re: str, end_re: str, cap: int = 8000) -> str | None:
    """ดึง section จริงจาก 10-K โดยเลือก 'ช่วง start→end ที่กว้างที่สุด' (real section คือเนื้อ
    ที่ยาวสุดระหว่างหัวข้อ; สารบัญ=ช่วงสั้น, การอ้างอิง=ไม่มี end ตามมา). กันสารบัญด้วยความถี่ 'Item N'.
    """
    import re
    low = text.lower()
    best, best_span = None, 0
    for m in re.finditer(start_re, low):
        si = m.start()
        if len(re.findall(r"item\s+\d", low[si:si + 1500])) >= 5:  # หัวสารบัญ
            continue
        em = re.search(end_re, low[si + 50:])
        end = si + 50 + em.start() if em else si + cap
        span = end - si
        if span > best_span:
            best, best_span = text[si:min(end, si + cap)], span
    return best.strip() if best and len(best.split()) >= 80 else None


def extract_filing_sections(text: str) -> dict:
    """แยกส่วน Risk Factors (Item 1A) และ MD&A (Item 7) จากข้อความ 10-K."""
    risk = _section(text, r"item\s*1a[\.\s\):]{0,4}risk\s+factors", r"item\s*1b[\.\s\):]")
    mda = _section(text, r"item\s*7[\.\s\):]{1,4}management.{0,6}s discussion",
                   r"item\s*7a[\.\s\):]|item\s*8[\.\s\):]")
    return {"risk_factors": risk, "mda": mda}


async def get_10k_context(symbol: str) -> dict | None:
    """ดึง+แคชส่วน Risk Factors/MD&A จาก 10-K ล่าสุด (ไว้ป้อน AI). คืน None ถ้าไม่มี 10-K."""
    url, date = await latest_filing_url(symbol, "10-K")
    if not url:
        return None
    cik = (await get_cik(symbol))
    cache = _CACHE_DIR / f"tenk_{cik}.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < _FRAME_TTL:
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
            if cached.get("url") == url:
                return cached
        except (OSError, json.JSONDecodeError):
            pass
    text = await fetch_document_text(url)
    sections = extract_filing_sections(text)
    out = {"url": url, "filing_date": date, **sections}
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out
