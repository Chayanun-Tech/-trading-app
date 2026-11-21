"""ประเมินมูลค่าแบบ "รายตัว" (Valuation v2) — เลือกวิธีตามธรรมชาติธุรกิจ + ใช้ตัวเลขของบริษัทเอง

ปัญหาของ v1 (consensus_value): ใช้สูตรเดียวกับทุกหุ้น (คิดลด 8.5% · โตตาม CAGR รายได้ · P/E เพดาน 28)
→ พลาดกับหุ้นวัฏจักร (MU ใช้กำไรยอดรอบ), สินค้าโภคภัณฑ์ (EOG), หุ้นโตแรง (NVDA), หุ้นพลิกฟื้น (INTC)

══ สเปก ══════════════════════════════════════════════════════════════════════════════════════════
1) จำแนกประเภท (ตามลำดับ — เจอข้อแรกที่ตรงก็หยุด · ใช้ข้อมูลงบ SEC ย้อนหลังสูงสุด 10 ปี):
   financial    ธนาคาร/ประกัน (SIC 60–64)
   reit         REIT (SIC 6798)
   unprofitable กำไรสุทธิ TTM ≤ 0 หรือ margin มัธยฐาน 5 ปี ≤ 2% · และรายได้โต ≥ 15%/ปี
   turnaround   เคยมีกำไรดี (margin มัธยฐาน 10 ปี > 5%) แต่ตอนนี้ margin TTM < 1/3 ของมัธยฐาน หรือขาดทุน
   commodity    เหมือง/ถ่านหิน/น้ำมันต้นน้ำ/โรงกลั่น (SIC 10xx 12xx 13xx 14xx 29xx)
   fast         รายได้โต ≥ 15%/ปี (5 ปี) และมีกำไรต่อเนื่อง 3 ปีล่าสุด (เช็กก่อน cyclical — บริษัทที่เพิ่งพ้นช่วงขาดทุน
                อย่าง PDD/PLTR margin จะ "แกว่ง" ในสถิติ แต่ไม่ใช่วัฏจักร)
   cyclical     Owner Earnings margin แกว่งแรง (CV ≥ 0.5 · วัดจากเงินสด ไม่ใช่กำไรบัญชีที่ค่าตัดจำหน่ายบิดเบือน)
                หรือขาดทุน ≥ 2 ปีใน 10 ปี
   compounder   margin นิ่ง (CV < 0.35) และ ROE ≥ 15% (ทุนติดลบจากซื้อหุ้นคืน → Owner FCF margin ≥ 15%)
   mature       ที่เหลือ
   (ไม่เข้าเงื่อนไขไหนเลยและไม่เคยกำไร → ไม่ประเมิน)
   ★ กำหนดรายหุ้นได้ที่ data/archetype_overrides.json (พร้อมเหตุผล) เมื่อกฎอัตโนมัติจับผิด — เช่น MU ที่เป็นวัฏจักร
     หน่วยความจำ แต่ 3 ปีล่าสุดกำไรต่อเนื่อง · AMZN ที่ขาดทุนปี 2022 จากเงินลงทุน Rivian ไม่ใช่ธุรกิจวัฏจักร

2) ตัวเลขเฉพาะบริษัท:
   อัตราคิดลด r = 7% + 0.75% × คะแนนความเสี่ยง (จำกัด 7–12%) · ADR ต่างชาติ +1%
       คะแนนความเสี่ยง = ความผันผวนกำไร (CV <0.25:0 · <0.5:1 · ≥0.5:2) + หนี้สุทธิ/FCF (≤1:0 · ≤3:1 · >3 หรือ FCF≤0:2)
                         + ประเภท (unprofitable +2 · turnaround +1 · commodity +1)
   ธนาคาร/ประกัน: r อย่างน้อย 9.5% (หนี้คือวัตถุดิบของธุรกิจ สูตรหนี้/FCF จับความเสี่ยงไม่ได้)
   Owner Earnings (Buffett) = เงินสดจากการดำเนินงาน − ค่าบำรุงรักษา (≈ ค่าเสื่อม ไม่เกิน capex จริง) − ค่าตอบแทนเป็นหุ้น
   FCF จริง = เงินสดจากการดำเนินงาน − capex ทั้งหมด − ค่าตอบแทนเป็นหุ้น
   อัตราโต = CAGR รายได้ 5 ปี (มีเพดาน) · ต้นทุนการโต = สัดส่วนที่บริษัทลงทุนขยายจริง 5 ปี = 1 − FCF จริง ÷ Owner Earnings
   การโต/margin ทุกเส้นค่อย ๆ ลู่เข้า 2.5% (การโตระยะยาว) ภายในปีที่ 10 แล้วคิด terminal value

3) วิธีตามประเภท (ทุกวิธีให้ 3 ฉากทัศน์ ร้าย/กลาง/ดี):
   compounder/mature  DCF: Owner Earnings margin มัธยฐาน 5 ปี × (1 − สัดส่วนลงทุนขยาย) × รายได้ TTM · โต (เพดาน 15% / 8%)
                      ร้าย: โตครึ่งเดียว margin −15% คิดลด +1% · ดี: โต ×1.2 margin +5% คิดลด −0.5%
   fast               DCF 10 ปี: โตเริ่ม = CAGR (10–35%) ลดลงเป็นเส้นตรง · margin เริ่ม = FCF จริง TTM (ไม่ต่ำกว่าครึ่งของ
                      Owner Earnings เพราะ capex ขยายกิจการชั่วคราว เช่น ลงทุน AI) → ปลายทาง = Owner Earnings (TTM + มัธยฐาน 10 ปี)/2
                      ร้าย: โต ×0.6 margin ปลายทาง ×0.75 · ดี: โต ×1.15 margin คงที่ระดับ TTM
   cyclical           กำไรเฉลี่ยตลอดรอบ: margin มัธยฐาน 10 ปี × รายได้ปกติ (TTM แต่ไม่เกิน 1.15 × ค่าเฉลี่ย 5 ปี)
                      ร้าย/ดี: margin เปอร์เซ็นไทล์ 25/75 ของประวัติ · โต ≤ 6%
   commodity          เหมือน cyclical แต่รายได้ปกติ = ค่าเฉลี่ย 7 ปี (ไม่ใช้ปีราคาสินค้าสูง) · โต 2%
   financial          Justified P/B = (ROE − g) ÷ (r − g) · ROE มัธยฐาน 5 ปี · g = min(ROE × สัดส่วนเก็บไว้, 5%)
                      ร้าย/ดี: ROE ×0.8 / ×1.1
   turnaround         พื้น = ส่วนผู้ถือหุ้นหักค่าความนิยม · กลาง = margin ฟื้นครึ่งเดียวของอดีต · ดี = ฟื้นเต็ม
   unprofitable       เส้นทางสู่กำไร: รายได้โตจาก CAGR (10–40%) ลดลงเป็น 2.5% · FCF margin จาก TTM → เป้าหมาย
                      (ซอฟต์แวร์ 25% · อื่น ๆ 15%) ในปีที่ 10 · ร้าย: โต ×0.6 เป้า margin ครึ่งเดียว คิดลด +2%
   reit               ตัวคูณ P/FFO ในอดีต (จาก v1) — ยังไม่มีวิธีเฉพาะ
   * ตัวคูณที่ตลาดเคยให้ (P/E ในอดีต) แสดงเป็น "มุมมองตลาด" เทียบ แต่ไม่นำมาคิดมูลค่า (เป็นต้นเหตุ bias ใน v1)

4) ความน่าเชื่อ 0–100 (แทนเกรด A/B/C ที่ backtest แล้วไม่ช่วย):
   ความกว้างฉากทัศน์ (ดี÷ร้าย ≤1.8:40 · ≤2.5:30 · ≤4:15 · อื่น ๆ:5) + ความนิ่งของ margin (CV <0.25:25 · <0.5:15 · อื่น:5)
   + ความยากของประเภท (compounder 20 · mature 18 · financial 15 · fast 12 · cyclical/reit 10 · commodity 8
     · turnaround 5 · unprofitable 3) + ความสดของงบ (≤200 วัน:15 · อื่น:5)
   ≥70 สูง · 45–69 กลาง · <45 ต่ำ
═══════════════════════════════════════════════════════════════════════════════════════════════════
ผลลัพธ์เป็นราคาต่อหุ้น = ราคาตลาด × (มูลค่าส่วนผู้ถือหุ้น ÷ มูลค่าตลาด) เหมือน v1 · ไม่ใช่คำแนะนำการลงทุน
"""
from __future__ import annotations

