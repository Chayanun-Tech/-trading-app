"""S&P 500 revenue-vs-price value scanner.

หาหุ้นใน S&P 500 ที่ราคาตอนนี้ "ต่ำกว่า" ราคาที่รายได้ควรบ่งชี้ (ใช้ตรรกะเดียวกับ
เส้น "ราคาตามรายได้" ในแท็บ 📈 โมเดลรายได้ — ดู revenue_model.py) แต่รันสแกนทีเดียว
ทั้ง 500 บริษัทแทนที่จะดูทีละตัว

การสแกนเต็มรูปแบบ (500 บริษัท x งบ SEC + ราคาย้อนหลัง) ใช้เวลาหลายนาทีและควรรันในเครื่อง
(เหมือน offline Thai fundamentals) แล้ว cache ผลลง data_sp500_revenue_scan.json commit
ขึ้น repo — เว็ป (HF Space) แค่เสิร์ฟไฟล์ cache นี้ทันที ไม่ต้องสแกนสดบนคลาวด์ (เลี่ยง rate-limit
+ timeout). ปุ่ม "สแกนใหม่" ใน UI เรียก refresh=true ได้เฉพาะตอนรันในเครื่อง.
"""
from __future__ import annotations

import asyncio
import csv
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from app import edgar
from app.revenue_model import (
    _REVENUE_CONCEPTS,
    _annual_with_end,
    _fp_key,
    _quarterly_with_fp,
    _synthesize_missing_quarter,
)

_CSV_PATH = Path(__file__).resolve().parent / "sp500_constituents.csv"
_SCAN_CACHE = Path(__file__).resolve().parent / "data_sp500_revenue_scan.json"
_SCAN_TTL = 24 * 3600  # ราคาที่แคชไว้ถือว่า "เก่า" หลัง 24 ชม. (แต่ยังเสิร์ฟได้ถ้าไม่มีอะไรใหม่กว่า)


def load_sp500() -> list[dict]:
    """อ่านรายชื่อ S&P 500 (symbol/name/sector) จาก CSV คงที่ในเครื่อง
    (แหล่ง: github.com/datasets/s-and-p-500-companies, snapshot ณ วันดึง — สมาชิกดัชนีเปลี่ยนไม่บ่อย)."""
    out = []
    with _CSV_PATH.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            sym = (row.get("Symbol") or "").strip().upper()
            if not sym:
                continue
            # SEC/Yahoo ใช้ขีด (-) สำหรับหุ้นหลาย class เช่น BRK.B → BRK-B, BF.B → BF-B
            out.append({
                "symbol": sym.replace(".", "-"),
                "name": (row.get("Security") or "").strip(),
                "sector": (row.get("GICS Sector") or "").strip(),
            })
    return out


