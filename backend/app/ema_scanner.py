"""S&P 500 EMA-proximity scanner (สแกนหาหุ้นที่ราคา "ลงมาใกล้" เส้นค่าเฉลี่ย EMA 50/100/200).

เลนส์สายเทคนิค — ต่างจาก revenue_scanner / value_scanner ที่วัด "ถูก/แพง" จากงบการเงิน
โมดูลนี้ไม่แตะงบเลย ดูแค่โครงสร้างราคา: ตอนนี้ราคาห่างจากเส้น EMA แต่ละเส้นกี่ %

ตรรกะหลัก (ต่อหุ้น 1 ตัว):
  ดึงราคารายวันย้อนหลัง ~3 ปี → EMA 50/100/200 (indicators.ema เดียวกับสายเทคนิคในแอป)
  → ระยะห่าง % = (ราคา − EMA) / EMA × 100 → เข้าเกณฑ์เมื่อ |ระยะห่าง| ≤ tolerance ที่ตั้งไว้

นอกจาก "ใกล้ไหม" ยังเก็บบริบทที่ทำให้ตีความได้ถูก ซึ่งสำคัญพอ ๆ กับตัวเลขระยะห่าง:
  • ema200_slope_pct — EMA200 กำลังชี้ขึ้นหรือลง (ราคาแตะ EMA200 ขาขึ้น = ย่อพัก,
    แตะตอน EMA200 ชี้ลง = เทรนด์เสียแล้ว คนละสถานการณ์กันคนละเรื่อง)
  • touches_2y / days_since_touch — 2 ปีที่ผ่านมาลงมาแตะโซนนี้กี่ครั้ง ครั้งล่าสุดกี่วันก่อน
    (ตอบคำถาม "นาน ๆ จะลงมาที" ด้วยตัวเลขจริงของหุ้นตัวนั้น ไม่ใช่ความรู้สึก)
  • drawdown_pct — ตอนนี้ห่างจากจุดสูงสุด 52 สัปดาห์เท่าไร

แคชเหมือนสแกนตัวอื่น (data_sp500_ema_scan.json) แต่ TTL สั้นกว่า (12 ชม.) เพราะราคาขยับทุกวัน
ต่างจากงบที่ออกไตรมาสละครั้ง.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from app import indicators
from app.revenue_scanner import _market_cap_lookup, load_sp500

_SCAN_CACHE = Path(__file__).resolve().parent / "data_sp500_ema_scan.json"
_SCAN_TTL = 12 * 3600          # ราคาขยับทุกวัน — ผลเก่ากว่า 12 ชม. ถือว่า stale
_PERIODS = (50, 100, 200)
_BARS = 780                    # ~3 ปีทำการ: พอให้ EMA200 seed นิ่ง + นับการแตะย้อนหลัง 2 ปีได้
_MIN_BARS = 260                # ต่ำกว่านี้ EMA200 เพิ่งเกิด ยังไม่มีความหมาย
_TOUCH_WINDOW = 504            # ~2 ปีทำการ ใช้เป็นกรอบนับจำนวนครั้งที่เคยลงมาแตะ


def _pct_away(price: float, ema_val: float | None) -> float | None:
    """ราคาห่างจากเส้นกี่ % (บวก = ราคาอยู่เหนือเส้น, ลบ = อยู่ใต้เส้น)."""
    if not ema_val or ema_val <= 0:
        return None
    return (price - ema_val) / ema_val * 100


def _touch_stats(closes: list[float], ema_line: list[float | None], tolerance_pct: float,
                 cooldown_bars: int = 10) -> dict:
    """นับ "ครั้งที่ราคาลงมาแตะโซนเส้น" ในกรอบ ~2 ปีล่าสุด.

    นับเป็น *ครั้ง* (episode) ไม่ใช่ *จำนวนวัน* — ราคาที่ป้วนเปี้ยนอยู่ในโซน 10 วันติดกัน
    คือการลงมาแตะ 1 ครั้ง ไม่ใช่ 10 ครั้ง ไม่งั้นตัวเลข "นาน ๆ ลงมาที" จะเพี้ยนหมด.

    cooldown_bars: ต้องออกจากโซนติดกันอย่างน้อยกี่แท่ง ถึงจะนับการกลับเข้ามาเป็น "ครั้งใหม่"
    — ราคาที่แกว่งเข้า-ออกขอบโซนวันเว้นวันคือการมาเยือนรอบเดียว ไม่ใช่หลายรอบ ถ้าไม่มีกันชนนี้
    ตัวเลขจะพองจนใช้ตอบคำถาม "นาน ๆ ลงมาที" ไม่ได้เลย (ทดสอบจริง: AAPL ได้ 15 ครั้ง/2 ปี
    ทั้งที่จริง ๆ ลงมาเยือนไม่กี่รอบ).
    """
    window = min(len(closes), _TOUCH_WINDOW)
    episodes = 0
    days_in_zone = 0
    bars_since_zone = cooldown_bars      # เริ่มนอกโซน: การเข้าโซนครั้งแรกนับเป็นครั้งที่ 1 เสมอ
    last_touch_idx: int | None = None

    for i in range(len(closes) - window, len(closes)):
        e = ema_line[i]
        if not e or e <= 0:
            continue
        near = abs((closes[i] - e) / e * 100) <= tolerance_pct
        if near:
            days_in_zone += 1
            last_touch_idx = i
            if bars_since_zone >= cooldown_bars:
                episodes += 1
            bars_since_zone = 0
        else:
            bars_since_zone += 1

    return {
        "touches_2y": episodes,
        "days_in_zone_2y": days_in_zone,
        "pct_days_in_zone_2y": round(days_in_zone / window * 100, 1) if window else None,
        # นับจากแท่งล่าสุด: 0 = ยังอยู่ในโซนตอนนี้
        "days_since_touch": (len(closes) - 1 - last_touch_idx) if last_touch_idx is not None else None,
    }


async def _symbol_ema_state(symbol: str, name: str, sector: str) -> dict | None:
    """คำนวณสถานะ EMA ของหุ้นตัวเดียว. คืน None ถ้าราคาย้อนหลังไม่พอ (หุ้น IPO ใหม่/ดึงไม่ได้)."""
    from app.main import provider  # lazy import กันวน circular import (ตามแบบ revenue_scanner.py)

    get_history = getattr(provider, "get_history", None)
    try:
        candles = await get_history(symbol, "1d", max_bars=_BARS) if get_history \
            else await provider.get_candles(symbol, "1d", _BARS)
    except Exception:  # noqa: BLE001 — หุ้นเดียวพังไม่ควรล้มทั้งการสแกน
        return None
    if not candles or len(candles) < _MIN_BARS:
        return None

    closes = [c.close for c in candles]
    price = closes[-1]
    if price <= 0:
        return None

    lines = {p: indicators.ema(closes, p) for p in _PERIODS}
    emas = {p: lines[p][-1] for p in _PERIODS}
    if not emas[200]:
        return None

    # ความชันของ EMA200: เทียบกับค่าเมื่อ ~3 เดือนก่อน (63 แท่งทำการ)
    slope_pct = None
    prev200 = lines[200][-64] if len(lines[200]) >= 64 else None
    if prev200 and prev200 > 0:
        slope_pct = round((emas[200] - prev200) / prev200 * 100, 1)

    high_52w = max(closes[-252:]) if len(closes) >= 252 else max(closes)

    out = {
        "symbol": symbol,
        "name": name,
        "sector": sector,
        "price": round(price, 2),
        "ema200_slope_pct": slope_pct,            # บวก = EMA200 ชี้ขึ้น (เทรนด์ใหญ่ยังดี)
        "drawdown_pct": round((price / high_52w - 1) * 100, 1) if high_52w else None,
        "as_of_bar": datetime.fromtimestamp(candles[-1].time, tz=timezone.utc).strftime("%Y-%m-%d"),
    }
    for p in _PERIODS:
        out[f"ema{p}"] = round(emas[p], 2) if emas[p] else None
        out[f"dist{p}_pct"] = round(d, 2) if (d := _pct_away(price, emas[p])) is not None else None
    # สถิติการแตะเก็บเฉพาะ EMA200 ที่โซน ±3% (ค่ากลาง) — เก็บไว้ในแคชครั้งเดียว ไม่ผูกกับ
    # tolerance ที่ผู้ใช้ปรับตอนดูผล เพราะการนับย้อนหลังต้องใช้เส้น EMA ทั้งชุดซึ่งไม่ได้เก็บลงไฟล์
    out.update(_touch_stats(closes, lines[200], 3.0))
    return out


async def _run_full_scan(concurrency: int) -> dict:
    universe = load_sp500()
    semaphore = asyncio.Semaphore(concurrency)

    async def worker(item: dict) -> dict | None:
        async with semaphore:
            try:
                return await _symbol_ema_state(item["symbol"], item["name"], item["sector"])
            except Exception:  # noqa: BLE001
                return None

    results = await asyncio.gather(*(worker(item) for item in universe))
    ok = [r for r in results if r is not None]
    return {
        "as_of": time.time(),
        "universe_count": len(universe),
        "success_count": len(ok),
        "results": ok,
    }


def _load_cache() -> dict | None:
    if not _SCAN_CACHE.exists():
        return None
    try:
        return json.loads(_SCAN_CACHE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save_cache(payload: dict) -> None:
    _SCAN_CACHE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def can_scan_live() -> bool:
    """สแกนสดได้เฉพาะตอนรันในเครื่อง (มี .git) — บน HF Space เสิร์ฟแคชอย่างเดียว
    เหมือน revenue_scanner/value_scanner กัน 1 คลิก = ยิง Yahoo 500 ครั้งบนคลาวด์."""
    return (Path(__file__).resolve().parents[2] / ".git").exists()


# ความชัน EMA200 ที่ต่ำกว่านี้ถือว่า "แบน" (ไม่นับเป็นขาขึ้นหรือขาลง) — ใช้ค่าเดียวกันทั้ง
# ตอนกรอง (trend=up/down) และตอนติดป้าย ไม่งั้นผู้ใช้กรอง "เส้นชี้ลง" แล้วได้แถวที่ป้ายเขียนว่า
# "เส้นแบน" ซึ่งขัดกันเองและทำให้ไม่รู้ว่าจะเชื่ออันไหน
_TREND_FLAT_PCT = 1.0


def _trend_of(slope_pct: float | None) -> str:
    """ความชัน EMA200 → up / down / flat / unknown."""
    if slope_pct is None:
        return "unknown"
    if slope_pct > _TREND_FLAT_PCT:
        return "up"
    if slope_pct < -_TREND_FLAT_PCT:
        return "down"
    return "flat"


def _zone_label(dist_pct: float | None, slope_pct: float | None) -> tuple[str, str]:
    """แปลระยะห่าง+ความชันเป็นป้ายกำกับสั้น ๆ (ไม่ใช่คำแนะนำซื้อขาย — เป็นการอ่านโครงสร้างราคา)."""
    if dist_pct is None:
        return "—", ""
    trend = _trend_of(slope_pct)
    if trend == "down":
        return ("⚠️ เส้นชี้ลง", "EMA200 กำลังลาดลง — ราคาใกล้เส้นตอนเทรนด์ใหญ่เสีย "
                                "ต่างจากการย่อพักในขาขึ้น มักไม่ใช่แนวรับที่เชื่อได้")
    if trend == "up":
        return ("🟢 เหนือเส้น ขาขึ้น" if dist_pct >= 0 else "🟢 ย่อในขาขึ้น",
                "EMA200 ชี้ขึ้น — ราคาย่อลงมาหาเส้นระหว่างเทรนด์ขาขึ้น")
    if trend == "flat":
        return "⚪ เส้นแบน", "EMA200 แทบไม่มีความชัน — ราคาออกข้าง ไม่มีเทรนด์ชัด"
    return "—", "ข้อมูลไม่พอคำนวณความชัน EMA200"


async def _format_result(cached: dict, *, period: int, tolerance_pct: float, side: str,
                         trend: str, limit: int, min_market_cap: float | None,
                         max_market_cap: float | None, rescanned: bool = False) -> dict:
    key = f"dist{period}_pct"
    candidates = []
    for r in cached["results"]:
        d = r.get(key)
        if d is None or abs(d) > tolerance_pct:
            continue
        if side == "below" and d > 0:
            continue
        if side == "above" and d < 0:
            continue
        slope = r.get("ema200_slope_pct")
        if trend in ("up", "down") and _trend_of(slope) != trend:
            continue
        label, hint = _zone_label(d, slope)
        candidates.append({**r, "distance_pct": d, "zone_label": label, "zone_hint": hint,
                           "trend": _trend_of(slope)})

    # ใกล้เส้นที่สุดขึ้นก่อน (ใช้ค่าสัมบูรณ์ — ใต้เส้น 1% กับเหนือเส้น 1% ใกล้เท่ากัน)
    candidates.sort(key=lambda r: abs(r["distance_pct"]))

    cap_map = await _market_cap_lookup()
    enriched = []
    for r in candidates:
        cap = cap_map.get(r["symbol"])
        # ไม่มี market cap ให้เทียบ = กรองทิ้งเมื่อผู้ใช้ตั้งกรอบไว้ (เหมือนสแกนตัวอื่น)
        if min_market_cap is not None and (cap is None or cap < min_market_cap):
            continue
        if max_market_cap is not None and (cap is None or cap > max_market_cap):
            continue
        enriched.append({**r, "market_cap": cap})
    candidates = enriched

    as_of = cached["as_of"]
    return {
        "as_of": as_of,
        "as_of_label": datetime.fromtimestamp(as_of, tz=timezone.utc).astimezone().strftime("%d %b %Y %H:%M"),
        "stale": (time.time() - as_of) > _SCAN_TTL,
        "rescanned": rescanned,
        "can_scan_live": can_scan_live(),
        "universe_count": cached["universe_count"],
        "success_count": cached["success_count"],
        "criteria": {
            "period": period, "tolerance_pct": tolerance_pct, "side": side, "trend": trend,
            "limit": limit, "min_market_cap": min_market_cap, "max_market_cap": max_market_cap,
        },
        "candidates": candidates[:limit],
        "candidate_count": len(candidates),
        "methodology": (
            f"EMA{period} จากราคาปิดรายวัน (ย้อนหลังสูงสุด ~3 ปี) · ระยะห่าง % = (ราคา − EMA) ÷ EMA × 100 "
            f"— เข้าเกณฑ์เมื่ออยู่ในโซน ±{tolerance_pct}% · ความชัน EMA200 วัดจากค่าเมื่อ ~3 เดือนก่อน "
            f"(เกิน ±{_TREND_FLAT_PCT:.0f}% ถึงนับเป็นขาขึ้น/ขาลง ต่ำกว่านั้นถือว่าเส้นแบน) · "
            f"'ลงมาแตะ n ครั้ง/2 ปี' นับที่โซน ±3% ของ EMA200 แบบนับเป็นครั้ง (ช่วงที่อยู่ในโซนติดกันนับเป็นครั้งเดียว)"
        ),
        "coverage_note": (
            f"คำนวณได้ {cached['success_count']}/{cached['universe_count']} บริษัทใน S&P 500 "
            f"(หุ้นที่เพิ่ง IPO หรือมีราคาย้อนหลังไม่ถึง ~{_MIN_BARS} วันทำการ จะไม่ปรากฏ เพราะ EMA200 ยังไม่นิ่งพอ)"
        ),
        "disclaimer": (
            "การที่ราคาลงมาใกล้ EMA ไม่ได้แปลว่า 'ต้องเด้ง' — เส้นค่าเฉลี่ยเป็นเพียงจุดอ้างอิงที่คนจำนวนมากเฝ้าดู "
            "ไม่ใช่แนวรับที่มีแรงพยุงจริง หุ้นที่พื้นฐานเสียจะทะลุเส้นลงไปแล้วไม่กลับมาอีกเลยก็มี "
            "ให้ดูความชันของเส้นและเหตุผลที่ราคาลงมาประกอบเสมอ — เพื่อการศึกษา ไม่ใช่คำแนะนำการลงทุน"
        ),
    }


async def scan_sp500_ema(
    *, period: int = 200, tolerance_pct: float = 3.0, side: str = "both", trend: str = "any",
    limit: int = 40, concurrency: int = 8, refresh: bool = False, auto: bool = False,
    min_market_cap: float | None = None, max_market_cap: float | None = None,
) -> dict:
    """refresh=True: สแกนสดใหม่เสมอ · auto=True: สแกนสดใหม่ให้เองเมื่อผลเก่ากว่า 12 ชม.
    (เฉพาะตอนรันในเครื่อง — บนเว็ปเสิร์ฟแคชเสมอ) · ทั้งคู่ False: อ่านแคชอย่างเดียว.

    period: 50/100/200 · side: both/above/below · trend: any/up/down (ความชัน EMA200)."""
    if period not in _PERIODS:
        raise ValueError(f"period ต้องเป็น {' / '.join(map(str, _PERIODS))}")

    cached = None if refresh else _load_cache()
    outdated = cached is not None and (time.time() - cached["as_of"]) > _SCAN_TTL
    rescanned = False
    if cached is None or (auto and outdated and can_scan_live()):
        cached = await _run_full_scan(concurrency)
        _save_cache(cached)
        rescanned = True
    return await _format_result(
        cached, period=period, tolerance_pct=tolerance_pct, side=side, trend=trend, limit=limit,
        min_market_cap=min_market_cap, max_market_cap=max_market_cap, rescanned=rescanned,
    )