import json
import statistics
from datetime import date
from pathlib import Path

from app import edgar
from app.consensus_value import TERMINAL_GROWTH, _annual, _instant, _ttm, gather_inputs, revenue_cagr

_DIV = ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"]
_BUYBACK = ["PaymentsForRepurchaseOfCommonStock"]
_GOODWILL = ["Goodwill"]
_DA = ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization", "DepreciationAmortizationAndAccretionNet",
       "Depreciation", "DepreciationAndAmortisationExpense", "DepreciationPropertyPlantAndEquipment"]
COMMODITY_SIC = ("10", "12", "13", "14", "29")
SOFTWARE_SIC = ("7370", "7371", "7372", "7373", "7374")

LABEL = {"financial": "การเงิน (ธนาคาร/ประกัน)", "reit": "REIT", "unprofitable": "ยังไม่ทำกำไร (กำลังโต)",
         "turnaround": "กำลังพลิกฟื้น", "commodity": "ผูกราคาสินค้าโภคภัณฑ์", "cyclical": "วัฏจักร",
         "fast": "โตเร็ว", "compounder": "Compounder มั่นคง", "mature": "อิ่มตัว/โตช้า"}
OVERRIDES = Path(__file__).resolve().parents[2] / "data" / "archetype_overrides.json"


