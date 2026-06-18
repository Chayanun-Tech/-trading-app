"""Engine สาย VI — รวม verdict ทุกด้าน → คะแนนคุณค่า (0-100) + เกรด A–F.

ลอกแนวคิดจาก engine.py (สายเทคนิค) แต่แกนเป็น good/poor แทน up/down
และ neutral → fair (ไม่ออกเสียง). 'ด้าน' ที่ confidence ต่ำจะมีน้ำหนักน้อยลงเอง.
"""
from __future__ import annotations

from app import knowledge_base as kb

_WEIGHTS = {s["id"]: float(s.get("weight", 1.0)) for s in kb.value_schools()}

DISCLAIMER = (
    "⚠️ ตารางนี้ประเมินคุณค่าหุ้นจากปัจจัยพื้นฐานหลายด้านเพื่อ 'ประกอบ' การตัดสินใจลงทุนระยะยาว "
    "ไม่ใช่คำแนะนำให้ซื้อขาย ไม่การันตีผล. ข้อมูลพื้นฐานมาจาก yfinance อาจคลาดเคลื่อน/ล่าช้า "
    "และเกณฑ์ยังไม่เทียบรายอุตสาหกรรม (sector) — ควรตรวจงบจริงและกระจายความเสี่ยงเสมอ"
)


def _grade(score: int) -> str:
    if score >= 78:
        return "A"
    if score >= 62:
        return "B"
    if score >= 48:
        return "C"
    if score >= 35:
        return "D"
    return "F"


def aggregate_value(verdicts: list[dict], weights: dict | None = None) -> dict:
    """ถ่วงน้ำหนัก verdict ทั้งหมดเป็นคะแนนคุณค่า + ประเมินความเป็นฉันทามติ."""
    wmap = {**_WEIGHTS, **(weights or {})}
    good_score = poor_score = 0.0
    good_w = poor_w = fair_w = 0.0
    for v in verdicts:
        w = float(wmap.get(v["id"], 1.0))
        contrib = w * (v["confidence"] / 100.0)
        if v["view"] == "good":
            good_score += contrib
            good_w += w
        elif v["view"] == "poor":
            poor_score += contrib
            poor_w += w
        else:
            fair_w += w

    total = good_score + poor_score
    score = 50 if total <= 0 else round(good_score / total * 100)

    directional_w = good_w + poor_w
    if directional_w <= 0:
        consensus = "ข้อมูลพื้นฐานไม่พอชี้ขาด — ทุกด้านเป็นกลาง ควรหาข้อมูลเพิ่ม"
    else:
        win = max(good_w, poor_w)
        share = win / directional_w
        if share >= 0.75:
            consensus = "ฉันทามติแข็งแรง — ปัจจัยพื้นฐานส่วนใหญ่ชี้ทางเดียวกัน"
        elif share >= 0.6:
            consensus = "ฉันทามติพอใช้ — มีด้านเด่นแต่ยังมีจุดอ่อนปน"
        else:
            consensus = "ปัจจัยผสม — มีทั้งจุดแข็งและจุดอ่อนพอกัน ควรพิจารณารายด้าน"

    return {"value_score": score, "quality_grade": _grade(score),
            "consensus_strength": consensus, "fair_weight": round(fair_w, 2)}


def _fallback_summary(verdicts: list[dict], agg: dict, snapshot: dict) -> str:
    goods = [v["name"] for v in verdicts if v["view"] == "good"]
    poors = [v["name"] for v in verdicts if v["view"] == "poor"]
    name = snapshot.get("long_name") or snapshot.get("symbol") or "หุ้นนี้"
    return (
        f"{name}: จุดแข็งด้าน {', '.join(goods) or '—'}; จุดอ่อนด้าน {', '.join(poors) or '—'}. "
        f"{agg['consensus_strength']} คะแนนคุณค่ารวม {agg['value_score']}/100 (เกรด {agg['quality_grade']}). "
        "สำหรับลงทุนระยะยาว เน้นธุรกิจที่จุดแข็งยั่งยืนและซื้อในราคาสมเหตุผล"
    )


def _key_metrics(snapshot: dict) -> dict:
    keys = ["pe", "forward_pe", "peg", "pb", "roe", "gross_margin", "operating_margin",
            "debt_to_equity", "current_ratio", "revenue_growth", "earnings_growth",
            "fcf_yield", "dividend_yield", "payout_ratio", "market_cap"]
    return {k: snapshot.get(k) for k in keys}


def build_value_report(verdicts: list[dict], snapshot: dict, *, ai_enabled: bool,
                       summary: str | None = None, weights: dict | None = None) -> dict:
    """ประกอบผลลัพธ์สุดท้ายตาม schema ValueReport."""
    agg = aggregate_value(verdicts, weights)
    return {
        "symbol": snapshot.get("symbol"),
        "long_name": snapshot.get("long_name"),
        "sector": snapshot.get("sector"),
        "verdicts": verdicts,
        "value_score": agg["value_score"],
        "quality_grade": agg["quality_grade"],
        "consensus_strength": agg["consensus_strength"],
        "summary": summary or _fallback_summary(verdicts, agg, snapshot),
        "key_metrics": _key_metrics(snapshot),
        "ai_enabled": ai_enabled,
        "disclaimer": DISCLAIMER,
    }
