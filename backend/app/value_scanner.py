"""S&P 500 fair-value / undervalue scanner (สแกนหาหุ้นที่ราคาต่ำกว่ามูลค่ายุติธรรมตาม sector).

ต่างจาก revenue_scanner.py (ที่เทียบราคากับ "แนวโน้มรายได้" อย่างเดียว) — โมดูลนี้ใช้เครื่องประเมิน
มูลค่าแบบ sector-aware ตัวเดียวกับกล่อง "ราคายุติธรรม" ในแท็บ 📈 โมเดลรายได้:
  จำแนก sector (SIC ของ SEC → heuristic จากงบ) → เลือก metric หลักของกลุ่มนั้น
  (ธนาคาร/ประกัน = P/B · REIT = P/FFO · ลงทุนหนัก = P/FCF เฉลี่ย 5 ปี · วัฏจักร = Shiller P/E ·
   ทั่วไป = P/E) → ราคายุติธรรม = metric ต่อหุ้นล่าสุด × ตัวคูณ median ที่ตลาดให้ ~2 ปีล่าสุด
  → upside % = (ราคายุติธรรม − ราคาปัจจุบัน) / ราคาปัจจุบัน

กลุ่มที่ประเมินด้วย multiple ไม่ได้ (biotech ก่อนมีรายได้ / เติบโตยังไม่ทำกำไร) จะไม่มีราคายุติธรรม
และถูกข้ามไปโดยตั้งใจ — ไม่ใช่ error.

เหมือน revenue_scanner: การสแกนเต็ม 500 บริษัทใช้เวลาหลายนาที ควรรันในเครื่องแล้ว commit ไฟล์แคช
(data_sp500_value_scan.json) ขึ้น repo — เว็ป (HF Space) แค่เสิร์ฟแคชทันที ไม่สแกนสดบนคลาวด์.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from app import revenue_model, sector_profile
from app.revenue_scanner import _market_cap_lookup, load_sp500

_SCAN_CACHE = Path(__file__).resolve().parent / "data_sp500_value_scan.json"
_SCAN_TTL = 24 * 3600  # ผลเก่ากว่านี้ถือว่า stale (ยังเสิร์ฟได้ แต่ขึ้นเตือนให้สแกนใหม่)


async def _symbol_fair_value(symbol: str, name: str, sector: str) -> dict | None:
    """ประเมินราคายุติธรรมตาม sector ของหุ้นตัวเดียว. คืน None ถ้าประเมินไม่ได้
    (ข้อมูล SEC ไม่พอ / กลุ่มที่ใช้ multiple ไม่ได้ / ราคาย้อนหลังดึงไม่ได้)."""
    try:
        d = await revenue_model.sector_fair_value(symbol)
    except Exception:  # noqa: BLE001 — บริษัทเดียวพังไม่ควรล้มทั้งการสแกน
        return None

    fv = d.get("fair_value")
    if not fv or not fv.get("fair_price") or "upside_pct" not in fv:
        return None

    return {
        "symbol": symbol,
        "name": name,
        "gics_sector": sector,                       # sector แบบ GICS จากรายชื่อดัชนี (ไว้แสดง/กรองคร่าว ๆ)
        "profile_key": d["profile_key"],             # กลุ่มที่เครื่องประเมินใช้จริง (bank/reit/cyclical/...)
        "profile_label": d["sector_label"],
        "sector_source": d["sector_source"],         # sic = จำแนกจากรหัส SEC, financials = เดาจากงบ
        "basis": fv["basis"],                        # เช่น "P/E median", "P/B median", "P/FFO median"
        "per_share": fv["per_share"],
        "median_multiple": fv["median_multiple"],
        "fair_price": fv["fair_price"],
        "low": fv["low"],
        "high": fv["high"],
        "price": fv["current_price"],
        "upside_pct": fv["upside_pct"],
        # เก็บเฉพาะคำเตือนที่เจาะจงหุ้นตัวนี้ (ข้อมูลไม่ครบ/ฐานเพี้ยน) — คำเตือนประจำกลุ่มธุรกิจ
        # เหมือนกันทุกตัวในกลุ่มอยู่แล้ว ไปอ่านได้ในแท็บโมเดลรายได้ ไม่ต้องเก็บซ้ำ 500 รอบในไฟล์แคช
        "warnings": d.get("data_warnings", []),
    }


async def _run_full_scan(concurrency: int) -> dict:
    universe = load_sp500()
    semaphore = asyncio.Semaphore(concurrency)

    async def worker(item: dict) -> dict | None:
        async with semaphore:
            return await _symbol_fair_value(item["symbol"], item["name"], item["sector"])

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
    """สแกนสดได้เฉพาะตอนรันในเครื่อง — เช็คจากการมี .git (โคลนจาก repo จริง) ซึ่งเป็นสัญญาณเดียว
    กับที่ปุ่ม "ดันขึ้นเว็ป" ใช้. บน HF Space (container ไม่มี .git) ต้องเสิร์ฟแคชอย่างเดียว
    ไม่งั้นผู้เข้าเว็ปคนหนึ่งกดปุ่มเดียว = ยิง SEC/Yahoo อีก 500 ครั้งบนคลาวด์ (โดน rate-limit + timeout)."""
    return (Path(__file__).resolve().parents[2] / ".git").exists()


def profile_options() -> list[dict]:
    """รายชื่อกลุ่มธุรกิจที่ใช้กรองใน UI (มาจาก PROFILES จริง ไม่ต้อง hardcode ฝั่งหน้าเว็ป)."""
    return [{"key": k, "label": p["label"]} for k, p in sector_profile.PROFILES.items()]


async def _format_result(cached: dict, min_upside_pct: float, limit: int,
                         min_market_cap: float | None, max_market_cap: float | None,
                         profile: str | None, rescanned: bool = False) -> dict:
    candidates = [r for r in cached["results"] if r["upside_pct"] >= min_upside_pct]
    if profile:
        candidates = [r for r in candidates if r["profile_key"] == profile]
    candidates.sort(key=lambda r: -r["upside_pct"])

    cap_map = await _market_cap_lookup()
    enriched = []
    for r in candidates:
        cap = cap_map.get(r["symbol"])
        # ไม่มี market cap ให้เทียบ = กรองทิ้งเมื่อผู้ใช้ตั้งกรอบไว้ (เหมือน revenue_scanner)
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
        "rescanned": rescanned,          # True = รอบนี้สแกนสดใหม่ให้อัตโนมัติ (ไม่ได้อ่านแคชเก่า)
        "can_scan_live": can_scan_live(),
        "universe_count": cached["universe_count"],
        "success_count": cached["success_count"],
        "criteria": {
            "min_upside_pct": min_upside_pct, "limit": limit, "profile": profile,
            "min_market_cap": min_market_cap, "max_market_cap": max_market_cap,
        },
        "profiles": profile_options(),
        "candidates": candidates[:limit],
        "candidate_count": len(candidates),
        "methodology": (
            "ราคายุติธรรม = (metric หลักของกลุ่มธุรกิจต่อหุ้นล่าสุด) × (ตัวคูณ median ที่ตลาดให้ ~2 ปีล่าสุด) "
            "— ธนาคาร/ประกันใช้ P/B · REIT ใช้ P/FFO · ลงทุนหนักใช้ P/FCF เฉลี่ย 5 ปี · วัฏจักรใช้ EPS เฉลี่ย ~10 ปี (Shiller) · "
            "ทั่วไปใช้ P/E · ช่วงราคา = ตัวคูณ p25–p75 · upside % = (ราคายุติธรรม − ราคาปัจจุบัน) ÷ ราคาปัจจุบัน"
        ),
        "coverage_note": (
            f"ประเมินได้ {cached['success_count']}/{cached['universe_count']} บริษัทใน S&P 500 "
            "(กลุ่มที่ประเมินด้วยตัวคูณไม่ได้ เช่น ไบโอเทคก่อนมีรายได้ หรือบริษัทที่ metric หลักติดลบ/ข้อมูลน้อยเกินไป "
            "จะไม่ปรากฏในผล — เป็นการข้ามโดยตั้งใจ ไม่ใช่ข้อผิดพลาด)"
        ),
        "disclaimer": (
            "ตัวคูณ median สะท้อน 'ราคาที่ตลาดเคยให้หุ้นตัวนี้ 2 ปีล่าสุด' ไม่ใช่มูลค่าที่แท้จริง — "
            "หุ้นที่ตลาดเคยให้ราคาแพงเกินจริงมาตลอดจะดู 'ถูก' ในตารางนี้ และหุ้นที่พื้นฐานเสื่อมถาวรก็เช่นกัน "
            "ควรเปิดแท็บ 📈 โมเดลรายได้ ดูรายละเอียด/คำเตือนของหุ้นนั้นต่อเสมอ — เพื่อการศึกษา ไม่ใช่คำแนะนำการลงทุน"
        ),
    }


async def scan_sp500_fair_value(
    *, min_upside_pct: float = 20.0, limit: int = 40, concurrency: int = 6, refresh: bool = False,
    auto: bool = False, min_market_cap: float | None = None, max_market_cap: float | None = None,
    profile: str | None = None,
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
    return await _format_result(cached, min_upside_pct, limit, min_market_cap, max_market_cap,
                                profile, rescanned=rescanned)