def load_overrides() -> dict:
    """ประเภทธุรกิจที่กำหนดรายหุ้น (เมื่อกฎอัตโนมัติจับผิด) — ไฟล์ data/archetype_overrides.json."""
    try:
        d = json.loads(OVERRIDES.read_text(encoding="utf-8"))
        return {k.upper(): v for k, v in d.items() if not k.startswith("_") and v.get("archetype") in LABEL}
    except (OSError, ValueError):
        return {}


PRIOR = {"compounder": 20, "mature": 18, "financial": 15, "fast": 12, "cyclical": 10, "reit": 10,
         "commodity": 8, "turnaround": 5, "unprofitable": 3}


# ── ตัวช่วยสถิติ ─────────────────────────────────────────────────────────────────
def _med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def _pct(xs, q):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def _clip(x, lo, hi):
    return max(lo, min(hi, x))


# ── ตัวชี้วัดของบริษัท ────────────────────────────────────────────────────────────
def metrics(inp: dict) -> dict:
    rev, ni = inp["revenue"], inp["net_income"]
    yrs = sorted(y for y in rev if rev[y] and rev[y] > 0)[-10:]
    nm = {y: ni[y] / rev[y] for y in yrs if y in ni}
    da = inp.get("da") or {}

    def maint(y):  # ค่าบำรุงรักษา ≈ ค่าเสื่อมราคา (ไม่เกิน capex จริง) — แนวคิด Owner Earnings ของ Buffett
        cx = abs(inp["capex"].get(y, 0.0))
        return min(cx, abs(da[y])) if y in da and da[y] else cx
    fcf = {y: (inp["ocf"][y] - abs(inp["capex"].get(y, 0.0)) - abs(inp["sbc"].get(y, 0.0))) / rev[y]
           for y in yrs if y in inp["ocf"]}                       # FCF จริง (หัก capex ทั้งหมด)
    own = {y: (inp["ocf"][y] - maint(y) - abs(inp["sbc"].get(y, 0.0))) / rev[y]
           for y in yrs if y in inp["ocf"]}                       # Owner Earnings (หักแค่ค่าบำรุงรักษา)
    nm_vals = [nm[y] for y in sorted(nm)]
    own_vals = [own[y] for y in sorted(own)]
    base_cv = own_vals if len(own_vals) >= 5 else nm_vals     # ความนิ่งวัดจากเงินสด (บัญชีค่าตัดจำหน่ายไม่หลอก)
    mean_b = statistics.mean(base_cv) if len(base_cv) >= 5 else None
    cv = (statistics.pstdev(base_cv) / abs(mean_b)) if mean_b else None
    rev_ttm = inp.get("revenue_ttm")
    ttm_nm = inp["ni_ttm"] / rev_ttm if inp.get("ni_ttm") is not None and rev_ttm else None
    ttm_fcf = ((inp["ocf_ttm"] - abs(inp.get("capex_ttm") or 0) - abs(inp.get("sbc_ttm") or 0)) / rev_ttm
               if inp.get("ocf_ttm") is not None and rev_ttm else None)
    cx_t, da_t = abs(inp.get("capex_ttm") or 0), abs(inp.get("da_ttm") or 0)
    ttm_own = ((inp["ocf_ttm"] - (min(cx_t, da_t) if da_t else cx_t) - abs(inp.get("sbc_ttm") or 0)) / rev_ttm
               if inp.get("ocf_ttm") is not None and rev_ttm else None)
    eqh = inp.get("equity_hist") or {}
    roes = [ni[y] / eqh[y] for y in sorted(eqh) if eqh[y] and eqh[y] > 0 and y in ni][-5:]
    payouts = []
    for y in sorted(ni)[-5:]:
        if ni[y] and ni[y] > 0:
            payouts.append((abs(inp["div"].get(y, 0.0)) + abs(inp["buyback"].get(y, 0.0))) / ni[y])
    payout = _med(payouts)
    net_debt = inp["debt"] - inp["cash"]
    norm_fcf = (rev_ttm or 0) * (_med(list(fcf.values())[-5:]) or 0)
    return {
        "years": len(yrs), "nm": nm_vals, "nm_med5": _med(nm_vals[-5:]), "nm_med10": _med(nm_vals),
        "fcf_hist": [fcf[y] for y in sorted(fcf)], "fcf_med5": _med([fcf[y] for y in sorted(fcf)][-5:]),
        "fcf_med10": _med(list(fcf.values())), "ttm_nm": ttm_nm, "ttm_fcf": ttm_fcf, "cv": cv,
        "own_med5": _med(own_vals[-5:]), "own_med10": _med(own_vals), "ttm_own": ttm_own,
        "recent_loss": any(x < 0 for x in nm_vals[-3:]),
        "loss_years": sum(1 for x in nm_vals if x < 0), "cagr5": revenue_cagr(rev, 5), "cagr10": revenue_cagr(rev, 10),
        "roe": _med(roes), "retention": _clip(1 - payout, 0, 1) if payout is not None else None,
        "net_debt": net_debt, "norm_fcf": norm_fcf,
        "rev_avg5": statistics.mean([rev[y] for y in yrs[-5:]]) if len(yrs) >= 5 else None,
        "rev_avg7": statistics.mean([rev[y] for y in yrs[-7:]]) if len(yrs) >= 7 else None,
    }


