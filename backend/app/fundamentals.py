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


_YA_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
_ya_session = {"cookie": None, "crumb": None, "ts": 0.0}
_CRUMB_TTL = 1800  # 30 นาที


async def _yahoo_crumb(force: bool = False) -> tuple[str, str]:
    """ขอ cookie+crumb ของ Yahoo (cache 30 นาที) สำหรับเรียก quoteSummary.

    ลอง seed cookie หลายทาง (บาง host เช่น fc.yahoo.com อาจถูกบล็อกจาก IP ดาต้าเซ็นเตอร์).
    """
    import httpx
    now = time.time()
    if not force and _ya_session["crumb"] and now - _ya_session["ts"] < _CRUMB_TTL:
        return _ya_session["cookie"], _ya_session["crumb"]
    last_err: Exception | None = None
    for seed in ("https://finance.yahoo.com/quote/AAPL",
                 "https://finance.yahoo.com/",
                 "https://fc.yahoo.com/"):
        try:
            async with httpx.AsyncClient(headers=_YA_UA, timeout=20, follow_redirects=True) as c:
                await c.get(seed)
                crumb = (await c.get("https://query1.finance.yahoo.com/v1/test/getcrumb")).text.strip()
                cookie = "; ".join(f"{k}={v}" for k, v in c.cookies.items())
            if crumb and "<" not in crumb and len(crumb) < 40:
                _ya_session.update(cookie=cookie, crumb=crumb, ts=now)
                return cookie, crumb
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError(f"ขอ crumb จาก Yahoo ไม่สำเร็จ (IP อาจถูกจำกัด): {last_err}")


def _raw(d: dict, key: str):
    v = (d or {}).get(key)
    if isinstance(v, dict):
        return _to_float(v.get("raw"))
    return _to_float(v)


async def fetch_yahoo_fundamentals(symbol: str) -> dict:
    """ดึงปัจจัยพื้นฐานจาก Yahoo quoteSummary ผ่าน httpx (รองรับทั้งหุ้น US และไทย .BK).

    ใช้ httpx (ไม่ใช่ curl_cffi) จึงไม่ติดปัญหา path ภาษาไทย และคุม retry/cache เองได้.
    """
    import asyncio as _aio

    import httpx
    mods = "summaryProfile,price,financialData,defaultKeyStatistics,summaryDetail"
    hosts = ("https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com")
    result = None
    last_status = None
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
        for attempt in range(3):
            host = hosts[attempt % len(hosts)]
            url = f"{host}/v10/finance/quoteSummary/{symbol}?modules={mods}"
            cookie, crumb = await _yahoo_crumb(force=attempt > 0)
            r = await c.get(f"{url}&crumb={crumb}", headers={**_YA_UA, "Cookie": cookie})
            last_status = r.status_code
            if r.status_code == 200:
                result = (r.json().get("quoteSummary", {}).get("result") or [])
                break
            if r.status_code in (401, 403, 429):  # crumb เก่า/โดนจำกัด → หน่วงแล้วลองใหม่
                await _aio.sleep(0.6 * (attempt + 1))
                continue
            r.raise_for_status()
    if not result:
        raise RuntimeError(f"Yahoo ไม่ตอบข้อมูลพื้นฐานของ {symbol} (status {last_status})")
    res = result[0]
    sp, price = res.get("summaryProfile", {}), res.get("price", {})
    fd, ks, sd = res.get("financialData", {}), res.get("defaultKeyStatistics", {}), res.get("summaryDetail", {})

    market_cap = _raw(sd, "marketCap") or _raw(price, "marketCap")
    fcf = _raw(fd, "freeCashflow")
    raw_de = _raw(fd, "debtToEquity")
    dy = _raw(sd, "trailingAnnualDividendYield")
    if dy is None:
        dy = _raw(sd, "dividendYield")
    if dy is not None and dy > 1:  # กันค่าเป็นเปอร์เซ็นต์ (เช่น 0.36% มาเป็น 36)
        dy = dy / 100.0
    return {
        "symbol": symbol.upper(),
        "long_name": (price.get("longName") or price.get("shortName") or symbol.upper()),
        "sector": sp.get("sector"),
        "industry": sp.get("industry"),
        "summary": (sp.get("longBusinessSummary") or "")[:1500] or None,
        "currency": fd.get("financialCurrency"),
        "market_cap": market_cap,
        "pe": _raw(sd, "trailingPE"),
        "forward_pe": _raw(sd, "forwardPE") or _raw(ks, "forwardPE"),
        "peg": _raw(ks, "pegRatio") or _raw(ks, "trailingPegRatio"),
        "pb": _raw(ks, "priceToBook"),
        "roe": _raw(fd, "returnOnEquity"),
        "gross_margin": _raw(fd, "grossMargins"),
        "operating_margin": _raw(fd, "operatingMargins"),
        "profit_margin": _raw(fd, "profitMargins"),
        "debt_to_equity": (raw_de / 100.0) if raw_de is not None else None,
        "current_ratio": _raw(fd, "currentRatio"),
        "revenue_growth": _raw(fd, "revenueGrowth"),
        "earnings_growth": _raw(fd, "earningsGrowth"),
        "fcf": fcf,
        "fcf_yield": (fcf / market_cap) if (fcf is not None and market_cap) else None,
        "dividend_yield": dy,
        "payout_ratio": _raw(sd, "payoutRatio"),
    }


