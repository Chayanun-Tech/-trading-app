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