def classify(m: dict, profile: str, sic) -> str | None:
    s = str(sic or "")
    if profile in ("bank", "insurance") or s[:2] in ("60", "61", "62", "63", "64"):
        return "financial"
    if profile == "reit" or s == "6798":
        return "reit"
    if m["years"] < 3:
        return None
    cagr5 = m["cagr5"] or 0
    unprofitable_now = (m["ttm_nm"] is not None and m["ttm_nm"] <= 0) or (m["nm_med5"] is not None and m["nm_med5"] <= 0.02)
    hist_good = m["nm_med10"] is not None and m["nm_med10"] > 0.05
    if unprofitable_now and cagr5 >= 0.15:
        return "unprofitable"
    if hist_good and m["ttm_nm"] is not None and m["ttm_nm"] < m["nm_med10"] / 3:
        return "turnaround"
    if unprofitable_now:
        return "turnaround" if hist_good else None
    if s[:2] in COMMODITY_SIC:
        return "commodity"
    if cagr5 >= 0.15 and not m["recent_loss"] and (m["ttm_nm"] or 0) > 0:
        return "fast"                                   # โตแรงและกำไรต่อเนื่อง 3 ปี (เช่น PDD/PLTR ที่เพิ่งพ้นช่วงขาดทุน)
    if (m["cv"] is not None and m["cv"] >= 0.5) or m["loss_years"] >= 2:
        return "cyclical"
    if cagr5 >= 0.15:
        return "fast"
    if m["cv"] is not None and m["cv"] < 0.35 and ((m["roe"] or 0) >= 0.15 or (m["fcf_med5"] or 0) >= 0.15):
        return "compounder"
    return "mature"


def discount_rate(m: dict, arche: str, foreign: bool) -> tuple[float, int]:
    cv = m["cv"] if m["cv"] is not None else 1.0
    risk = 0 if cv < 0.25 else 1 if cv < 0.5 else 2
    if m["norm_fcf"] <= 0:
        risk += 2
    else:
        lev = m["net_debt"] / m["norm_fcf"]
        risk += 0 if lev <= 1 else 1 if lev <= 3 else 2
    risk += {"unprofitable": 2, "turnaround": 1, "commodity": 1}.get(arche, 0)
    # ทดสอบแล้ว (2026-11-21): บวกความเสี่ยงอุตสาหกรรมวัฏจักร (ชิป/รถ/เหล็ก/เคมี ฯลฯ) +1.5% ไม่ช่วย —
    # IC รวม +0.009→+0.008 และตัวที่ "ดูถูก" ในกลุ่มนี้กลับให้ผลตอบแทนดีกว่า (20% vs 15%/ปี) จึงไม่ใช้
    r = _clip(0.07 + 0.0075 * risk, 0.07, 0.12) + (0.01 if foreign else 0.0)
    return r, risk


