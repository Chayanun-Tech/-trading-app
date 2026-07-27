"""S&P 500 Peter Lynch classifier scan (สแกนทั้งดัชนีแล้วจัดกลุ่มหุ้นตาม 6 ประเภทของ Peter Lynch).

ต่างจาก value_scanner / revenue_scanner (ที่ตอบว่า "หุ้นตัวไหนถูก") — โมดูลนี้ตอบว่า "หุ้นแต่ละตัว
เป็นหุ้นประเภทไหนในสายตาของ Peter Lynch" แล้วสรุปเป็นตารางจัดกลุ่ม (Fast Grower / Stalwart /
Slow Grower / Cyclical / Turnaround / Asset Play) พร้อมซอยย่อยตาม GICS Sector เพื่อให้หาหุ้นแต่ละ
ประเภทได้ง่าย.

ใช้ตัวจำแนกตัวเดียวกับกล่อง "ประเภทหุ้น (Peter Lynch)" ในแท็บมูลค่า IV — intrinsic_value.classify_peter_lynch()
— ป้อน snapshot ปัจจัยพื้นฐาน (yfinance/Yahoo) ของแต่ละตัว. เป็น heuristic เชิงปริมาณ ไม่ใช่การจัด
หมวดแบบเด็ดขาด (Cyclical/Turnaround/Asset Play ต้องอ่านวงจรธุรกิจ/หมายเหตุงบเพิ่ม) จึงแนบ confidence
ให้ทุกแถว.

เหมือน scanner อื่น: การสแกนเต็ม 500 บริษัทยิง Yahoo หลายร้อยครั้ง ควรรันในเครื่องแล้ว commit ไฟล์แคช
(data_sp500_lynch_scan.json) ขึ้น repo — เว็ป (HF Space) แค่เสิร์ฟแคชทันที ไม่สแกนสดบนคลาวด์.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from app.fundamentals import get_fundamentals
from app.intrinsic_value import classify_peter_lynch
from app.revenue_scanner import load_sp500

_SCAN_CACHE = Path(__file__).resolve().parent / "data_sp500_lynch_scan.json"
_SCAN_TTL = 24 * 3600  # ผลเก่ากว่านี้ถือว่า stale (ยังเสิร์ฟได้ แต่ขึ้นเตือนให้สแกนใหม่)

# ลำดับการแสดงผลของ 6 ประเภท + คำอธิบายสั้นสำหรับหัวตารางสรุป (label ไทยรายตัวมาจาก classifier เอง)
CATEGORY_ORDER = ["Fast Grower", "Stalwart", "Slow Grower", "Cyclical", "Turnaround", "Asset Play"]
CATEGORY_META = {
    "Fast Grower":  {"label_th": "หุ้นเติบโตเร็ว",        "emoji": "🚀", "desc": "กำไร/รายได้โตสูง (Lynch มอง ~20–25%+) ตลาดให้ราคากับอนาคตมาก — ต้องเฝ้าว่า growth รักษาได้หรือเริ่มชะลอ"},
    "Stalwart":     {"label_th": "หุ้นใหญ่แข็งแกร่ง",     "emoji": "🏛️", "desc": "บริษัทใหญ่คุณภาพดี โตปานกลางสม่ำเสมอ — เน้นดูคุณภาพกำไรและราคาที่ไม่แพงเกินไป"},
    "Slow Grower":  {"label_th": "หุ้นเติบโตช้า/ปันผล",   "emoji": "🐢", "desc": "ธุรกิจโตต่ำแต่กระแสเงินสดนิ่ง มักคืนผลตอบแทนผ่านเงินปันผล"},
    "Cyclical":     {"label_th": "หุ้นวัฏจักร",           "emoji": "🔄", "desc": "กำไรขึ้นลงตามวัฏจักรเศรษฐกิจ/สินค้าโภคภัณฑ์ — ดูจุดของรอบมากกว่า P/E ต่ำเพียงอย่างเดียว"},
    "Turnaround":   {"label_th": "หุ้นฟื้นตัว",           "emoji": "🩹", "desc": "กิจการกำลังแก้ปัญหา/ฟื้นจากช่วงกำไรหดตัว — เน้นสภาพคล่อง หนี้ และหลักฐานการฟื้นจริง"},
    "Asset Play":   {"label_th": "หุ้นสินทรัพย์",         "emoji": "💰", "desc": "มูลค่าหลักอยู่ในสินทรัพย์ (ที่ดิน เงินสด ทรัพยากร มูลค่าทางบัญชี) ที่ตลาดอาจมองข้าม"},
}
_CONFIDENCE_RANK = {"สูง": 0, "ปานกลาง": 1, "ต่ำ": 2}


def _usable_snapshot(snap: dict) -> bool:
    """มีข้อมูลเชิงปริมาณพอจะจัดประเภทได้จริงไหม — กัน snapshot เปล่า (Yahoo โดน rate-limit /
    หุ้นเพิ่งเข้าตลาด) ไม่ให้ถูกจัดเป็น 'Fast Grower score 0' แบบเข้าใจผิด."""
    if not snap:
        return False
    if snap.get("market_cap") is None:
        return False
    return any(snap.get(k) is not None
               for k in ("pe", "pb", "roe", "earnings_growth", "revenue_growth"))


async def _symbol_category(symbol: str, name: str, gics_sector: str) -> dict | None:
    """จำแนกประเภท Peter Lynch ของหุ้นตัวเดียว. คืน None ถ้าข้อมูลพื้นฐานไม่พอ (ข้ามโดยตั้งใจ ไม่ใช่ error)."""
    try:
        snap = await get_fundamentals(symbol)
    except Exception:  # noqa: BLE001 — บริษัทเดียวพังไม่ควรล้มทั้งการสแกน
        return None
    if not _usable_snapshot(snap):
        return None

    profile = classify_peter_lynch(snap)
    scores = profile.get("scores") or []
    top_score = scores[0]["score"] if scores else 0
    secondary = profile.get("secondary") or []

    return {
        "symbol": symbol,
        "name": name,
        "gics_sector": gics_sector,                 # sector แบบ GICS (แกนจัดกลุ่มหลัก — เสถียร มาจากรายชื่อดัชนี)
        "industry": snap.get("industry"),           # อุตสาหกรรมย่อยตาม Yahoo (ไว้แสดง)
        "category": profile["primary"],
        "category_th": profile["primary_th"],
        "confidence": profile["confidence"],
        "top_score": top_score,
        "runner_up": (secondary[0]["type"] if secondary else None),
        "runner_up_th": (secondary[0]["type_th"] if secondary else None),
        "market_cap": snap.get("market_cap"),
        "metrics": profile.get("inputs", {}),       # earnings_growth_pct, revenue_growth_pct, dividend_yield_pct, pe, peg, pb, roe_pct ...
    }


async def _run_full_scan(concurrency: int) -> dict:
    universe = load_sp500()
    semaphore = asyncio.Semaphore(concurrency)

    async def worker(item: dict) -> dict | None:
        async with semaphore:
            return await _symbol_category(item["symbol"], item["name"], item["sector"])

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
    """สแกนสดได้เฉพาะตอนรันในเครื่อง (เช็คจากการมี .git) — บน HF Space เสิร์ฟแคชอย่างเดียว
    ไม่งั้น 1 คลิกบนเว็ป = ยิง Yahoo อีก 500 ครั้งบนคลาวด์ (โดน rate-limit + timeout)."""
    return (Path(__file__).resolve().parents[2] / ".git").exists()


def _confidence_key(row: dict) -> int:
    return _CONFIDENCE_RANK.get(row.get("confidence"), 1)


async def _format_result(cached: dict, *, category: str | None, sector: str | None,
                         min_market_cap: float | None, max_market_cap: float | None,
                         limit: int, rescanned: bool = False) -> dict:
    rows = cached["results"]

    # กรอง market cap + sector ก่อน แล้วค่อยนับ/จัดกลุ่ม (ให้จำนวนต่อประเภทตรงกับที่ผู้ใช้กรองไว้จริง)
    def _cap_ok(r: dict) -> bool:
        cap = r.get("market_cap")
        if min_market_cap is not None and (cap is None or cap < min_market_cap):
            return False
        if max_market_cap is not None and (cap is None or cap > max_market_cap):
            return False
        return True

    pool = [r for r in rows if _cap_ok(r)]
    if sector:
        pool = [r for r in pool if r.get("gics_sector") == sector]

    # นับจำนวนต่อประเภท + ตารางไขว้ ประเภท × sector (สำหรับตารางสรุป)
    sectors_present = sorted({r.get("gics_sector") or "—" for r in pool})
    matrix: dict[str, dict[str, int]] = {c: {} for c in CATEGORY_ORDER}
    counts: dict[str, int] = {c: 0 for c in CATEGORY_ORDER}
    for r in pool:
        cat = r["category"]
        sec = r.get("gics_sector") or "—"
        counts[cat] = counts.get(cat, 0) + 1
        matrix.setdefault(cat, {})
        matrix[cat][sec] = matrix[cat].get(sec, 0) + 1

    categories = [
        {
            "key": c,
            "label_th": CATEGORY_META[c]["label_th"],
            "emoji": CATEGORY_META[c]["emoji"],
            "desc": CATEGORY_META[c]["desc"],
            "count": counts.get(c, 0),
        }
        for c in CATEGORY_ORDER
    ]

    # รายชื่อหุ้น: กรองตามประเภทที่เลือก (ถ้ามี) แล้วเรียง confidence สูงก่อน ตามด้วยคะแนน แล้ว market cap
    candidates = [r for r in pool if (not category or r["category"] == category)]
    candidates.sort(key=lambda r: (_confidence_key(r), -(r.get("top_score") or 0),
                                   -(r.get("market_cap") or 0)))

    as_of = cached["as_of"]
    return {
        "as_of": as_of,
        "as_of_label": datetime.fromtimestamp(as_of, tz=timezone.utc).astimezone().strftime("%d %b %Y %H:%M"),
        "stale": (time.time() - as_of) > _SCAN_TTL,
        "rescanned": rescanned,
        "can_scan_live": can_scan_live(),
        "universe_count": cached["universe_count"],
        "success_count": cached["success_count"],
        "classified_count": len(pool),
        "criteria": {
            "category": category, "sector": sector, "limit": limit,
            "min_market_cap": min_market_cap, "max_market_cap": max_market_cap,
        },
        "categories": categories,
        "sectors": sectors_present,
        "matrix": matrix,
        "candidates": candidates[:limit],
        "candidate_count": len(candidates),
        "methodology": (
            "จำแนกด้วย heuristic เชิงปริมาณจาก snapshot ปัจจัยพื้นฐานล่าสุด (Yahoo) ตามแนว Peter Lynch — "
            "Fast Grower: กำไร/รายได้โตสูง ปันผลต่ำ · Stalwart: บริษัทใหญ่ โตปานกลางสม่ำเสมอ · "
            "Slow Grower: โตต่ำ ปันผลสูง · Cyclical: อยู่ในกลุ่มธุรกิจวัฏจักร · Turnaround: กำไรสุทธิติดลบ/หดตัวแรง · "
            "Asset Play: P/BV ต่ำ หรือธุรกิจอิงสินทรัพย์ (อสังหา/ธนาคาร/ทรัพยากร)"
        ),
        "coverage_note": (
            f"จัดประเภทได้ {cached['success_count']}/{cached['universe_count']} บริษัทใน S&P 500 "
            "(บริษัทที่ Yahoo ไม่มีข้อมูลพื้นฐานพอตอนสแกน จะไม่ปรากฏ — เป็นการข้ามโดยตั้งใจ ไม่ใช่ข้อผิดพลาด)"
        ),
        "disclaimer": (
            "การจำแนกนี้เป็นแนวทางจากตัวเลขล่าสุด ไม่ใช่การจัดหมวดแบบเด็ดขาด — หุ้นหนึ่งตัวอาจอยู่ได้มากกว่าหนึ่งกลุ่ม "
            "โดยเฉพาะ Cyclical / Turnaround / Asset Play ต้องอ่านลักษณะธุรกิจและวงจรกำไรประกอบ จึงแนบ 'ความมั่นใจ' ให้ทุกแถว "
            "เพื่อการศึกษา ไม่ใช่คำแนะนำการลงทุน"
        ),
    }


async def scan_sp500_lynch(
    *, category: str | None = None, sector: str | None = None, limit: int = 500,
    concurrency: int = 6, refresh: bool = False, auto: bool = False,
    min_market_cap: float | None = None, max_market_cap: float | None = None,
) -> dict:
    """refresh=True: สแกนสดใหม่เสมอ · auto=True: สแกนสดใหม่ให้เองเมื่อผลเก่ากว่า 24 ชม.
    (เฉพาะตอนรันในเครื่อง — บนเว็ปเสิร์ฟแคชเสมอ) · ทั้งคู่ False: อ่านแคชอย่างเดียว."""
    cached = None if refresh else _load_cache()
    outdated = cached is not None and (time.time() - cached["as_of"]) > _SCAN_TTL
    rescanned = False
    if cached is None or (auto and outdated and can_scan_live()):
        cached = await _run_full_scan(concurrency)
        _save_cache(cached)
        rescanned = True
    return await _format_result(cached, category=category, sector=sector, limit=limit,
                                min_market_cap=min_market_cap, max_market_cap=max_market_cap,
                                rescanned=rescanned)
