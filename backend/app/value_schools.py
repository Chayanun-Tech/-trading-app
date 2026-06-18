"""Python evaluators สาย VI — ตัดสิน 'ด้าน' ปัจจัยพื้นฐานจากอัตราส่วนการเงิน.

แต่ละฟังก์ชันคืน verdict dict (ตรงกับ schema ValueVerdict) = หนึ่งแถวในตาราง VI.
ด้านเชิงคุณภาพ (moat/management/business_quality) ประเมินด้วย Claude ใน fundamentals_ai.py.

หมายเหตุ: เกณฑ์เป็นค่าตายตัว ยังไม่เทียบรายอุตสาหกรรม (sector) — เป็นข้อจำกัดของ v1.
ทุก evaluator ต้องทนค่า None (yfinance ดึงบางฟิลด์ไม่ได้เป็นช่วง ๆ).
"""
from __future__ import annotations

from app import knowledge_base as kb

_META = {s["id"]: (s["display_name"], s["category"]) for s in kb.value_schools()}


def _verdict(school_id: str, view: str, signal: str, confidence: int,
             rationale: str, metric_value: str | None = None) -> dict:
    name, category = _META.get(school_id, (school_id, "valuation"))
    return {
        "id": school_id, "name": name, "category": category,
        "view": view, "signal": signal,
        "confidence": max(0, min(100, int(confidence))),
        "metric_value": metric_value,
        "rationale": rationale, "evaluator": "python",
    }


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def _num(x: float | None, nd: int = 2) -> str:
    return "—" if x is None else f"{x:.{nd}f}"


def _eval_valuation(f: dict) -> dict:
    pe, peg, pb = f.get("pe"), f.get("peg"), f.get("pb")
    mv = f"P/E={_num(pe)}, PEG={_num(peg)}, P/B={_num(pb)}"
    if pe is None and peg is None and pb is None:
        return _verdict("valuation", "fair", "no-data", 15,
                        "ไม่มีข้อมูลมูลค่า (อาจขาดทุนหรือ provider ไม่ส่งค่า)", mv)
    # ถูก: PEG ต่ำ หรือ P/E ต่ำ
    if (peg is not None and 0 < peg < 1) or (pe is not None and 0 < pe < 15):
        return _verdict("valuation", "good", "undervalued", 70,
                        f"ราคาดูไม่แพงเทียบกำไร/การเติบโต ({mv}) — PEG<1 หรือ P/E<15", mv)
    # แพง/ไม่มีกำไร
    if (pe is not None and pe <= 0) or (pe is not None and pe > 30) or (peg is not None and peg > 2):
        why = "ขาดทุน/ไม่มีกำไร" if (pe is not None and pe <= 0) else "ราคาแพงเทียบกำไร"
        return _verdict("valuation", "poor", "expensive", 60,
                        f"{why} ({mv}) — ต้องเติบโตสูงจริงจึงคุ้ม", mv)
    return _verdict("valuation", "fair", "fair-value", 45,
                    f"มูลค่าอยู่ระดับสมเหตุผลปานกลาง ({mv})", mv)


def _eval_profitability(f: dict) -> dict:
    roe, gm, om = f.get("roe"), f.get("gross_margin"), f.get("operating_margin")
    mv = f"ROE={_pct(roe)}, Gross={_pct(gm)}, Op={_pct(om)}"
    if roe is None and om is None:
        return _verdict("profitability", "fair", "no-data", 15, "ไม่มีข้อมูลความสามารถทำกำไร", mv)
    if roe is not None and roe >= 0.15:
        return _verdict("profitability", "good", "strong", 70,
                        f"ทำกำไรจากทุนได้ดี (ROE {_pct(roe)} ≥ 15%)", mv)
    if (roe is not None and roe < 0.05) or (om is not None and om < 0):
        return _verdict("profitability", "poor", "weak", 60,
                        f"ความสามารถทำกำไรอ่อน (ROE {_pct(roe)} / Op margin {_pct(om)})", mv)
    return _verdict("profitability", "fair", "moderate", 45,
                    f"ทำกำไรได้ปานกลาง ({mv})", mv)


