"""ดึงข้อมูลปัจจัยพื้นฐานหุ้น (สาย VI) ผ่าน yfinance.

yfinance เป็น lib แบบ sync + ช้า + โดน rate-limit ได้ จึง:
- เรียกใน asyncio.to_thread เสมอ (ไม่บล็อก event loop)
- cache ผลไว้ ~12 ชม. เพราะพื้นฐานเปลี่ยนช้า (ไตรมาสละครั้ง)
- ทุกค่าที่ขาด/ดึงไม่ได้ คืนเป็น None — ผู้ประเมิน (value_schools) ต้องทนค่าว่างได้
"""
from __future__ import annotations

import asyncio
import os
import time

_CACHE_TTL = 12 * 3600  # 12 ชั่วโมง
_cache: dict[str, tuple[float, dict]] = {}


def _ensure_ascii_ca_bundle() -> None:
    """yfinance 1.x ใช้ curl_cffi เบื้องหลัง ซึ่ง libcurl โหลด CA cert จาก path ที่มีอักขระ
    ไม่ใช่ ASCII ไม่ได้ (ใช้ ANSI file API). โปรเจกต์นี้อยู่ใต้ path ภาษาไทย → ก็อป cacert.pem
    ไป temp ที่เป็น ASCII แล้วชี้ env ให้ curl/ssl ใช้แทน. ทำครั้งเดียวตอน import."""
    try:
        import certifi
        ca = certifi.where()
        if ca.isascii() and os.path.exists(ca):
            return  # path เป็น ASCII อยู่แล้ว ไม่ต้องทำอะไร
        import shutil
        import tempfile
        dst = os.path.join(tempfile.gettempdir(), "trading_app_cacert.pem")
        if not dst.isascii():
            return  # แม้แต่ temp ก็ไม่ ASCII — ปล่อยให้ใช้ค่า default
        if not os.path.exists(dst):
            shutil.copy(ca, dst)
        os.environ.setdefault("CURL_CA_BUNDLE", dst)
        os.environ.setdefault("SSL_CERT_FILE", dst)
    except Exception:
        pass


_ensure_ascii_ca_bundle()

# สัญลักษณ์ที่ไม่ใช่หุ้นราย ตัว (VI ใช้ไม่ได้): คริปโต -USD, forex =X, ฟิวเจอร์ =F, ดัชนี ^, Bitkub _THB
_NON_EQUITY_SUFFIX = ("-USD", "=X", "=F", "_THB")


def is_equity_symbol(symbol: str) -> bool:
    """True ถ้าน่าจะเป็นหุ้นรายตัว (รองรับ VI). ปฏิเสธคริปโต/forex/ฟิวเจอร์/ดัชนี."""
    s = (symbol or "").upper().strip()
    if not s or s.startswith("^"):
        return False
    return not any(s.endswith(suf) for suf in _NON_EQUITY_SUFFIX)


def _to_float(value) -> float | None:
    """แปลงเป็น float; คืน None ถ้าเป็น None/NaN/แปลงไม่ได้."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def _fetch_sync(symbol: str) -> dict:
    """เรียก yfinance (sync) แล้ว normalize เป็น dict เมตริกมาตรฐาน."""
    import yfinance as yf

    ticker = yf.Ticker(symbol)
    info = ticker.get_info() or {}

    market_cap = _to_float(info.get("marketCap"))
    fcf = _to_float(info.get("freeCashflow"))
    fcf_yield = (fcf / market_cap) if (fcf is not None and market_cap) else None

    # yfinance ให้ debtToEquity เป็น "เปอร์เซ็นต์" (เช่น 150.0 = 1.5 เท่า) → หารด้วย 100
    raw_de = _to_float(info.get("debtToEquity"))
    debt_to_equity = (raw_de / 100.0) if raw_de is not None else None

    # yfinance 1.x ให้ dividendYield เป็น "เปอร์เซ็นต์" แล้ว (เช่น 0.36 = 0.36%) ต่างจาก margin/ROE
    # ที่เป็นเศษส่วน → หารด้วย 100 ให้เป็นเศษส่วนเหมือนตัวอื่น (ระบบคูณ 100 ตอนแสดงผลเอง)
    raw_dy = _to_float(info.get("dividendYield"))
    dividend_yield = (raw_dy / 100.0) if raw_dy is not None else None

    return {
        "symbol": symbol.upper(),
        "long_name": info.get("longName") or info.get("shortName") or symbol.upper(),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "summary": (info.get("longBusinessSummary") or "")[:1500] or None,
        "market_cap": market_cap,
        "currency": info.get("currency"),
        # มูลค่า (valuation)
        "pe": _to_float(info.get("trailingPE")),
        "forward_pe": _to_float(info.get("forwardPE")),
        "peg": _to_float(info.get("trailingPegRatio") or info.get("pegRatio")),
        "pb": _to_float(info.get("priceToBook")),
        # คุณภาพ/กำไร (profitability)
        "roe": _to_float(info.get("returnOnEquity")),
        "gross_margin": _to_float(info.get("grossMargins")),
        "operating_margin": _to_float(info.get("operatingMargins")),
        "profit_margin": _to_float(info.get("profitMargins")),
        # สุขภาพการเงิน (financial health)
        "debt_to_equity": debt_to_equity,
        "current_ratio": _to_float(info.get("currentRatio")),
        # การเติบโต (growth)
        "revenue_growth": _to_float(info.get("revenueGrowth")),
        "earnings_growth": _to_float(info.get("earningsGrowth")),
        # กระแสเงินสด (cash flow)
        "fcf": fcf,
        "fcf_yield": fcf_yield,
        # ปันผล (dividend)
        "dividend_yield": dividend_yield,
        "payout_ratio": _to_float(info.get("payoutRatio")),
    }


async def get_fundamentals(symbol: str, *, force_refresh: bool = False) -> dict:
    """คืน snapshot ปัจจัยพื้นฐานของหุ้น (cache 12 ชม.). เรียก yfinance ใน thread."""
    key = (symbol or "").upper().strip()
    now = time.time()
    if not force_refresh:
        cached = _cache.get(key)
        if cached and now - cached[0] < _CACHE_TTL:
            return cached[1]
    data = await asyncio.to_thread(_fetch_sync, key)
    _cache[key] = (now, data)
    return data