def fundamental_growth(m: dict, cap: float) -> float:
    """อัตราโต = CAGR รายได้ 5 ปี (มีเพดาน) — ต้นทุนของการโตคิดแยกด้วย reinvestment = g ÷ ROE
    (ไม่ใช้ ROE × สัดส่วนกำไรที่เก็บไว้ เพราะบริษัทที่ทุนน้อย ROE สูง เช่น V/ADBE จ่ายคืนหมดแต่ยังโตได้)."""
    g = m["cagr5"] if m["cagr5"] is not None else 0.03
    return _clip(g, 0.0, cap)


def dcf_path(rev0: float, g_start: float, m_start: float, m_end: float, r: float, net_cash: float,
             g_t: float = TERMINAL_GROWTH, years: int = 10) -> float:
    """รายได้โตจาก g_start ลดเป็นเส้นตรงถึง g_t ในปีที่ years · FCF margin จาก m_start → m_end · บวกเงินสดสุทธิ."""
    rev, pv, fcf = rev0, 0.0, 0.0
    for i in range(1, years + 1):
        g = g_start + (g_t - g_start) * (i - 1) / (years - 1)
        m = m_start + (m_end - m_start) * i / years
        rev *= 1 + g
        fcf = rev * m
        pv += fcf / (1 + r) ** i
    tv = max(fcf, 0) * (1 + g_t) / (r - g_t)
    return pv + tv / (1 + r) ** years + net_cash