def _eval_financial_health(f: dict) -> dict:
    de, cr = f.get("debt_to_equity"), f.get("current_ratio")
    mv = f"D/E={_num(de)}, Current={_num(cr)}"
    if de is None and cr is None:
        return _verdict("financial_health", "fair", "no-data", 15, "ไม่มีข้อมูลหนี้/สภาพคล่อง", mv)
    risky = (de is not None and de > 2) or (cr is not None and cr < 1)
    healthy = (de is None or de < 1) and (cr is None or cr > 1.5)
    if risky:
        return _verdict("financial_health", "poor", "leveraged", 60,
                        f"งบดุลตึง (หนี้สูง/สภาพคล่องน้อย: {mv})", mv)
    if healthy and (de is not None or cr is not None):
        return _verdict("financial_health", "good", "solid", 68,
                        f"งบดุลแข็งแรง (หนี้ต่ำ/สภาพคล่องดี: {mv})", mv)
    return _verdict("financial_health", "fair", "moderate", 45,
                    f"งบดุลระดับกลาง ({mv})", mv)


def _eval_growth(f: dict) -> dict:
    rg, eg = f.get("revenue_growth"), f.get("earnings_growth")
    mv = f"Rev={_pct(rg)}, EPS={_pct(eg)}"
    if rg is None and eg is None:
        return _verdict("growth", "fair", "no-data", 15, "ไม่มีข้อมูลการเติบโต", mv)
    best = max([g for g in (rg, eg) if g is not None], default=None)
    worst = min([g for g in (rg, eg) if g is not None], default=None)
    if worst is not None and worst < 0:
        return _verdict("growth", "poor", "shrinking", 58,
                        f"กิจการหดตัว ({mv}) — ต้องหาสาเหตุก่อนลงทุน", mv)
    if best is not None and best >= 0.10:
        return _verdict("growth", "good", "growing", 65,
                        f"เติบโตดี ({mv} ≥ 10%)", mv)
    return _verdict("growth", "fair", "flat", 45, f"เติบโตช้า/ทรงตัว ({mv})", mv)


def _eval_cash_flow(f: dict) -> dict:
    fcf, fy = f.get("fcf"), f.get("fcf_yield")
    mv = f"FCF yield={_pct(fy)}"
    if fcf is None and fy is None:
        return _verdict("cash_flow", "fair", "no-data", 15, "ไม่มีข้อมูลกระแสเงินสดอิสระ", mv)
    if fcf is not None and fcf <= 0:
        return _verdict("cash_flow", "poor", "cash-burn", 58,
                        "กระแสเงินสดอิสระติดลบ — เผาเงิน ต้องระวังการเพิ่มทุน/ก่อหนี้", mv)
    if fy is not None and fy >= 0.05:
        return _verdict("cash_flow", "good", "cash-rich", 68,
                        f"สร้างเงินสดจริงได้ดี (FCF yield {_pct(fy)} ≥ 5%)", mv)
    return _verdict("cash_flow", "fair", "positive", 48,
                    f"กระแสเงินสดเป็นบวกแต่ไม่สูง ({mv})", mv)


def _eval_dividend(f: dict) -> dict:
    dy, po = f.get("dividend_yield"), f.get("payout_ratio")
    mv = f"Yield={_pct(dy)}, Payout={_pct(po)}"
    if not dy:
        return _verdict("dividend", "fair", "no-dividend", 30,
                        "ไม่จ่ายปันผล — ปกติสำหรับหุ้นเติบโตที่นำกำไรไปลงทุนต่อ", mv)
    if po is not None and po > 1:
        return _verdict("dividend", "poor", "unsustainable", 55,
                        f"จ่ายปันผลเกินกำไร (payout {_pct(po)} > 100%) — เสี่ยงไม่ยั่งยืน", mv)
    if po is None or po < 0.8:
        return _verdict("dividend", "good", "sustainable", 60,
                        f"จ่ายปันผล {_pct(dy)} และดูยั่งยืน (payout {_pct(po)})", mv)
    return _verdict("dividend", "fair", "moderate", 45,
                    f"จ่ายปันผล {_pct(dy)} แต่ payout เริ่มสูง ({_pct(po)})", mv)


_EVALUATORS = {
    "valuation": _eval_valuation,
    "profitability": _eval_profitability,
    "financial_health": _eval_financial_health,
    "growth": _eval_growth,
    "cash_flow": _eval_cash_flow,
    "dividend": _eval_dividend,
}


def evaluate_value_schools(snapshot: dict) -> list[dict]:
    """รัน evaluator เชิงตัวเลขทั้งหมด คืนรายการ verdict (เรียงตาม registry)."""
    out = []
    for school in kb.value_schools_by_evaluator("python"):
        fn = _EVALUATORS.get(school["id"])
        if fn:
            out.append(fn(snapshot))
    return out
