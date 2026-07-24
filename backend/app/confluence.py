"""Confluence view — รวมผลสแกนทุกเลนส์เป็นตารางเดียว แล้วนับว่า "กี่เลนส์เห็นตรงกัน".

ปัญหาที่แก้: แต่ละสแกนตอบคนละคำถาม แต่ UI เดิมแยกเป็นคนละแท็บ ทำให้ต้องเปิดสามหน้าแล้วเทียบเอง
ซึ่งนอกจากเหนื่อยแล้วยังทำให้ "หุ้นที่โผล่หัวตารางในเลนส์เดียว" ดูน่าเชื่อกว่าความเป็นจริง

เลนส์ที่รวมตอนนี้:
  📉 รายได้   (revenue_scanner)  — ราคาต่ำกว่าที่แนวโน้มรายได้บ่งชี้
  💎 มูลค่า   (value_scanner)    — ราคาต่ำกว่าราคายุติธรรมตามตัวคูณของกลุ่มธุรกิจ
  📐 EMA      (ema_scanner)      — ราคาลงมาอยู่ในโซนเส้นค่าเฉลี่ยระยะยาว

โมดูลนี้ *ไม่สแกนเอง* — อ่านไฟล์แคชที่แต่ละสแกนสร้างไว้แล้ว join ด้วย symbol จึงเร็วมาก
(ไม่ยิงเน็ตเลยนอกจาก market cap) แต่ก็แปลว่าเลนส์ไหนยังไม่เคยสแกน เลนส์นั้นจะขึ้นว่า "ยังไม่มีข้อมูล"
ไม่ใช่ "ไม่ผ่าน" — สองอย่างนี้ต่างกันมากและห้ามปนกัน

หมายเหตุเรื่องเลนส์ "เงินสด": ยังไม่มีสแกนแยกสำหรับ P/FCF ทั้ง 500 บริษัท — ตอนนี้เลนส์เงินสด
ถูกครอบคลุมบางส่วนผ่านเลนส์มูลค่า เฉพาะกลุ่มที่ใช้ฐาน P/FCF (ธุรกิจลงทุนหนัก) ซึ่งดูได้จากคอลัมน์ "ฐาน"
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from app import ema_scanner, revenue_scanner, value_scanner
from app.sector_profile import multiple_sanity

# ระยะห่างจาก EMA200 ที่ถือว่า "อยู่ในโซนเส้น" สำหรับหน้ารวมเลนส์
_EMA_ZONE_PCT = 3.0


def _lens_meta(cache: dict | None, ttl: int) -> dict:
    """สถานะของเลนส์หนึ่ง ๆ (มีข้อมูลไหม เก่าแค่ไหน) — แสดงตรง ๆ ให้ผู้ใช้รู้ว่าเชื่อได้แค่ไหน."""
    if not cache:
        return {"available": False, "as_of": None, "as_of_label": None, "stale": True,
                "count": 0}
    as_of = cache.get("as_of") or 0
    return {
        "available": True,
        "as_of": as_of,
        "as_of_label": datetime.fromtimestamp(as_of, tz=timezone.utc).astimezone().strftime("%d %b %Y %H:%M"),
        "stale": (time.time() - as_of) > ttl,
        "count": cache.get("success_count") or len(cache.get("results") or []),
    }


def _index(cache: dict | None) -> dict[str, dict]:
    return {r["symbol"]: r for r in (cache or {}).get("results", []) if r.get("symbol")}


def _revenue_lens(row: dict | None, max_gap_pct: float) -> dict:
    """ราคาต่ำกว่า 'ราคาตามรายได้' ถึงเกณฑ์ไหม."""
    if not row:
        return {"status": "na", "detail": "ไม่มีข้อมูล"}
    gap = row.get("gap_pct")
    if gap is None:
        return {"status": "na", "detail": "คำนวณไม่ได้"}
    return {
        "status": "pass" if gap <= max_gap_pct else "fail",
        "value": gap,
        "detail": f"ราคาต่ำกว่าที่รายได้บ่งชี้ {abs(gap):.0f}%" if gap < 0 else f"ราคาสูงกว่าที่รายได้บ่งชี้ {gap:.0f}%",
    }


def _value_lens(row: dict | None, min_upside_pct: float) -> dict:
    """ราคาต่ำกว่าราคายุติธรรมถึงเกณฑ์ไหม + ฐานที่ใช้ตั้งราคายุติธรรมเชื่อได้แค่ไหน."""
    if not row:
        return {"status": "na", "detail": "ประเมินไม่ได้ (กลุ่มที่ใช้ตัวคูณไม่ได้ / ข้อมูลไม่พอ)"}
    upside = row.get("upside_pct")
    if upside is None:
        return {"status": "na", "detail": "ประเมินไม่ได้"}
    sanity = multiple_sanity(row.get("basis"), row.get("median_multiple"))
    if upside < min_upside_pct:
        status = "fail"
    elif sanity["level"] == "extreme":
        # ผ่านเกณฑ์ตัวเลข แต่ฐานเฟ้อเกินกว่าจะนับเป็นเสียงยืนยัน — ไม่ใช่ผ่าน ไม่ใช่ไม่ผ่าน
        status = "flag"
    else:
        status = "pass"
    return {
        "status": status,
        "value": upside,
        "basis": row.get("basis"),
        "multiple": row.get("median_multiple"),
        "multiple_level": sanity["level"],
        "detail": sanity["note"] or f"{row.get('basis')} {row.get('median_multiple')}x → upside {upside:+.0f}%",
    }


def _ema_lens(row: dict | None, zone_pct: float) -> dict:
    """ราคาอยู่ในโซน EMA200 ไหม (และเทรนด์ใหญ่ยังชี้ขึ้นอยู่หรือเปล่า)."""
    if not row:
        return {"status": "na", "detail": "ไม่มีข้อมูลราคา"}
    dist = row.get("dist200_pct")
    if dist is None:
        return {"status": "na", "detail": "EMA200 ยังไม่นิ่ง (ราคาย้อนหลังไม่พอ)"}
    slope = row.get("ema200_slope_pct")
    trend = ema_scanner._trend_of(slope)      # ใช้เกณฑ์ความชันเดียวกับหน้าสแกน EMA
    if abs(dist) > zone_pct:
        status = "fail"
        detail = f"ห่าง EMA200 {dist:+.1f}% (นอกโซน ±{zone_pct:.0f}%)"
    elif trend == "down":
        # อยู่ในโซนจริง แต่เส้นชี้ลง = เทรนด์ใหญ่เสียแล้ว ไม่ควรนับเป็นเสียงสนับสนุน
        status = "flag"
        detail = f"อยู่ในโซน EMA200 ({dist:+.1f}%) แต่เส้นชี้ลง {slope:+.1f}% — เทรนด์ใหญ่เสีย"
    else:
        status = "pass"
        detail = f"อยู่ในโซน EMA200 ({dist:+.1f}%)" + (f" · เส้นชี้ขึ้น {slope:+.1f}%" if trend == "up" else "")
    return {"status": status, "value": dist, "slope_pct": slope,
            "touches_2y": row.get("touches_2y"), "days_since_touch": row.get("days_since_touch"),
            "detail": detail}


_VERDICTS = {
    3: ("🟢🟢🟢", "ทั้งสามเลนส์เห็นตรงกัน — สัญญาณแข็งแรงที่สุดเท่าที่เครื่องมือชุดนี้ให้ได้"),
    2: ("🟢🟢⚪", "สองเลนส์เห็นตรงกัน — น่าสนใจพอที่จะไปอ่านงบต่อ"),
    1: ("🟢⚪⚪", "เลนส์เดียว — ยังไม่ใช่คำตอบ เป็นแค่รายการเฝ้าดู"),
    0: ("⚪⚪⚪", "ไม่มีเลนส์ไหนผ่านเกณฑ์"),
}


async def scan_confluence(
    *, min_agree: int = 2, max_gap_pct: float = -15.0, min_upside_pct: float = 20.0,
    ema_zone_pct: float = _EMA_ZONE_PCT, limit: int = 60,
    min_market_cap: float | None = None, max_market_cap: float | None = None,
) -> dict:
    """รวมผลแคชของทุกเลนส์ → ตารางเดียว เรียงตามจำนวนเลนส์ที่เห็นตรงกัน."""
    rev_cache = revenue_scanner._load_cache()
    val_cache = value_scanner._load_cache()
    ema_cache = ema_scanner._load_cache()

    rev_idx, val_idx, ema_idx = _index(rev_cache), _index(val_cache), _index(ema_cache)
    universe = {item["symbol"]: item for item in revenue_scanner.load_sp500()}

    rows = []
    for symbol, meta in universe.items():
        lenses = {
            "revenue": _revenue_lens(rev_idx.get(symbol), max_gap_pct),
            "value": _value_lens(val_idx.get(symbol), min_upside_pct),
            "ema": _ema_lens(ema_idx.get(symbol), ema_zone_pct),
        }
        agree = sum(1 for v in lenses.values() if v["status"] == "pass")
        if agree < min_agree:
            continue
        flags = sum(1 for v in lenses.values() if v["status"] == "flag")
        available = sum(1 for v in lenses.values() if v["status"] != "na")
        badge, verdict = _VERDICTS.get(agree, _VERDICTS[0])
        # ราคาปัจจุบันเอาจากเลนส์ไหนก็ได้ที่มี — แต่ละแคชสแกนคนละเวลา จึงบอกที่มาไว้ด้วย
        price = (ema_idx.get(symbol) or {}).get("price") \
            or (val_idx.get(symbol) or {}).get("price") \
            or (rev_idx.get(symbol) or {}).get("price_now")
        rows.append({
            "symbol": symbol,
            "name": meta["name"],
            "sector": meta["sector"],
            "price": price,
            "agree_count": agree,
            "flag_count": flags,
            "available_lenses": available,
            "badge": badge,
            "verdict": verdict,
            "lenses": lenses,
        })

    # เรียง: เห็นตรงกันมากสุดก่อน → ธงเตือนน้อยกว่าก่อน → upside สูงกว่าก่อน
    rows.sort(key=lambda r: (-r["agree_count"], r["flag_count"],
                             -(r["lenses"]["value"].get("value") or -999)))

    cap_map = await revenue_scanner._market_cap_lookup()
    enriched = []
    for r in rows:
        cap = cap_map.get(r["symbol"])
        if min_market_cap is not None and (cap is None or cap < min_market_cap):
            continue
        if max_market_cap is not None and (cap is None or cap > max_market_cap):
            continue
        enriched.append({**r, "market_cap": cap})

    lens_meta = {
        "revenue": {**_lens_meta(rev_cache, revenue_scanner._SCAN_TTL), "label": "📉 รายได้"},
        "value": {**_lens_meta(val_cache, value_scanner._SCAN_TTL), "label": "💎 มูลค่ายุติธรรม"},
        "ema": {**_lens_meta(ema_cache, ema_scanner._SCAN_TTL), "label": "📐 EMA200"},
    }
    missing = [m["label"] for m in lens_meta.values() if not m["available"]]

    return {
        "lenses": lens_meta,
        "missing_lenses": missing,
        "can_scan_live": revenue_scanner.can_scan_live(),
        "criteria": {
            "min_agree": min_agree, "max_gap_pct": max_gap_pct, "min_upside_pct": min_upside_pct,
            "ema_zone_pct": ema_zone_pct, "limit": limit,
            "min_market_cap": min_market_cap, "max_market_cap": max_market_cap,
        },
        "candidates": enriched[:limit],
        "candidate_count": len(enriched),
        "methodology": (
            "แต่ละเลนส์ให้ผลอิสระจากแคชของสแกนตัวเอง แล้วนับว่ากี่เลนส์ 'ผ่านเกณฑ์' พร้อมกัน · "
            f"📉 รายได้ผ่านเมื่อราคาต่ำกว่าที่รายได้บ่งชี้ ≥ {abs(max_gap_pct):.0f}% · "
            f"💎 มูลค่าผ่านเมื่อ upside ≥ {min_upside_pct:.0f}% และตัวคูณฐานไม่สูงผิดปกติ · "
            f"📐 EMA ผ่านเมื่อราคาอยู่ในโซน ±{ema_zone_pct:.0f}% ของ EMA200 และเส้นไม่ได้ชี้ลง"
        ),
        "legend": (
            "🟢 ผ่านเกณฑ์ · 🔶 เข้าเกณฑ์ตัวเลขแต่มีข้อควรระวัง (ฐานประเมินเฟ้อ / เทรนด์ใหญ่เสีย) "
            "ไม่นับเป็นเสียงยืนยัน · ⚪ ไม่ผ่าน · — ไม่มีข้อมูลสำหรับเลนส์นั้น (ไม่เท่ากับ 'ไม่ผ่าน')"
        ),
        "disclaimer": (
            "การที่หลายเลนส์เห็นตรงกันแปลว่า 'วิธีวัดหลายแบบชี้ไปทางเดียวกัน' ไม่ได้แปลว่าถูกต้อง — "
            "ทุกเลนส์ใช้ข้อมูลอดีตชุดเดียวกัน จึงพลาดพร้อมกันได้เมื่อธุรกิจเปลี่ยนเชิงโครงสร้าง "
            "(คู่แข่งใหม่ กฎเกณฑ์เปลี่ยน เทคโนโลยีแทนที่) ซึ่งเป็นสิ่งที่ตัวเลขย้อนหลังมองไม่เห็นเลย "
            "ใช้เป็นจุดเริ่มค้นคว้า ไม่ใช่คำแนะนำการลงทุน"
        ),
    }
