"""Small-cap discovery scanner for finding companies worth deeper research.

The scanner deliberately separates discovery from conviction:
1. Nasdaq universe data provides size, price, and liquidity filters.
2. Existing fundamental providers enrich only a short pre-filtered list.
3. A transparent score ranks growth, quality, balance-sheet strength,
   valuation, and remaining size runway while exposing red flags.
"""
from __future__ import annotations

import asyncio
import math
import re
import time
from typing import Awaitable, Callable

from app.fundamentals import fetch_yahoo_fundamentals, get_fundamentals

NASDAQ_SCREENER_URL = "https://api.nasdaq.com/api/screener/stocks"
NASDAQ_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.nasdaq.com/market-activity/stocks/screener",
}

_UNIVERSE_TTL = 30 * 60
_universe_cache: tuple[float, list[dict]] = (0.0, [])
_EXCLUDED_NAME = re.compile(
    r"\b(warrant|warrants|unit|units|right|rights|preferred|depositary share|"
    r"senior note|subordinated note|acquisition corp|blank check)\b",
    re.IGNORECASE,
)
_EXCLUDED_SYMBOL = re.compile(r"(\^|/|\.U$|\.W$|WS$|WT$|R$)")


def _number(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace("$", "").replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _band(value: float | None, bands: list[tuple[float, float]], default: float = 0.0) -> float:
    if value is None:
        return default
    for threshold, score in bands:
        if value >= threshold:
            return score
    return default


async def fetch_nasdaq_universe(force_refresh: bool = False) -> list[dict]:
    import httpx

    global _universe_cache
    now = time.time()
    if not force_refresh and _universe_cache[1] and now - _universe_cache[0] < _UNIVERSE_TTL:
        return _universe_cache[1]

    params = {"tableonly": "true", "limit": 10000, "offset": 0, "download": "true"}
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(35.0, connect=10.0),
        headers=NASDAQ_HEADERS,
        follow_redirects=True,
    ) as client:
        response = await client.get(NASDAQ_SCREENER_URL, params=params)
        response.raise_for_status()
        payload = response.json()
    data = (payload or {}).get("data") or {}
    # Nasdaq currently returns rows directly under data; keep the older
    # data.table.rows shape as a compatibility fallback.
    rows = data.get("rows") or (data.get("table") or {}).get("rows") or []
    _universe_cache = (now, rows)
    return rows


def prefilter_universe(
    rows: list[dict],
    *,
    min_market_cap: float,
    max_market_cap: float,
    min_price: float,
    min_dollar_volume: float,
    sector: str | None = None,
    us_only: bool = True,
) -> list[dict]:
    candidates: list[dict] = []
    sector_key = (sector or "").strip().lower()
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        name = str(row.get("name") or "").strip()
        market_cap = _number(row.get("marketCap"))
        price = _number(row.get("lastsale"))
        volume = _number(row.get("volume"))
        row_sector = str(row.get("sector") or "").strip()
        country = str(row.get("country") or "").strip()
        if not symbol or market_cap is None or price is None or volume is None:
            continue
        if _EXCLUDED_NAME.search(name) or _EXCLUDED_SYMBOL.search(symbol):
            continue
        if us_only and country not in {"United States", ""}:
            continue
        if sector_key and row_sector.lower() != sector_key:
            continue
        dollar_volume = price * volume
        if not (min_market_cap <= market_cap <= max_market_cap):
            continue
        if price < min_price or dollar_volume < min_dollar_volume:
            continue
        candidates.append({
            "symbol": symbol,
            "name": name,
            "price": price,
            "market_cap": market_cap,
            "volume": volume,
            "dollar_volume": dollar_volume,
            "sector": row_sector or None,
            "industry": row.get("industry") or None,
            "country": country or None,
            "ipo_year": row.get("ipoyear") or None,
            "nasdaq_url": f"https://www.nasdaq.com{row.get('url')}" if row.get("url") else None,
        })

    # Enrich liquid companies across the size range first. This avoids spending
    # slow fundamental calls on securities that are practically untradeable.
    return sorted(
        candidates,
        key=lambda x: (
            x["dollar_volume"],
            1 - abs((x["market_cap"] - 750_000_000) / max(max_market_cap, 1)),
        ),
        reverse=True,
    )


