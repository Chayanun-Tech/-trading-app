"""Engine รวมผลทุกศาสตร์ → ตารางความน่าจะเป็น + ถ่วงน้ำหนักเป็นโอกาสขึ้น/ลง.

หัวใจของแอป: ไม่ฟันธงเอง แต่ให้แต่ละศาสตร์โหวต แล้วถ่วงน้ำหนักตาม registry
โดยยึด 'จิตวิทยากราฟ' เป็นตัวสังเคราะห์สุดท้าย.
"""
from __future__ import annotations

from app import knowledge_base as kb

_WEIGHTS = {s["id"]: float(s.get("weight", 1.0)) for s in kb.schools()}

DISCLAIMER = (
    "⚠️ ตารางนี้เป็นการประเมินตามศาสตร์เทคนิคหลายสำนักเพื่อ 'ประกอบ' การตัดสินใจ "
    "ไม่ใช่คำแนะนำให้ซื้อขาย ไม่การันตีผล ตลาดมีความไม่แน่นอนเสมอ "
    "โปรดบริหารความเสี่ยง (แนะนำเสี่ยงไม่เกิน 1–2% ต่อไม้) และตัดสินใจด้วยตนเอง"
)


def _bias_label(up_prob: int) -> str:
    if up_prob >= 75:
        return "Bullish (ขึ้น) — ค่อนข้างชัด"
    if up_prob >= 60:
        return "Bullish (ขึ้น) — เอียงขึ้น"
    if up_prob > 55:
        return "เอียงขึ้นเล็กน้อย"
    if up_prob >= 45:
        return "Neutral (ไม่ชัด) — รอความชัดเจน"
    if up_prob >= 40:
        return "เอียงลงเล็กน้อย"
    if up_prob >= 25:
        return "Bearish (ลง) — เอียงลง"
    return "Bearish (ลง) — ค่อนข้างชัด"


def aggregate(verdicts: list[dict], weights: dict | None = None) -> dict:
    """ถ่วงน้ำหนัก verdict ทั้งหมดเป็นคะแนนขึ้น/ลง + ประเมินความเป็นฉันทามติ.

    weights: override น้ำหนักรายศาสตร์ {id: weight}; ถ้าไม่ให้ ใช้ค่าจาก registry.
    """
    wmap = {**_WEIGHTS, **(weights or {})}
    up_score = down_score = 0.0
    up_w = down_w = neutral_w = 0.0
    for v in verdicts:
        w = float(wmap.get(v["id"], 1.0))
        contrib = w * (v["confidence"] / 100.0)
        if v["view"] == "up":
            up_score += contrib
            up_w += w
        elif v["view"] == "down":
            down_score += contrib
            down_w += w
        else:
            neutral_w += w

    total = up_score + down_score
    if total <= 0:
        up_prob = 50
    else:
        up_prob = round(up_score / total * 100)
    down_prob = 100 - up_prob

    # ความเป็นฉันทามติ: ดูสัดส่วนน้ำหนักฝั่งที่ชนะเทียบฝั่งตรงข้าม
    directional_w = up_w + down_w
    if directional_w <= 0:
        consensus = "ไม่มีสัญญาณชัดเจน (ทุกศาสตร์เป็นกลาง) — ควรรอ"
    else:
        win = max(up_w, down_w)
        share = win / directional_w
        if share >= 0.75:
            consensus = "ฉันทามติแข็งแรง — ศาสตร์ส่วนใหญ่ชี้ทางเดียวกัน"
        elif share >= 0.6:
            consensus = "ฉันทามติพอใช้ — มีทิศทางเด่นแต่ยังมีศาสตร์ค้าน"
        else:
            consensus = "ขัดแย้งกันสูง — ศาสตร์แบ่งสองฝั่ง ควรลดขนาด/รอยืนยัน"

    return {
        "up_probability": up_prob,
        "down_probability": down_prob,
        "bias": _bias_label(up_prob),
        "consensus_strength": consensus,
        "neutral_weight": round(neutral_w, 2),
    }


def _fallback_psychology(verdicts: list[dict], agg: dict) -> str:
    ups = [v["name"] for v in verdicts if v["view"] == "up"]
    downs = [v["name"] for v in verdicts if v["view"] == "down"]
    return (
        f"จิตวิทยากราฟโดยรวม: ฝั่งขึ้นได้แรงหนุนจาก {', '.join(ups) or '—'}; "
        f"ฝั่งลงจาก {', '.join(downs) or '—'}. {agg['consensus_strength']} "
        f"โอกาสขึ้นประเมินที่ {agg['up_probability']}% เทียบลง {agg['down_probability']}%. "
        "ยึดเทรนด์หลักเป็นที่ตั้ง และรอจุดได้เปรียบ (แนวรับในขาขึ้น/แนวต้านในขาลง) ก่อนเข้า"
    )


def build_report(verdicts: list[dict], *, input_mode: str, ai_enabled: bool,
                 symbol: str | None = None, timeframe: str | None = None,
                 psychology_summary: str | None = None,
                 suggested_plan: str | None = None,
                 weights: dict | None = None) -> dict:
    """ประกอบผลลัพธ์สุดท้ายตาม schema MultiSchoolReport."""
    agg = aggregate(verdicts, weights)
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "input_mode": input_mode,
        "verdicts": verdicts,
        "up_probability": agg["up_probability"],
        "down_probability": agg["down_probability"],
        "bias": agg["bias"],
        "consensus_strength": agg["consensus_strength"],
        "psychology_summary": psychology_summary or _fallback_psychology(verdicts, agg),
        "suggested_plan": suggested_plan,
        "ai_enabled": ai_enabled,
        "disclaimer": DISCLAIMER,
    }