async def _symbol_revenue_gap(symbol: str, name: str, sector: str, lookback_quarters: int) -> dict | None:
    """คำนวณ gap ระหว่างราคาจริงกับ 'ราคาตามรายได้' ของหุ้นตัวเดียว (ตรรกะเดียวกับ revenue_model.py).
    คืน None ถ้าข้อมูลไม่พอ (ไม่ใช่ error — บางบริษัท เช่น กลุ่มการเงิน/ประกัน ไม่ยื่น revenue
    ในรูปแบบมาตรฐานที่ใช้คำนวณตรงนี้ได้)."""
    try:
        facts = await edgar.get_company_facts(symbol)
    except Exception:  # noqa: BLE001
        return None

    raw_revenue_meta = _synthesize_missing_quarter(
        _quarterly_with_fp(facts, _REVENUE_CONCEPTS, "USD"),
        _annual_with_end(facts, _REVENUE_CONCEPTS, "USD"),
    )
    dates = sorted(raw_revenue_meta.keys())
    if len(dates) < 5:
        return None

    by_key: dict[tuple[str, int], float] = {}
    for d in dates:
        k = _fp_key(raw_revenue_meta[d], d)
        if k:
            by_key[k] = raw_revenue_meta[d]["val"]

    quarters_full = []
    for d in dates:
        meta = raw_revenue_meta[d]
        rev = meta["val"]
        k = _fp_key(meta, d)
        yoy = None
        if k:
            prev = by_key.get((k[0], k[1] - 1))
            if prev:
                yoy = (rev - prev) / abs(prev)
        quarters_full.append({"period": d, "revenue": rev, "yoy_pct": yoy})

    quarters = [q for q in quarters_full if q["yoy_pct"] is not None][-lookback_quarters:]
    if len(quarters) < 2:
        return None

    # ป้องกันข้อมูลเพี้ยน: บริษัทบางกลุ่ม (ธนาคาร/ประกัน) เลิกยื่น revenue ใน concept มาตรฐานนี้
    # หลังเปลี่ยนมาตรฐานบัญชี (ASC 606 ไม่ครอบคลุมดอกเบี้ยรับ) ทำให้ "ไตรมาสล่าสุดที่คำนวณได้"
    # กลายเป็นข้อมูลเก่าหลายปีโดยไม่รู้ตัว — เทียบราคาวันนี้กับรายได้เมื่อ 10 ปีก่อนไม่มีความหมาย ข้ามไปเลย
    latest_period = datetime.fromisoformat(quarters[-1]["period"]).replace(tzinfo=timezone.utc)
    if (datetime.now(timezone.utc) - latest_period).days > 270:
        return None

    from app.main import provider  # lazy import กันวน circular import (ตามแบบ revenue_model.py)
    get_history = getattr(provider, "get_history", None)
    try:
        candles = await get_history(symbol, "1d", max_bars=lookback_quarters * 70 + 30) if get_history \
            else await provider.get_candles(symbol, "1d", lookback_quarters * 70 + 30)
    except Exception:  # noqa: BLE001
        return None
    if not candles:
        return None

    start_time = max(candles[0].time, int(datetime.fromisoformat(quarters[0]["period"]).replace(tzinfo=timezone.utc).timestamp()))
    price_base = next((c for c in candles if c.time >= start_time), candles[0])
    rev_base = next(
        (q for q in quarters
         if int(datetime.fromisoformat(q["period"]).replace(tzinfo=timezone.utc).timestamp()) >= start_time),
        quarters[0],
    )
    if not rev_base["revenue"]:
        return None

    latest = quarters[-1]
    fair_now = price_base.close * (latest["revenue"] / rev_base["revenue"])
    if not fair_now or fair_now <= 0:
        return None
    price_now = candles[-1].close
    gap_pct = (price_now - fair_now) / fair_now * 100

    return {
        "symbol": symbol,
        "name": name,
        "sector": sector,
        "price_now": round(price_now, 2),
        "fair_price": round(fair_now, 2),
        "gap_pct": round(gap_pct, 1),
        "base_quarter": rev_base["period"][:7],
        "latest_quarter": latest["period"][:7],
        "latest_yoy_pct": round(latest["yoy_pct"] * 100, 1) if latest["yoy_pct"] is not None else None,
    }


