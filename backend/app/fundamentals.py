"""ดึงข้อมูลปัจจัยพื้นฐานหุ้น (สาย VI) ผ่าน yfinance.

yfinance เป็น lib แบบ sync + ช้า + โดน rate-limit ได้ จึง:
- เรียกใน asyncio.to_thread เสมอ (ไม่บล็อก event loop)
- cache ผลไว้ ~12 ชม. เพราะพื้นฐานเปลี่ยนช้า (ไตรมาสละครั้ง)
- ทุกค่าที่ขาด/ดึงไม่ได้ คืนเป็น None — ผู้ประเมิน (value_schools) ต้องทนค่าว่างได้
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

_CACHE_TTL = 12 * 3600  # 12 ชั่วโมง
_cache: dict[str, tuple[float, dict]] = {}

# snapshot ออฟไลน์ (commit ลง repo + ฝังใน Docker image) — ใช้บนเว็ปที่ดึง Yahoo หุ้นไทยไม่ได้
_OFFLINE_PATH = Path(__file__).resolve().parent / "offline_fundamentals.json"


def load_offline() -> dict:
    try:
        return json.loads(_OFFLINE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def get_offline(symbol: str) -> dict | None:
    return load_offline().get((symbol or "").upper().strip())


def save_offline(symbol: str, snap: dict) -> None:
    data = load_offline()
    entry = {k: v for k, v in snap.items() if k != "_source"}
    entry["fetched_at"] = int(time.time())
    data[(symbol or "").upper().strip()] = entry
    _OFFLINE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True),
                             encoding="utf-8")


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


async def fetch_fmp_fundamentals(symbol: str) -> dict:
    """ดึงปัจจัยพื้นฐานจาก Financial Modeling Prep (ต้องตั้ง FMP_API_KEY).

    FMP เป็น API จริง ใช้ได้จาก IP ดาต้าเซ็นเตอร์ (ต่างจาก Yahoo ที่บล็อก) — ใช้เป็นแหล่ง
    หลักของหุ้นไทย/ต่างประเทศบนเว็ป. หมายเหตุ: free tier อาจไม่ครอบคลุมหุ้นไทยทุกตัว.
    """
    from app.config import get_settings
    key = get_settings().fmp_api_key
    if not key:
        raise RuntimeError("ยังไม่ได้ตั้ง FMP_API_KEY")
    import httpx
    base = "https://financialmodelingprep.com/stable"

    async def _get(c, path: str) -> dict:
        sep = "&" if "?" in path else "?"
        r = await c.get(f"{base}/{path}{sep}apikey={key}")
        if r.status_code == 402:  # ไม่อยู่ในแพ็ก (เช่น หุ้นไทยในแพ็กฟรี) → ข้ามอย่างนุ่มนวล
            return {}
        r.raise_for_status()
        d = r.json()
        if isinstance(d, dict) and (d.get("Error Message") or d.get("message")):
            return {}
        return d[0] if isinstance(d, list) and d else (d if isinstance(d, dict) else {})

    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers=_YA_UA) as c:
        prof = await _get(c, f"profile?symbol={symbol}")
        ratios = await _get(c, f"ratios-ttm?symbol={symbol}")
        km = await _get(c, f"key-metrics-ttm?symbol={symbol}")
        growth = await _get(c, f"financial-growth?symbol={symbol}&limit=1")
    if not prof and not ratios:
        raise RuntimeError(f"FMP ไม่มีข้อมูลของ {symbol} (อาจไม่รองรับในแพ็กฟรี)")

    merged = {**growth, **km, **ratios, **prof}  # prof ทับท้าย (มี marketCap/price/sector)

    def pick(*keys, src=None):
        d = src if src is not None else merged
        for k in keys:
            if d.get(k) is not None:
                return _to_float(d.get(k))
        return None

    price, last_div = _to_float(prof.get("price")), _to_float(prof.get("lastDividend"))
    dy = pick("dividendYieldTTM")
    if dy is None and last_div and price:  # หุ้นไทยแพ็กฟรี: คำนวณจาก profile
        dy = last_div / price
    if dy is not None and dy > 1:
        dy = dy / 100.0
    return {
        "symbol": symbol.upper(),
        "long_name": prof.get("companyName") or symbol.upper(),
        "sector": prof.get("sector"),
        "industry": prof.get("industry"),
        "summary": (prof.get("description") or "")[:1500] or None,
        "currency": prof.get("currency"),
        "market_cap": pick("marketCap"),
        "pe": pick("priceToEarningsRatioTTM"),
        "forward_pe": None,
        "peg": pick("priceToEarningsGrowthRatioTTM"),
        "pb": pick("priceToBookRatioTTM"),
        "roe": pick("returnOnEquityTTM"),
        "gross_margin": pick("grossProfitMarginTTM"),
        "operating_margin": pick("operatingProfitMarginTTM"),
        "profit_margin": pick("netProfitMarginTTM"),
        "debt_to_equity": pick("debtToEquityRatioTTM"),
        "current_ratio": pick("currentRatioTTM"),
        "revenue_growth": pick("revenueGrowth", src=growth),
        "earnings_growth": pick("epsgrowth", "netIncomeGrowth", src=growth),
        "fcf": None,
        "fcf_yield": pick("freeCashFlowYieldTTM"),
        "dividend_yield": dy,
        "payout_ratio": pick("dividendPayoutRatioTTM"),
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

    def _merge(dst: dict, src: dict) -> None:
        for k, v in (src or {}).items():
            if v is not None and k != "_source":
                dst[k] = v

    def _has_quant(d: dict) -> bool:
        return any(d.get(k) is not None for k in ("roe", "pe", "gross_margin", "operating_margin"))

    from app.config import get_settings
    has_fmp = bool(get_settings().fmp_api_key)
    sources: list[str] = []

    snap: dict = {}
    if facts:  # หุ้น US: EDGAR เป็นฐานเต็ม (เสถียร ไม่ต้องพึ่ง Yahoo)
        from app.financials import latest_snapshot
        snap = latest_snapshot(facts)
        _apply_price_ratios(snap, price)
        if _has_quant(snap):
            sources.append("edgar")

    # 1) สดเต็ม: Yahoo → yfinance (ใช้ได้ในเครื่อง; บนเว็ปหุ้นไทยจะล้ม)
    if not _has_quant(snap):
        for name, fn in (("yahoo", fetch_yahoo_fundamentals),
                         ("yfinance", lambda s: asyncio.to_thread(_fetch_sync, s))):
            try:
                live = await fn(key)
                if live:
                    _merge(snap, live)
                    sources.append(name)
                    if _has_quant(snap):
                        break
            except Exception:
                continue

    # 2) snapshot ออฟไลน์ (เต็มแต่ลงวันที่ — สำหรับเว็ปที่ดึงสดหุ้นไทยไม่ได้)
    if not _has_quant(snap):
        off = get_offline(key)
        if off:
            _merge(snap, off)
            snap["fetched_at"] = off.get("fetched_at")
            sources.append("offline")

    # 3) FMP (หุ้นไทยแพ็กฟรี = profile บางส่วน / US sector) — ทางเลือกท้ายสุด
    if not _has_quant(snap) and has_fmp:
        try:
            _merge(snap, await fetch_fmp_fundamentals(key))
            sources.append("fmp")
        except Exception:
            pass

    if not snap:
        raise RuntimeError(
            "ยังไม่มีข้อมูลพื้นฐานของหุ้นนี้บนเว็ป — มักเป็นหุ้นไทยหรือ ADR ต่างชาติ "
            "(งบไม่อยู่ใน SEC แบบ us-gaap และเว็ปดึง Yahoo ไม่ได้). "
            "วิธีแก้: เปิดแอปในเครื่อง พิมพ์สัญลักษณ์นี้ แล้วกดปุ่ม '🔄 อัปเดตขึ้นเว็ป' เพื่อบันทึก snapshot")

    for k in _SNAPSHOT_KEYS:
        snap.setdefault(k, None)
    snap.setdefault("symbol", key)
    snap["_source"] = "+".join(sources) or "none"
    _cache[key] = (now, snap)
    return snap


async def update_offline(symbol: str) -> dict:
    """ดึงข้อมูลสด (Yahoo→yfinance) แล้วเซฟลง snapshot ออฟไลน์. ใช้ได้เฉพาะที่ Yahoo เข้าถึงได้
    (รันในเครื่อง). บนเว็ป (Yahoo บล็อก) จะ throw — ให้ route แจ้งผู้ใช้."""
    key = (symbol or "").upper().strip()
    try:
        snap = await fetch_yahoo_fundamentals(key)
    except Exception:
        snap = await asyncio.to_thread(_fetch_sync, key)
    save_offline(key, snap)
    _cache.pop(key, None)  # ล้าง cache เพื่อให้รอบหน้าอ่านค่าใหม่
    return snap