# ── วิธีตามประเภท → (ร้าย, กลาง, ดี, หมายเหตุ) ─────────────────────────────────────────
def value_by_archetype(arche: str, inp: dict, m: dict, r: float, sic) -> dict | None:
    rev = inp.get("revenue_ttm")
    nc = inp["cash"] - inp["debt"]
    if not rev or rev <= 0:
        return None
    if arche in ("compounder", "mature"):
        g = fundamental_growth(m, 0.15 if arche == "compounder" else 0.08)
        own = m["own_med5"] if m["own_med5"] is not None else m["fcf_med5"]
        if own is None or own <= 0.01:
            return None
        # การโตต้องลงทุนเพิ่ม: ใช้สัดส่วนที่บริษัท "ลงทุนขยายจริง" ใน 5 ปี = 1 − FCF จริง ÷ Owner Earnings
        # (ตรงกับการโตที่ทำได้จริงในช่วงเดียวกัน · ไม่ใช้ g ÷ ROE เพราะส่วนผู้ถือหุ้นบิดเบือนจากซื้อหุ้นคืน/ค่าความนิยม)
        if m["fcf_med5"] is not None and own > 0:
            reinvest = _clip(1 - m["fcf_med5"] / own, 0.0, 0.8)
        else:
            reinvest = _clip(g / m["roe"], 0.0, 0.8) if m["roe"] and m["roe"] > 0 else 0.3
        mm = own * (1 - reinvest)
        return {"bear": dcf_path(rev, g * 0.5, mm * 0.85, mm * 0.85, r + 0.01, nc),
                "base": dcf_path(rev, g, mm, mm, r, nc),
                "bull": dcf_path(rev, g * 1.2, mm * 1.05, mm * 1.05, r - 0.005, nc),
                "method": "DCF Owner FCF (margin มัธยฐาน 5 ปี · โตพื้นฐานจาก ROE × กำไรที่เก็บไว้)",
                "assume": {"โต": g, "Owner Earnings margin": own, "ลงทุนต่อเพื่อโต": reinvest, "เงินสดเหลือให้เจ้าของ": mm}}
    if arche == "fast":
        m0 = m["ttm_fcf"] if m["ttm_fcf"] is not None else m["fcf_med5"]
        own_now = m["ttm_own"]
        if own_now is not None and own_now > 0 and (m0 is None or m0 < 0.5 * own_now):
            m0 = 0.5 * own_now   # FCF ปีนี้ถูกกดด้วย capex ขยายกิจการ (เช่น ลงทุน AI) — เริ่มที่ครึ่งหนึ่งของ Owner Earnings
        if m0 is None or m0 <= 0:
            return None
        own_end = m["ttm_own"] if m["ttm_own"] is not None else m0
        m_end = (own_end + (m["own_med10"] if m["own_med10"] is not None else own_end)) / 2  # โตช้าลง → ลงทุนขยายน้อยลง
        g0 = _clip(m["cagr5"] or 0.15, 0.10, 0.35)
        return {"bear": dcf_path(rev, g0 * 0.6, m0, m_end * 0.75, r + 0.01, nc),
                "base": dcf_path(rev, g0, m0, m_end, r, nc),
                "bull": dcf_path(rev, min(g0 * 1.15, 0.40), m0, m0, r - 0.005, nc),
                "method": "DCF หุ้นโตเร็ว (การโตลดลงเป็นเส้นตรง · margin ลู่เข้าค่ากลางระยะยาว)",
                "assume": {"โตเริ่ม": g0, "FCF margin เริ่ม": m0, "FCF margin ปลายทาง": m_end}}
    if arche in ("cyclical", "commodity"):
        hist = m["fcf_hist"]
        if len(hist) < 5:
            return None
        mid, lo, hi = _med(hist), _pct(hist, 0.25), _pct(hist, 0.75)
        if mid is None or mid <= 0.01:
            return None
        if arche == "commodity":
            rev_n = m["rev_avg7"] or m["rev_avg5"] or rev
            g = 0.02
        else:
            rev_n = min(rev, 1.15 * m["rev_avg5"]) if m["rev_avg5"] else rev
            g = _clip(m["cagr10"] or 0.02, 0.0, 0.06)
        return {"bear": dcf_path(rev_n, g * 0.5, max(lo, 0.0), max(lo, 0.0), r + 0.01, nc),
                "base": dcf_path(rev_n, g, mid, mid, r, nc),
                "bull": dcf_path(rev_n, g, hi, hi, r, nc),
                "method": "กำไรเฉลี่ยตลอดรอบ (margin มัธยฐาน 10 ปี × รายได้ปกติ — ไม่ใช้ปีที่ยอด/ก้นรอบ)",
                "assume": {"รายได้ปกติ ÷ TTM": rev_n / rev, "FCF margin กลางรอบ": mid, "โต": g}}
    if arche == "financial":
        eq = inp.get("equity")
        if not eq or eq <= 0 or m["roe"] is None or m["roe"] <= 0:
            return None
        g = min((m["roe"] * (m["retention"] if m["retention"] is not None else 0.5)), 0.05)

        def pb(roe):
            return _clip((roe - g) / (r - g), 0.3, 4.0)
        return {"bear": eq * pb(m["roe"] * 0.8), "base": eq * pb(m["roe"]), "bull": eq * pb(m["roe"] * 1.1),
                "method": "Justified P/B = (ROE − g) ÷ (r − g)", "assume": {"ROE": m["roe"], "โต": g}}
    if arche == "turnaround":
        eq = inp.get("equity") or 0
        floor = max(eq - (inp.get("goodwill") or 0), 0)
        mh = m["fcf_med10"]
        if mh is None or mh <= 0:
            return {"bear": floor, "base": floor, "bull": floor, "method": "มูลค่าสินทรัพย์สุทธิ (ไม่รวมค่าความนิยม)",
                    "assume": {}} if floor > 0 else None
        return {"bear": floor, "base": max(floor, dcf_path(rev, 0.02, mh * 0.5, mh * 0.5, r, nc)),
                "bull": max(floor, dcf_path(rev, 0.02, mh * 0.5, mh, r, nc)),
                "method": "พลิกฟื้น: พื้น = สินทรัพย์สุทธิ · กลาง = margin ฟื้นครึ่งเดียว · ดี = ฟื้นเต็ม",
                "assume": {"FCF margin ในอดีต": mh}}
    if arche == "unprofitable":
        g0 = _clip(m["cagr5"] or 0.2, 0.10, 0.40)
        m0 = m["ttm_fcf"] if m["ttm_fcf"] is not None else -0.1
        target = 0.25 if str(sic or "") in SOFTWARE_SIC else 0.15
        return {"bear": dcf_path(rev, g0 * 0.6, m0, target * 0.5, r + 0.02, nc),
                "base": dcf_path(rev, g0, m0, target, r, nc),
                "bull": dcf_path(rev, min(g0 * 1.1, 0.45), m0, target * 1.2, r - 0.005, nc),
                "method": "เส้นทางสู่กำไร (รายได้โตลดลง · margin ไต่สู่เป้าหมายในปีที่ 10)",
                "assume": {"โตเริ่ม": g0, "FCF margin ตอนนี้": m0, "เป้า margin": target}}
    return None


def confidence(bear: float, bull: float, m: dict, arche: str, data_end: str | None,
               as_of: date | None = None) -> tuple[int, str]:
    bear = max(bear, 1e-9)
    w = bull / bear if bear > 0 else 99
    s = 40 if w <= 1.8 else 30 if w <= 2.5 else 15 if w <= 4 else 5
    cv = m["cv"] if m["cv"] is not None else 1
    s += 25 if cv < 0.25 else 15 if cv < 0.5 else 5
    s += PRIOR.get(arche, 5)
    fresh = data_end and ((as_of or date.today()) - date.fromisoformat(data_end)).days <= 200
    s += 15 if fresh else 5
    return s, ("สูง" if s >= 70 else "กลาง" if s >= 45 else "ต่ำ")