def score_candidate(base: dict, fundamentals: dict) -> dict:
    revenue_growth = _number(fundamentals.get("revenue_growth"))
    earnings_growth = _number(fundamentals.get("earnings_growth"))
    gross_margin = _number(fundamentals.get("gross_margin"))
    operating_margin = _number(fundamentals.get("operating_margin"))
    profit_margin = _number(fundamentals.get("profit_margin"))
    roe = _number(fundamentals.get("roe"))
    debt_to_equity = _number(fundamentals.get("debt_to_equity"))
    current_ratio = _number(fundamentals.get("current_ratio"))
    fcf = _number(fundamentals.get("fcf"))
    fcf_yield = _number(fundamentals.get("fcf_yield"))
    pe = _number(fundamentals.get("pe"))
    peg = _number(fundamentals.get("peg"))
    market_cap = _number(fundamentals.get("market_cap")) or base["market_cap"]

    growth = (
        _band(revenue_growth, [(0.30, 22), (0.20, 18), (0.12, 13), (0.05, 7), (0, 3)])
        + _band(earnings_growth, [(0.40, 13), (0.25, 11), (0.12, 8), (0, 4)])
    )
    quality = (
        _band(gross_margin, [(0.60, 7), (0.40, 5), (0.25, 3), (0, 1)])
        + _band(operating_margin, [(0.20, 6), (0.10, 4), (0, 2)])
        + _band(profit_margin, [(0.15, 5), (0.07, 3), (0, 1)])
        + _band(roe, [(0.25, 7), (0.15, 5), (0.08, 3), (0, 1)])
    )
    balance = 0.0
    if debt_to_equity is not None:
        balance += 8 if debt_to_equity <= 0.4 else 6 if debt_to_equity <= 0.8 else 3 if debt_to_equity <= 1.5 else 0
    if current_ratio is not None:
        balance += 6 if current_ratio >= 2 else 4 if current_ratio >= 1.25 else 1 if current_ratio >= 1 else 0
    if fcf is not None:
        balance += 6 if fcf > 0 else 0
    elif fcf_yield is not None:
        balance += 6 if fcf_yield > 0 else 0

    valuation = 0.0
    if peg is not None and peg > 0:
        valuation += 6 if peg <= 1 else 4 if peg <= 1.5 else 2 if peg <= 2.5 else 0
    elif pe is not None and pe > 0:
        valuation += 4 if pe <= 20 else 3 if pe <= 30 else 1 if pe <= 45 else 0
    if fcf_yield is not None:
        valuation += 4 if fcf_yield >= 0.06 else 3 if fcf_yield >= 0.03 else 1 if fcf_yield > 0 else 0

    if market_cap <= 300_000_000:
        runway = 10.0
    elif market_cap <= 1_000_000_000:
        runway = 9.0
    elif market_cap <= 2_000_000_000:
        runway = 7.0
    elif market_cap <= 5_000_000_000:
        runway = 4.0
    else:
        runway = 1.0

    red_flags: list[str] = []
    penalty = 0.0
    if market_cap < 50_000_000:
        red_flags.append("micro-cap: ความผันผวนและความเสี่ยงสภาพคล่องสูง")
        penalty += 8
    if base["dollar_volume"] < 3_000_000:
        red_flags.append("มูลค่าซื้อขายต่อวันต่ำ")
        penalty += 5
    if debt_to_equity is not None and debt_to_equity > 2:
        red_flags.append("หนี้ต่อทุนสูง")
        penalty += 8
    if current_ratio is not None and current_ratio < 1:
        red_flags.append("สภาพคล่องระยะสั้นตึง")
        penalty += 6
    if profit_margin is not None and profit_margin < 0:
        red_flags.append("ยังขาดทุน")
        penalty += 4
    if fcf is not None and fcf < 0:
        red_flags.append("กระแสเงินสดอิสระติดลบ")
        penalty += 5
    if revenue_growth is not None and revenue_growth < 0:
        red_flags.append("รายได้หดตัว")
        penalty += 8
    if revenue_growth is not None and revenue_growth > 2:
        red_flags.append("growth สูงผิดปกติ: อาจเกิดจากฐานต่ำ ต้องเปิดงบตรวจ")
        penalty += 3
    if fcf_yield is not None and abs(fcf_yield) > 0.50:
        red_flags.append("FCF yield ผิดปกติ: ตรวจรายการพิเศษ/คุณภาพข้อมูล")
        penalty += 3
    if (base.get("industry") or "").lower().find("biotechnology") >= 0:
        red_flags.append("ไบโอเทค: ต้องตรวจ cash runway และความเสี่ยงผลทดลองแยก")
    if (fundamentals.get("sector") or base.get("sector") or "").lower() in {
        "finance", "financial services", "real estate",
    }:
        red_flags.append("ธุรกิจการเงิน/อสังหาฯ: D/E, current ratio และ FCF ต้องตีความเฉพาะอุตสาหกรรม")

    raw_score = growth + quality + balance + valuation + runway - penalty
    score = round(_clamp(raw_score), 1)
    available = sum(v is not None for v in (
        revenue_growth, earnings_growth, gross_margin, operating_margin, profit_margin,
        roe, debt_to_equity, current_ratio, fcf_yield, pe, peg,
    ))
    confidence = round(available / 11 * 100)
    positives: list[str] = []
    if revenue_growth is not None and revenue_growth >= 0.20:
        positives.append(f"รายได้โต {revenue_growth * 100:.1f}%")
    if earnings_growth is not None and earnings_growth >= 0.20:
        positives.append(f"กำไรโต {earnings_growth * 100:.1f}%")
    if operating_margin is not None and operating_margin >= 0.10:
        positives.append(f"มาร์จิ้นดำเนินงาน {operating_margin * 100:.1f}%")
    if debt_to_equity is not None and debt_to_equity <= 0.8:
        positives.append(f"D/E ต่ำ {debt_to_equity:.2f}x")
    if fcf_yield is not None and fcf_yield > 0:
        positives.append(f"FCF yield {fcf_yield * 100:.1f}%")
    if peg is not None and 0 < peg <= 1.5:
        positives.append(f"PEG {peg:.2f}")

    return {
        **base,
        "name": fundamentals.get("long_name") or base["name"],
        "sector": fundamentals.get("sector") or base.get("sector"),
        "industry": fundamentals.get("industry") or base.get("industry"),
        "market_cap": market_cap,
        "score": score,
        "confidence": confidence,
        "scorecard": {
            "growth": round(growth, 1),
            "quality": round(quality, 1),
            "balance_sheet": round(balance, 1),
            "valuation": round(valuation, 1),
            "runway": round(runway, 1),
            "penalty": round(penalty, 1),
        },
        "metrics": {
            "revenue_growth": revenue_growth,
            "earnings_growth": earnings_growth,
            "gross_margin": gross_margin,
            "operating_margin": operating_margin,
            "profit_margin": profit_margin,
            "roe": roe,
            "debt_to_equity": debt_to_equity,
            "current_ratio": current_ratio,
            "fcf_yield": fcf_yield,
            "pe": pe,
            "peg": peg,
        },
        "positives": positives,
        "red_flags": red_flags,
        "data_source": fundamentals.get("_source"),
    }


