"""ตัวชี้วัดเฉพาะ sector (ส่วนขยายของชั้น 3) — สูตรบริสุทธิ์ ไม่แตะ I/O ทดสอบง่าย.

สูตรพวกนี้คือ metric ที่ profile แต่ละกลุ่มต้องใช้แทน P/E, FCF ธรรมดา:
  • Book Value/share → ธนาคาร/ประกัน (มูลค่าอิงงบดุล ไม่ใช่กระแสเงินสด)
  • FFO / AFFO       → REIT (บวกค่าเสื่อมกลับ เพราะอสังหาฯ ไม่ได้เสื่อมจริงแบบเครื่องจักร)
  • EBITDA           → ธุรกิจลงทุนหนัก (เทียบข้ามโครงสร้างหนี้ได้)
  • Normalized       → วัฏจักร/ลงทุนหนัก (เฉลี่ยหลายปีกลบวัฏจักร — Shiller/normalized FCF)
  • Cash runway      → เติบโตยังไม่กำไร (เหลือเงินกี่เดือนก่อนต้องระดมทุน)
  • Rule of 40       → SaaS (โต% + FCF margin% ≥ 40 = สุขภาพดี)
"""
from __future__ import annotations

from statistics import mean


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) else None


def _safe_div(a, b) -> float | None:
    a, b = _num(a), _num(b)
    if a is None or not b:
        return None
    return a / b


def book_value_per_share(equity, preferred, shares) -> float | None:
    """มูลค่าตามบัญชีต่อหุ้น = (ส่วนของผู้ถือหุ้น − หุ้นบุริมสิทธิ) ÷ จำนวนหุ้น."""
    eq, sh = _num(equity), _num(shares)
    if eq is None or not sh:
        return None
    return (eq - (_num(preferred) or 0.0)) / sh


def ffo(net_income, depreciation, gains_on_sale=0.0) -> float | None:
    """Funds From Operations = กำไรสุทธิ + ค่าเสื่อม − กำไรจากการขายทรัพย์สิน (มาตรฐาน NAREIT)."""
    ni, dep = _num(net_income), _num(depreciation)
    if ni is None or dep is None:
        return None
    return ni + dep - (_num(gains_on_sale) or 0.0)


def affo(ffo_val, maintenance_capex, straight_line_rent=0.0) -> float | None:
    """Adjusted FFO = FFO − capex บำรุงรักษา − ค่าเช่าปรับเส้นตรง (เงินสดที่จ่ายปันผลได้จริง)."""
    f, mc = _num(ffo_val), _num(maintenance_capex)
    if f is None or mc is None:
        return None
    return f - mc - (_num(straight_line_rent) or 0.0)


def ebitda(operating_income, depreciation) -> float | None:
    """EBITDA ≈ กำไรจากการดำเนินงาน + ค่าเสื่อม/ค่าตัดจำหน่าย."""
    oi, dep = _num(operating_income), _num(depreciation)
    if oi is None or dep is None:
        return None
    return oi + dep


def roe(net_income, equity) -> float | None:
    """Return on Equity = กำไรสุทธิ ÷ ส่วนของผู้ถือหุ้น (สัดส่วน 0-1)."""
    return _safe_div(net_income, equity)


def net_margin(net_income, revenue) -> float | None:
    return _safe_div(net_income, revenue)


def normalized(values, n: int) -> float | None:
    """ค่าเฉลี่ย n ปีล่าสุด (กลบวัฏจักร) — ใช้กับ EPS (Shiller) และ FCF (normalized)."""
    vals = [v for v in values if isinstance(v, (int, float))]
    if not vals:
        return None
    return mean(vals[-n:])


def cash_runway_months(cash, annual_burn) -> float | None:
    """เหลือเงินกี่เดือนก่อนหมด. annual_burn = เงินสดที่เผาต่อปี (ค่าบวก = กำลังเผา).
    ถ้าไม่ได้เผา (burn ≤ 0 = กระแสเงินสดเป็นบวก) คืน None (runway ไม่ใช่ประเด็น)."""
    c, b = _num(cash), _num(annual_burn)
    if c is None or b is None or b <= 0:
        return None
    return c / (b / 12.0)


def rule_of_40(revenue_growth_pct, fcf_margin_pct) -> float | None:
    """Rule of 40 = อัตราโตรายได้ (%) + FCF margin (%). ≥ 40 = สมดุลโต/กำไรดี (เกณฑ์ SaaS)."""
    g, m = _num(revenue_growth_pct), _num(fcf_margin_pct)
    if g is None or m is None:
        return None
    return g + m