async def _run_full_scan(lookback_quarters: int, concurrency: int) -> dict:
    universe = load_sp500()
    semaphore = asyncio.Semaphore(concurrency)

    async def worker(item: dict) -> dict | None:
        async with semaphore:
            try:
                return await _symbol_revenue_gap(item["symbol"], item["name"], item["sector"], lookback_quarters)
            except Exception:  # noqa: BLE001
                return None

    results = await asyncio.gather(*(worker(item) for item in universe))
    ok = [r for r in results if r is not None]
    return {
        "as_of": time.time(),
        "universe_count": len(universe),
        "success_count": len(ok),
        "lookback_quarters": lookback_quarters,
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
    ไม่งั้นคนเข้าเว็ปกดปุ่มเดียว = ยิง SEC/Yahoo อีก 500 ครั้งบนคลาวด์ (rate-limit + timeout)."""
    return (Path(__file__).resolve().parents[2] / ".git").exists()


async def _market_cap_lookup() -> dict[str, float]:
    """symbol -> market cap (USD) จาก Nasdaq screener (ครอบคลุมหุ้น US ทุกตลาด ไม่ใช่แค่ Nasdaq)
    ใช้แคช 30 นาทีเดียวกับ multibagger_scanner กันยิง API ซ้ำ."""
    from app.multibagger_scanner import fetch_nasdaq_universe

    try:
        rows = await fetch_nasdaq_universe()
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, float] = {}
    for row in rows:
        sym = str(row.get("symbol") or "").strip().upper()
        cap_raw = row.get("marketCap")
        if not sym or cap_raw in (None, ""):
            continue
        try:
            cap = float(str(cap_raw).replace("$", "").replace(",", ""))
        except (TypeError, ValueError):
            continue
        if cap > 0:
            out[sym] = cap
    return out


async def _format_result(
    cached: dict, max_gap_pct: float, limit: int,
    min_market_cap: float | None = None, max_market_cap: float | None = None,
    rescanned: bool = False,
) -> dict:
    candidates = [r for r in cached["results"] if r["gap_pct"] <= max_gap_pct]
    candidates.sort(key=lambda r: r["gap_pct"])

    cap_map = await _market_cap_lookup()
    enriched = []
    for r in candidates:
        cap = cap_map.get(r["symbol"])
        if min_market_cap is not None and (cap is None or cap < min_market_cap):
            continue  # ไม่มี market cap ให้เทียบ หรือเล็กกว่ากรอบที่ตั้ง กรองทิ้งเมื่อผู้ใช้ตั้งขั้นต่ำ
        if max_market_cap is not None and (cap is None or cap > max_market_cap):
            continue
        enriched.append({**r, "market_cap": cap})
    candidates = enriched

    as_of = cached["as_of"]
    return {
        "as_of": as_of,
        "as_of_label": datetime.fromtimestamp(as_of, tz=timezone.utc).astimezone().strftime("%d %b %Y %H:%M"),
        "stale": (time.time() - as_of) > _SCAN_TTL,
        "rescanned": rescanned,          # True = รอบนี้สแกนสดใหม่ให้อัตโนมัติ (ไม่ได้อ่านแคชเก่า)
        "can_scan_live": can_scan_live(),
        "universe_count": cached["universe_count"],
        "success_count": cached["success_count"],
        "criteria": {
            "max_gap_pct": max_gap_pct, "limit": limit,
            "min_market_cap": min_market_cap, "max_market_cap": max_market_cap,
        },
        "candidates": candidates[:limit],
        "candidate_count": len(candidates),
        "methodology": (
            "ราคาตามรายได้ = ราคา ณ ไตรมาสฐาน (~2 ปีก่อน) x (รายได้ไตรมาสล่าสุด / รายได้ไตรมาสฐาน) "
            "gap % = (ราคาจริงตอนนี้ - ราคาตามรายได้) / ราคาตามรายได้ x 100 — ติดลบมาก = ราคาต่ำกว่าที่รายได้บ่งชี้มาก"
        ),
        "coverage_note": (
            f"คำนวณสำเร็จ {cached['success_count']}/{cached['universe_count']} บริษัทใน S&P 500 "
            "(บางกลุ่ม เช่น ธนาคาร/ประกัน/REIT อาจข้ามไป เพราะยื่นงบรายได้ในรูปแบบที่ต่างจากบริษัททั่วไป "
            "จึงคำนวณด้วยตรรกะนี้ตรง ๆ ไม่ได้)"
        ),
        "disclaimer": (
            "เป็นตัวกรองเชิงปริมาณอย่างง่าย ไม่ได้ปรับตามจำนวนหุ้นที่เปลี่ยนไป (ซื้อหุ้นคืน/เพิ่มทุน) "
            "มาร์จิ้น หรือคุณภาพธุรกิจ — ใช้เป็นจุดเริ่มค้นคว้าต่อ (เปิดแท็บ 📈 โมเดลรายได้ ของหุ้นนั้น) "
            "ไม่ใช่คำแนะนำซื้อขาย"
        ),
    }


async def scan_sp500_revenue_gap(
    *, max_gap_pct: float = -15.0, limit: int = 40, lookback_quarters: int = 8,
    concurrency: int = 8, refresh: bool = False, auto: bool = False,
    min_market_cap: float | None = None, max_market_cap: float | None = None,
) -> dict:
    """refresh=True: สแกนสดใหม่เสมอ · auto=True: สแกนสดใหม่ให้เองเมื่อผลเก่ากว่า 24 ชม.
    (เฉพาะตอนรันในเครื่อง — บนเว็ปเสิร์ฟแคชเสมอ) · ทั้งคู่ False: อ่านแคชอย่างเดียว."""
    cached = None if refresh else _load_cache()
    outdated = cached is not None and (time.time() - cached["as_of"]) > _SCAN_TTL
    rescanned = False
    if cached is None or (auto and outdated and can_scan_live()):
        cached = await _run_full_scan(lookback_quarters, concurrency)
        _save_cache(cached)
        rescanned = True
    return await _format_result(cached, max_gap_pct, limit, min_market_cap, max_market_cap,
                                rescanned=rescanned)