# ── ตัวประกอบร่าง ───────────────────────────────────────────────────────────────
def evaluate(facts: dict, sic, profile: str, price: float, mcap_ccy: float, hist_fair: dict | None = None,
             symbol: str | None = None, overrides: dict | None = None, as_of: date | None = None) -> dict | None:
    """แกนคำนวณ (ไม่ต่อเน็ต — ใช้ได้ทั้งของจริงและ backtest point-in-time)."""
    inp = gather_inputs(facts)
    unit = inp["unit"]
    inp["div"] = _annual(facts, _DIV, unit)
    inp["buyback"] = _annual(facts, _BUYBACK, unit)
    inp["goodwill"] = _instant(facts, _GOODWILL, unit)
    inp["da"] = _annual(facts, _DA, unit)
    inp["da_ttm"] = _ttm(facts, _DA, unit, cumulative=True)[0]
    m = metrics(inp)
    arche = classify(m, profile, sic)
    ov = (overrides if overrides is not None else load_overrides()).get((symbol or "").upper())
    if ov:
        arche = ov["archetype"]
    if arche is None:
        return None
    r, risk = discount_rate(m, arche, unit != "USD")
    if arche == "financial":
        r = max(r, 0.095)   # ต้นทุนทุนของธนาคาร/ประกันสูงกว่าที่สูตรหนี้/FCF จับได้ (หนี้คือวัตถุดิบของธุรกิจ)
    if arche == "reit":
        if not hist_fair:
            return None
        k = mcap_ccy / price
        val = {"bear": hist_fair["low"] * k, "base": hist_fair["fair_price"] * k, "bull": hist_fair["high"] * k,
               "method": "ตัวคูณ P/FFO ในอดีต (ยังไม่มีวิธีเฉพาะ REIT)", "assume": {}}
    else:
        val = value_by_archetype(arche, inp, m, r, sic)
    if not val or val["base"] <= 0:
        return None
    to_px = price / mcap_ccy
    bear, base, bull = sorted([val["bear"], val["base"], val["bull"]])
    score, level = confidence(bear, bull, m, arche, inp["data_end"], as_of)
    return {
        "archetype": arche, "archetype_label": LABEL[arche], "method": val["method"],
        "archetype_source": "กำหนดรายหุ้น: " + ov["reason"] if ov else "กฎอัตโนมัติ",
        "low": round(max(bear, 0) * to_px, 2), "fair_price": round(base * to_px, 2), "high": round(bull * to_px, 2),
        "upside_pct": round((base * to_px / price - 1) * 100, 1),
        "discount_rate_pct": round(r * 100, 2), "risk_score": risk,
        "assumptions": {k: round(v, 4) for k, v in val["assume"].items()},
        "confidence": score, "confidence_level": level,
        "metrics": {"cagr5": m["cagr5"], "cv": m["cv"], "roe": m["roe"], "fcf_med5": m["fcf_med5"],
                    "ttm_fcf": m["ttm_fcf"], "years": m["years"]},
        "data_end": inp["data_end"],
    }


async def value_stock(symbol: str, *, refresh: bool = False) -> dict | None:
    """ประเมินหุ้น 1 ตัวด้วย v2 (ใช้แหล่งข้อมูลเดียวกับ v1)."""
    from app import revenue_model
    from app.consensus_value import _fx_to_usd
    from app.fundamentals import fetch_yahoo_fundamentals

    symbol = symbol.upper()
    hist = None
    try:
        hist = await revenue_model.sector_fair_value(symbol, refresh=refresh)
    except Exception:  # noqa: BLE001
        pass
    try:
        facts = await edgar.get_company_facts(symbol, force_refresh=refresh and hist is None)
        snap = await fetch_yahoo_fundamentals(symbol)
    except Exception:  # noqa: BLE001
        return None
    sic = (hist or {}).get("sic")
    if sic is None:
        try:
            sic = (await edgar.get_submissions(symbol)).get("sic")
        except Exception:  # noqa: BLE001
            sic = None
    price = (hist or {}).get("price")
    unit = gather_inputs(facts)["unit"]
    fx = await _fx_to_usd(unit)
    if not price or not snap.get("market_cap") or not fx:
        return None
    res = evaluate(facts, sic, (hist or {}).get("profile_key") or "general", price, snap["market_cap"] / fx,
                   (hist or {}).get("fair_value"), symbol=symbol)
    if res:
        res.update({"symbol": symbol, "price": round(price, 2), "sic": sic,
                    "market_view": ({"basis": hist["fair_value"]["basis"], "fair": hist["fair_value"]["fair_price"]}
                                    if hist and hist.get("fair_value") else None)})
    return res