_SNAPSHOT_KEYS = (
    "symbol", "long_name", "sector", "industry", "summary", "market_cap",
    "pe", "forward_pe", "peg", "pb", "roe", "gross_margin", "operating_margin",
    "profit_margin", "debt_to_equity", "current_ratio", "revenue_growth",
    "earnings_growth", "fcf", "fcf_yield", "dividend_yield", "payout_ratio",
)


def _apply_price_ratios(snap: dict, price: float | None) -> None:
    """เติมอัตราส่วนที่ต้องใช้ราคา (P/E, P/B, PEG, fcf_yield, market_cap) จากค่างบ EDGAR + ราคาปัจจุบัน."""
    shares, eps = snap.get("shares"), snap.get("eps")
    eq, ni, fcf, div = (snap.get("total_equity"), snap.get("net_income"),
                        snap.get("fcf"), snap.get("dividends_paid"))
    if not price or not shares:
        return
    mc = price * shares
    snap["market_cap"] = mc
    if eq and eq > 0:
        snap["pb"] = mc / eq
    if ni and ni > 0:
        snap["pe"] = mc / ni
    elif eps and eps > 0:
        snap["pe"] = price / eps
    if fcf and mc:
        snap["fcf_yield"] = fcf / mc
    if div and mc:
        snap["dividend_yield"] = abs(div) / mc
    eg = snap.get("earnings_growth")
    if snap.get("pe") and eg and eg > 0:
        snap["peg"] = snap["pe"] / (eg * 100)


async def get_fundamentals(symbol: str, *, facts: dict | None = None,
                           price: float | None = None, force_refresh: bool = False) -> dict:
    """คืน snapshot ปัจจัยพื้นฐานของหุ้น (cache 12 ชม.).

    ลำดับความน่าเชื่อถือ: ฐานจาก SEC EDGAR (เสถียร) + ราคาจาก provider httpx → เติมอัตราส่วน,
    แล้ว 'เสริม' ด้วย yfinance แบบ best-effort (sector/ค่าทางการ). ถ้า yfinance โดน rate limit
    ก็ข้ามไป ไม่ทำให้ทั้งคำขอล้ม. ต้องมีอย่างน้อยหนึ่งแหล่งที่สำเร็จ.
    """
    key = (symbol or "").upper().strip()
    now = time.time()
    if not force_refresh:
        cached = _cache.get(key)
        if cached and now - cached[0] < _CACHE_TTL:
            return cached[1]

    snap: dict = {}
    if facts:
        from app.financials import latest_snapshot
        snap = latest_snapshot(facts)
        _apply_price_ratios(snap, price)

    # แหล่ง live: Yahoo ผ่าน httpx ก่อน (รองรับ US + ไทย, คุมได้), ตกไปใช้ yfinance ถ้าล้ม
    live, live_src = None, None
    try:
        live = await fetch_yahoo_fundamentals(key)
        live_src = "yahoo"
    except Exception:
        try:
            live = await asyncio.to_thread(_fetch_sync, key)
            live_src = "yfinance"
        except Exception:
            live = None
    if live:
        for k, v in live.items():
            if v is not None:
                snap[k] = v

    if not snap:
        raise RuntimeError("ดึงข้อมูลพื้นฐานไม่สำเร็จจากทุกแหล่ง (Yahoo/SEC) — ลองใหม่ภายหลัง")

    for k in _SNAPSHOT_KEYS:
        snap.setdefault(k, None)
    snap.setdefault("symbol", key)
    snap["_source"] = "+".join(x for x in [("edgar" if facts else None), live_src] if x) or "none"
    _cache[key] = (now, snap)
    return snap