async def scan_small_caps(
    *,
    min_market_cap: float = 50_000_000,
    max_market_cap: float = 3_000_000_000,
    min_price: float = 2.0,
    min_dollar_volume: float = 1_000_000,
    sector: str | None = None,
    limit: int = 20,
    enrich_limit: int = 45,
    us_only: bool = True,
    fundamental_loader: Callable[[str], Awaitable[dict]] | None = None,
) -> dict:
    rows = await fetch_nasdaq_universe()
    universe = prefilter_universe(
        rows,
        min_market_cap=min_market_cap,
        max_market_cap=max_market_cap,
        min_price=min_price,
        min_dollar_volume=min_dollar_volume,
        sector=sector,
        us_only=us_only,
    )
    async def default_loader(symbol: str) -> dict:
        # The scanner favors the lightweight Yahoo snapshot. The deeper
        # EDGAR/yfinance merge remains available after the user opens VI.
        try:
            snapshot = await fetch_yahoo_fundamentals(symbol)
            snapshot["_source"] = "yahoo"
            return snapshot
        except Exception:
            return await get_fundamentals(symbol)

    loader = fundamental_loader or default_loader
    semaphore = asyncio.Semaphore(6)

    async def enrich(base: dict) -> dict | None:
        async with semaphore:
            try:
                fundamentals = await loader(base["symbol"])
                return score_candidate(base, fundamentals)
            except Exception:
                return None

    # Stratify by sector and size. Simply taking the most-traded names tends
    # to produce distressed/meme stocks, while taking only the tiniest names
    # produces an illiquid lottery-ticket list.
    groups: dict[tuple[str, str], list[dict]] = {}
    for item in universe:
        cap = item["market_cap"]
        size_band = "micro" if cap < 300_000_000 else "small" if cap < 1_000_000_000 else "upper-small"
        sector_key = item.get("sector") or "Unknown"
        turnover = item["dollar_volume"] / max(cap, 1)
        # Reward sufficient liquidity, but penalize extreme one-day turnover
        # that often accompanies news spikes and speculative squeezes.
        item["_research_priority"] = (
            math.log10(max(item["dollar_volume"], 1))
            - max(0.0, turnover - 0.20) * 8
            - (1.0 if turnover > 0.75 else 0.0)
        )
        groups.setdefault((sector_key, size_band), []).append(item)
    for bucket in groups.values():
        bucket.sort(key=lambda x: x["_research_priority"], reverse=True)

    selected: list[dict] = []
    active = sorted(groups)
    depth = 0
    while active and len(selected) < enrich_limit:
        next_active: list[tuple[str, str]] = []
        for key in active:
            bucket = groups[key]
            if depth < len(bucket):
                selected.append(bucket[depth])
                next_active.append(key)
                if len(selected) >= enrich_limit:
                    break
        active = next_active
        depth += 1

    enriched = await asyncio.gather(*(enrich(item) for item in selected))
    ranked = sorted(
        (item for item in enriched if item is not None),
        key=lambda x: (x["score"], x["confidence"], x["dollar_volume"]),
        reverse=True,
    )[:limit]
    return {
        "as_of": int(time.time()),
        "universe_source": "Nasdaq Stock Screener",
        "universe_count": len(rows),
        "prefilter_count": len(universe),
        "analyzed_count": sum(item is not None for item in enriched),
        "criteria": {
            "min_market_cap": min_market_cap,
            "max_market_cap": max_market_cap,
            "min_price": min_price,
            "min_dollar_volume": min_dollar_volume,
            "sector": sector,
            "us_only": us_only,
        },
        "candidates": ranked,
        "methodology": (
            "คะแนน 100 = Growth 35 + Quality 25 + Balance sheet 20 + "
            "Valuation 10 + Size runway 10 - red-flag penalties"
        ),
        "coverage_note": (
            f"ระบบอ่าน snapshot พื้นฐานแบบเร็วของตัวอย่าง {len(selected)} บริษัท "
            "ที่กระจายตาม sector/ขนาดจากผู้ผ่านรอบแรก ไม่ได้อ่านงบครบทุกบริษัทในครั้งเดียว "
            "ตัวเลข TTM จาก Yahoo อาจต่างจากงบ SEC รายปี จึงควรกด VI ยืนยันก่อนตัดสินใจ"
        ),
        "disclaimer": (
            "รายการนี้เป็นเครื่องมือค้นคว้าเบื้องต้น ไม่ใช่คำแนะนำซื้อขาย "
            "หุ้นขนาดเล็กอาจขาดสภาพคล่อง ผันผวนสูง และสูญเสียเงินลงทุนทั้งหมดได้"
        ),
    }