# ── กฎเลือกวิธีจากผล backtest ระยะ 2 (ops/backtest_v2.py · point-in-time 2013–2026) ────────────────
#   v2 ดีกว่า v1: fast (IC 3 ปี +0.05) · cyclical (กลับจากติดลบเป็นบวก) · compounder ใกล้เคียง → ใช้ v2
#   v1 ดีกว่า: financial · turnaround → ใช้ v1 (ตัวคูณในอดีต/หลายโมเดล) · reit ใช้ P/FFO ในอดีตอยู่แล้ว
#   ราคาถูกกลับให้ผลแย่ (กับดักหุ้นถูก): mature (IC −0.07 ถึง −0.11) · unprofitable (ถูก−แพง −18%/ปี)
#     → แสดงตัวเลขได้ แต่ "ไม่ให้สัญญาณซื้อจากมูลค่า"
#   ความน่าเชื่อ "ต่ำ" ให้สัญญาณกลับทิศ (IC 3 ปี −0.064) → ไม่ให้สัญญาณซื้อเช่นกัน
USE_V1 = {"financial", "turnaround"}
NO_BUY = {"mature": "หุ้นอิ่มตัว/โตช้า — backtest พบว่าตัวที่ดูถูกมักเป็นกับดักหุ้นถูก (ผลตอบแทนแย่กว่าตัวที่แพง)",
          "unprofitable": "ยังไม่ทำกำไร — มูลค่าขึ้นกับสมมติฐานอนาคตล้วน ๆ backtest พบว่าตัวที่ดูถูกแพ้หนัก"}
CONF_TO_GRADE = {"สูง": "A", "กลาง": "B", "ต่ำ": "C"}


def combine_v1_v2(v1: dict | None, v2: dict | None) -> dict | None:
    """รวมผล v1 (consensus_value) กับ v2 ตามกฎข้างบน → แถวเดียวที่ใช้ในแอป/Pine/หน้าพอร์ต."""
    if not v1 and not v2:
        return None
    arche = (v2 or {}).get("archetype")
    use_v1 = v1 is not None and (v2 is None or arche in USE_V1)
    src = v1 if use_v1 else v2
    conf_level = (v2 or {}).get("confidence_level") or "ต่ำ"
    if v2 is None:                                   # v2 ประเมินไม่ได้ → ใช้ v1 แต่ให้ความน่าเชื่อตามเกรดเดิม (สูงสุด "กลาง")
        conf_level = "กลาง" if (v1 or {}).get("grade") == "A" else "ต่ำ"
    reason = None
    if arche in NO_BUY:
        reason = NO_BUY[arche]
    elif conf_level == "ต่ำ":
        reason = "ความน่าเชื่อต่ำ — backtest พบว่าสัญญาณ 'ถูก' ของกลุ่มนี้มักกลับทิศ"
    price = src["price"]
    return {
        "low": src["low"], "fair_price": src["fair_price"], "high": src["high"], "price": price,
        "upside_pct": round((src["fair_price"] / price - 1) * 100, 1) if price else None,
        "source": "v1" if use_v1 else "v2",
        "archetype": arche, "archetype_label": LABEL.get(arche, "ไม่ระบุ") if arche else "ไม่ระบุ",
        "archetype_source": (v2 or {}).get("archetype_source"),
        "method": (v2 or {}).get("method") if not use_v1 else "หลายโมเดล v1 (ตัวคูณในอดีต/P-B) — backtest ดีกว่าสำหรับประเภทนี้",
        "confidence": (v2 or {}).get("confidence"), "confidence_level": conf_level,
        "grade": CONF_TO_GRADE[conf_level],
        "buy_allowed": reason is None, "no_buy_reason": reason,
        "discount_rate_pct": (v2 or {}).get("discount_rate_pct"), "assumptions": (v2 or {}).get("assumptions"),
        "implied_growth_pct": (v1 or {}).get("implied_growth_pct"), "hist_growth_pct": (v1 or {}).get("hist_growth_pct"),
        "models_used": (v1 or {}).get("models_used", 1), "profile": (v1 or {}).get("profile"),
        "v1_fair": (v1 or {}).get("fair_price"), "v2_fair": (v2 or {}).get("fair_price"),
        "data_end": (v2 or v1 or {}).get("data_end"),
    }
