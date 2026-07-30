"""Buffett OS — เครื่องคิดคะแนนการลงทุนแบบ VI/Warren Buffett จากงบ SEC จริง (auditable).

รวมเครื่องมือหลักที่สาย VI ระดับโลกใช้ตัดสินคุณภาพ + ความปลอดภัย + มูลค่า:
  • Piotroski F-Score (9)  — ความแข็งแรงพื้นฐานที่กำลังดีขึ้น/แย่ลง
  • Altman Z-Score          — ความเสี่ยงล้มละลาย 2 ปีข้างหน้า
  • Beneish M-Score + Accruals — สัญญาณการแต่งบัญชี/กำไรกระดาษ
  • Owner Earnings + Reverse DCF — กำไรเจ้าของจริง + "ตลาดคิดว่าจะโตกี่ %"
  • Moat metrics            — ROIC ยืนระยะ, ROIC−WACC, ความนิ่งของมาร์จิน (พลังตั้งราคา)
  • Capital Allocation      — ผู้บริหารใช้เงินสดเป็น (ปันผล/ซื้อคืน/ลงทุน/หนี้)
  • Buffett Scorecard       — รวม 4 เสาเป็นคะแนน 0–100 + คำตัดสิน

ทุกฟังก์ชันทนค่า None (งบบางช่องขาด) — คืน None/ธงว่า "คำนวณไม่ได้" แทน crash.
คำนวณจาก companyfacts (SEC XBRL) โดยตรง คีย์ด้วยปีงบ (fy) เพื่อจับคู่ YoY แม่นยำ.
"""
from __future__ import annotations

import statistics

from app import financials as F

# ── ค่าคงที่ประเมิน (ปรับได้) ──────────────────────────────────────────────
_RISK_FREE = 0.043       # อัตราผลตอบแทนพันธบัตร ~ 10Y (ประมาณ)
_EQUITY_PREMIUM = 0.05   # ส่วนชดเชยความเสี่ยงตลาดหุ้น
_DEFAULT_BETA = 1.0
_DISCOUNT_RATE = 0.10    # อัตราคิดลดฐาน (Buffett มักใช้ ~ ผลตอบแทนพันธบัตร แต่เราใช้ 10% เป็นกลาง)
_TERMINAL_GROWTH = 0.025

_DEPRECIATION = ["DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet",
                 "DepreciationAndAmortization", "Depreciation"]
_PPE = ["PropertyPlantAndEquipmentNet"]


def _c(spec: list, key: str) -> list[str]:
    """ดึงรายการ us-gaap concepts ของ metric หนึ่งจาก spec list ใน financials."""
    return next(c[2] for c in spec if c[0] == key)


def _safe_div(a, b):
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)) or b == 0:
        return None
    return a / b


def _num(x):
    return x if isinstance(x, (int, float)) else None


def extract_series(facts: dict, unit: str = "USD") -> dict[str, dict[int, float]]:
    """ดึงงบรายปีทุก metric ที่ต้องใช้ คีย์เป็น {metric: {fy: value}} — flow ใช้ 10-K/20-F duration,
    stock ใช้ instant. gross_profit เติมจาก revenue−cost ถ้าบริษัทไม่ได้แท็ก GrossProfit."""
    # หน่วยต่างกันตามชนิด: ค่าเงิน = unit (USD/สกุลท้องถิ่น), จำนวนหุ้น = "shares", EPS = "สกุล/shares"
    eps_unit = F.pick_reporting_unit(facts, F._EPS, per_share=True) if hasattr(F, "pick_reporting_unit") else "USD/shares"
    dur = lambda cs, u=unit: F._annual_duration(F._entries(facts, cs, u))    # noqa: E731
    inst = lambda cs, u=unit: F._annual_instant(F._entries(facts, cs, u))    # noqa: E731
    S: dict[str, dict[int, float]] = {
        "revenue": dur(_c(F._INCOME, "revenue")),
        "cost_of_revenue": dur(_c(F._INCOME, "cost_of_revenue")),
        "gross_profit": dur(_c(F._INCOME, "gross_profit")),
        "operating_income": dur(_c(F._INCOME, "operating_income")),
        "net_income": dur(_c(F._INCOME, "net_income")),
        "interest_expense": dur(_c(F._INCOME, "interest_expense")),
        "income_tax": dur(_c(F._INCOME, "income_tax")),
        "sga": dur(_c(F._INCOME, "sga")),
        "ocf": dur(_c(F._CASHFLOW, "operating_cash_flow")),
        "capex": dur(_c(F._CASHFLOW, "capex")),
        "dividends_paid": dur(_c(F._CASHFLOW, "dividends_paid")),
        "buybacks": dur(_c(F._CASHFLOW, "buybacks")),
        "da": dur(_DEPRECIATION),
        "eps": dur(F._EPS, eps_unit),
        "shares": dur(F._SHARES, "shares"),
        "total_assets": inst(_c(F._BALANCE, "total_assets")),
        "current_assets": inst(_c(F._BALANCE, "current_assets")),
        "cash": inst(_c(F._BALANCE, "cash")),
        "receivables": inst(_c(F._BALANCE, "receivables")),
        "inventory": inst(_c(F._BALANCE, "inventory")),
        "current_liabilities": inst(_c(F._BALANCE, "current_liabilities")),
        "total_liabilities": inst(_c(F._BALANCE, "total_liabilities")),
        "short_term_debt": inst(_c(F._BALANCE, "short_term_debt")),
        "long_term_debt": inst(_c(F._BALANCE, "long_term_debt")),
        "total_equity": inst(_c(F._BALANCE, "total_equity")),
        "retained_earnings": inst(_c(F._BALANCE, "retained_earnings")),
        "ppe": inst(_PPE),
    }
    # เติม gross_profit ที่ขาดจาก revenue − cost_of_revenue
    for fy, rev in S["revenue"].items():
        if fy not in S["gross_profit"] and fy in S["cost_of_revenue"]:
            S["gross_profit"][fy] = rev - S["cost_of_revenue"][fy]
    return S


def _debt(S, fy):
    std, ltd = S["short_term_debt"].get(fy), S["long_term_debt"].get(fy)
    if std is None and ltd is None:
        return None
    return (std or 0) + (ltd or 0)


# ── 1) Piotroski F-Score ─────────────────────────────────────────────────
def piotroski(S: dict, fys: list[int]) -> dict | None:
    if len(fys) < 2:
        return None
    t, p = fys[-1], fys[-2]
    g = lambda m, fy: S.get(m, {}).get(fy)  # noqa: E731
    roa_t, roa_p = _safe_div(g("net_income", t), g("total_assets", t)), _safe_div(g("net_income", p), g("total_assets", p))
    cr_t, cr_p = _safe_div(g("current_assets", t), g("current_liabilities", t)), _safe_div(g("current_assets", p), g("current_liabilities", p))
    gm_t, gm_p = _safe_div(g("gross_profit", t), g("revenue", t)), _safe_div(g("gross_profit", p), g("revenue", p))
    at_t, at_p = _safe_div(g("revenue", t), g("total_assets", t)), _safe_div(g("revenue", p), g("total_assets", p))
    ld_t, ld_p = _safe_div(g("long_term_debt", t), g("total_assets", t)), _safe_div(g("long_term_debt", p), g("total_assets", p))
    ocf_t, ni_t = g("ocf", t), g("net_income", t)
    sh_t, sh_p = g("shares", t), g("shares", p)
    checks = [
        ("ROA เป็นบวก", roa_t is not None and roa_t > 0),
        ("กระแสเงินสดดำเนินงาน (OCF) เป็นบวก", ocf_t is not None and ocf_t > 0),
        ("ROA ดีขึ้นจากปีก่อน", roa_t is not None and roa_p is not None and roa_t > roa_p),
        ("OCF > กำไรสุทธิ (กำไรเป็นเงินสดจริง)", ocf_t is not None and ni_t is not None and ocf_t > ni_t),
        ("หนี้ระยะยาว/สินทรัพย์ ลดลง", ld_t is not None and ld_p is not None and ld_t <= ld_p),
        ("สภาพคล่อง (current ratio) ดีขึ้น", cr_t is not None and cr_p is not None and cr_t > cr_p),
        ("ไม่ออกหุ้นเพิ่ม (ไม่ทำให้ผู้ถือหุ้นเจือจาง)", sh_t is not None and sh_p is not None and sh_t <= sh_p * 1.02),
        ("อัตรากำไรขั้นต้นดีขึ้น", gm_t is not None and gm_p is not None and gm_t > gm_p),
        ("ประสิทธิภาพใช้สินทรัพย์ (asset turnover) ดีขึ้น", at_t is not None and at_p is not None and at_t > at_p),
    ]
    score = sum(1 for _, ok in checks if ok)
    return {"score": score, "max": 9, "fy": t,
            "checks": [{"label": lbl, "pass": ok} for lbl, ok in checks],
            "rating": "แข็งแรง" if score >= 7 else ("ปานกลาง" if score >= 4 else "อ่อนแอ")}


# ── 2) Altman Z-Score (บริษัทมหาชนทั่วไป) ─────────────────────────────────
def altman_z(S: dict, fys: list[int], market_cap: float | None) -> dict | None:
    if not fys:
        return None
    t = fys[-1]
    ta = S["total_assets"].get(t)
    ca, cl = S["current_assets"].get(t), S["current_liabilities"].get(t)
    re = S["retained_earnings"].get(t)
    ebit = S["operating_income"].get(t)
    tl = S["total_liabilities"].get(t)
    sales = S["revenue"].get(t)
    if not ta or ta == 0:
        return None
    x1 = _safe_div((ca - cl) if ca is not None and cl is not None else None, ta)
    x2 = _safe_div(re, ta)
    x3 = _safe_div(ebit, ta)
    x4 = _safe_div(market_cap, tl)
    x5 = _safe_div(sales, ta)
    if None in (x1, x2, x3, x4, x5):
        return None
    z = 1.2 * x1 + 1.4 * x2 + 3.3 * x3 + 0.6 * x4 + 1.0 * x5
    zone = "ปลอดภัย" if z > 2.99 else ("เฝ้าระวัง (เทา)" if z >= 1.81 else "เสี่ยงล้มละลาย")
    return {"z": round(z, 2), "zone": zone, "fy": t,
            "components": {"WC/TA": round(x1, 3), "RE/TA": round(x2, 3), "EBIT/TA": round(x3, 3),
                           "MktEq/TL": round(x4, 3), "Sales/TA": round(x5, 3)}}


# ── 3) Beneish M-Score (จับสัญญาณแต่งบัญชี) + Accruals ────────────────────
def beneish_m(S: dict, fys: list[int]) -> dict | None:
    if len(fys) < 2:
        return None
    t, p = fys[-1], fys[-2]
    g = lambda m, fy: S.get(m, {}).get(fy)  # noqa: E731

    def ratio(idx_num, idx_den):
        return _safe_div(idx_num, idx_den)

    dsri = _safe_div(ratio(g("receivables", t), g("revenue", t)), ratio(g("receivables", p), g("revenue", p)))
    gm_t, gm_p = ratio(g("gross_profit", t), g("revenue", t)), ratio(g("gross_profit", p), g("revenue", p))
    gmi = _safe_div(gm_p, gm_t)
    sgi = _safe_div(g("revenue", t), g("revenue", p))
    sgai = _safe_div(ratio(g("sga", t), g("revenue", t)), ratio(g("sga", p), g("revenue", p)))
    # Asset Quality Index (ต้องมี PPE — ถ้าไม่มีให้ 1.0 กลาง ๆ)
    aqi = 1.0
    ta_t, ta_p = g("total_assets", t), g("total_assets", p)
    ca_t, ca_p = g("current_assets", t), g("current_assets", p)
    ppe_t, ppe_p = g("ppe", t), g("ppe", p)
    if None not in (ta_t, ta_p, ca_t, ca_p, ppe_t, ppe_p) and ta_t and ta_p:
        q_t = 1 - (ca_t + ppe_t) / ta_t
        q_p = 1 - (ca_p + ppe_p) / ta_p
        aqi = _safe_div(q_t, q_p) or 1.0
    # Depreciation Index
    depi = 1.0
    da_t, da_p = g("da", t), g("da", p)
    if None not in (da_t, da_p, ppe_t, ppe_p) and (da_p + ppe_p) and (da_t + ppe_t):
        rate_p = da_p / (da_p + ppe_p)
        rate_t = da_t / (da_t + ppe_t)
        depi = _safe_div(rate_p, rate_t) or 1.0
    # Leverage Index
    lev_t = _safe_div((g("long_term_debt", t) or 0) + (g("current_liabilities", t) or 0), ta_t)
    lev_p = _safe_div((g("long_term_debt", p) or 0) + (g("current_liabilities", p) or 0), ta_p)
    lvgi = _safe_div(lev_t, lev_p) or 1.0
    # Total Accruals to Total Assets
    tata = _safe_div((g("net_income", t) or 0) - (g("ocf", t) or 0), ta_t)
    if None in (dsri, gmi, sgi, tata):
        return None
    m = (-4.84 + 0.92 * dsri + 0.528 * gmi + 0.404 * aqi + 0.892 * sgi
         + 0.115 * depi - 0.172 * sgai + 4.679 * tata - 0.327 * lvgi) if sgai is not None else None
    if m is None:
        return None
    flag = m > -1.78  # เกณฑ์ Beneish: สูงกว่า -1.78 = มีโอกาสแต่งบัญชี
    accruals = tata
    return {"m": round(m, 2), "fy": t, "manipulation_flag": flag,
            "verdict": "⚠️ มีสัญญาณแต่งบัญชี" if flag else "ไม่พบสัญญาณผิดปกติ",
            "accruals_ratio": round(accruals, 3) if accruals is not None else None,
            "components": {k: round(v, 2) for k, v in
                           {"DSRI": dsri, "GMI": gmi, "AQI": aqi, "SGI": sgi, "DEPI": depi,
                            "SGAI": sgai, "LVGI": lvgi, "TATA": tata}.items() if v is not None}}


# ── 4) Owner Earnings + FCF ──────────────────────────────────────────────
def owner_earnings(S: dict, fy: int) -> dict | None:
    ocf, capex, da = S["ocf"].get(fy), S["capex"].get(fy), S["da"].get(fy)
    if ocf is None or capex is None:
        return None
    # maintenance capex ≈ min(capex, ค่าเสื่อม) — ส่วนเกินถือเป็น capex เพื่อเติบโต (Buffett)
    maint = min(capex, da) if isinstance(da, (int, float)) else capex
    oe = ocf - maint
    fcf = ocf - capex
    return {"owner_earnings": oe, "fcf": fcf, "maintenance_capex": maint,
            "growth_capex": (capex - maint) if maint is not None else None, "fy": fy}


# ── 5) ROIC / WACC / Moat ────────────────────────────────────────────────
def roic_series(S: dict, fys: list[int]) -> list[dict]:
    out = []
    for fy in fys:
        oi = S["operating_income"].get(fy)
        tax = S["income_tax"].get(fy)
        intr = S["interest_expense"].get(fy)
        eq, cash = S["total_equity"].get(fy), S["cash"].get(fy)
        debt = _debt(S, fy)
        if oi is None or eq is None:
            continue
        pretax = oi - (intr or 0)
        eff_tax = 0.21
        if isinstance(tax, (int, float)) and pretax and pretax > 0:
            eff_tax = min(max(tax / pretax, 0.0), 0.45)
        nopat = oi * (1 - eff_tax)
        invested = eq + (debt or 0) - (cash or 0)
        r = _safe_div(nopat, invested)
        if r is not None:
            out.append({"fy": fy, "roic": r})
    return out


def wacc(S: dict, fy: int, market_cap: float | None, beta: float | None) -> float | None:
    beta = beta if isinstance(beta, (int, float)) and beta > 0 else _DEFAULT_BETA
    cost_equity = _RISK_FREE + beta * _EQUITY_PREMIUM
    debt = _debt(S, fy) or 0
    intr = S["interest_expense"].get(fy)
    tax = 0.21
    cost_debt = (intr / debt) if isinstance(intr, (int, float)) and debt else 0.05
    cost_debt = min(max(cost_debt, 0.01), 0.15)
    e = market_cap if isinstance(market_cap, (int, float)) and market_cap > 0 else None
    if e is None:
        return round(cost_equity, 4)
    total = e + debt
    we, wd = e / total, debt / total
    return round(we * cost_equity + wd * cost_debt * (1 - tax), 4)


def roe_series(S: dict, fys: list[int]) -> list[dict]:
    """ROE รายปี (กำไรสุทธิ/ส่วนของผู้ถือหุ้น) — ใช้แทน ROIC สำหรับกลุ่มการเงิน (ธนาคาร/ประกัน)
    ที่แนวคิด 'invested capital' แบบบริษัททั่วไปใช้ไม่ได้."""
    out = []
    for fy in fys:
        r = _safe_div(S["net_income"].get(fy), S["total_equity"].get(fy))
        if r is not None:
            out.append({"fy": fy, "roic": r})
    return out


def moat(S: dict, fys: list[int], market_cap: float | None, beta: float | None,
         is_financial: bool = False) -> dict | None:
    roics = roe_series(S, fys) if is_financial else roic_series(S, fys)
    if not roics:
        return None
    metric = "ROE" if is_financial else "ROIC"
    latest = roics[-1]["roic"]
    years_above = sum(1 for r in roics[-10:] if r["roic"] >= 0.15)
    w = wacc(S, fys[-1], market_cap, beta)
    spread = (latest - w) if w is not None else None
    # ความนิ่งของ gross margin 10 ปี = พลังตั้งราคา (ยิ่งนิ่ง+สูง = คูเมืองแข็ง)
    gms = [_safe_div(S["gross_profit"].get(fy), S["revenue"].get(fy)) for fy in fys[-10:]]
    gms = [x for x in gms if x is not None]
    gm_stability = None
    if len(gms) >= 3 and statistics.mean(gms) > 0:
        cv = statistics.pstdev(gms) / statistics.mean(gms)
        gm_stability = max(0.0, 1 - cv)   # 1 = นิ่งมาก
    # จำแนกความกว้างคูเมือง
    width = "ไม่มีคูเมืองชัด"
    if latest >= 0.15 and years_above >= 5 and (spread is None or spread > 0.03):
        width = "กว้าง (wide)"
    elif latest >= 0.10 and (spread is None or spread > 0):
        width = "แคบ (narrow)"
    return {"metric": metric, "roic_latest": round(latest, 4),
            "roic_avg": round(statistics.mean(r["roic"] for r in roics[-10:]), 4),
            "years_roic_above_15": years_above, "wacc": w,
            "roic_minus_wacc": round(spread, 4) if spread is not None else None,
            "gross_margin_stability": round(gm_stability, 3) if gm_stability is not None else None,
            "width": width, "series": [{"fy": r["fy"], "roic": round(r["roic"], 4)} for r in roics[-10:]]}


# ── 6) Reverse DCF — "ตลาดกำลังคิดว่าจะโตกี่ %" ───────────────────────────
def _dcf_value(e0: float, g: float, r: float, n: int = 10, g_t: float = _TERMINAL_GROWTH) -> float:
    pv = 0.0
    e = e0
    for i in range(1, n + 1):
        e *= (1 + g)
        pv += e / ((1 + r) ** i)
    terminal = (e * (1 + g_t)) / (r - g_t)
    pv += terminal / ((1 + r) ** n)
    return pv


def reverse_dcf(owner_earn: float | None, market_cap: float | None, r: float = _DISCOUNT_RATE) -> dict | None:
    """หา 'อัตราโตของ owner earnings' ที่ทำให้มูลค่า DCF = ราคาตลาดวันนี้ (ตลาดคิดว่าจะโตเท่านี้)."""
    if not isinstance(owner_earn, (int, float)) or owner_earn <= 0 or not market_cap or market_cap <= 0:
        return None
    lo, hi = -0.20, 0.60
    for _ in range(60):
        mid = (lo + hi) / 2
        val = _dcf_value(owner_earn, mid, r)
        if val > market_cap:
            hi = mid
        else:
            lo = mid
    return {"implied_growth": round((lo + hi) / 2, 4), "discount_rate": r,
            "owner_earnings": owner_earn, "market_cap": market_cap}


def intrinsic_owner_earnings(owner_earn: float | None, growth: float, market_cap: float | None,
                             shares: float | None, r: float = _DISCOUNT_RATE) -> dict | None:
    """มูลค่าที่ควรเป็น (intrinsic) จาก owner earnings ที่อัตราโตอนุรักษนิยม + margin of safety เทียบราคา."""
    if not isinstance(owner_earn, (int, float)) or owner_earn <= 0:
        return None
    g = max(min(growth, 0.12), 0.0)   # จำกัดไม่เกิน 12%/ปี (อนุรักษนิยม)
    fair_equity = _dcf_value(owner_earn, g, r)
    mos = _safe_div(fair_equity - market_cap, fair_equity) if market_cap else None
    return {"assumed_growth": g, "fair_value_total": round(fair_equity, 0),
            "fair_value_per_share": round(fair_equity / shares, 2) if shares else None,
            "margin_of_safety": round(mos, 3) if mos is not None else None}


# ── 7) Compounding Simulator ─────────────────────────────────────────────
def compounding(owner_earn: float | None, market_cap: float | None,
                base_growth: float, exit_multiple: float = 15.0, years: int = 10) -> dict | None:
    if not isinstance(owner_earn, (int, float)) or owner_earn <= 0 or not market_cap:
        return None
    scenarios = {}
    for name, g in (("แย่ (bear)", base_growth - 0.05), ("ฐาน (base)", base_growth),
                    ("ดี (bull)", base_growth + 0.05)):
        g = max(g, -0.05)
        future_oe = owner_earn * ((1 + g) ** years)
        future_val = future_oe * exit_multiple
        irr = (future_val / market_cap) ** (1 / years) - 1
        scenarios[name] = {"growth": round(g, 3), "future_value": round(future_val, 0), "irr": round(irr, 4)}
    return {"exit_multiple": exit_multiple, "years": years, "scenarios": scenarios}


# ── 8) Capital Allocation ────────────────────────────────────────────────
def capital_allocation(S: dict, fys: list[int]) -> dict | None:
    recent = fys[-5:]
    if not recent:
        return None
    tot_div = sum(abs(S["dividends_paid"].get(fy, 0) or 0) for fy in recent)
    tot_bb = sum(abs(S["buybacks"].get(fy, 0) or 0) for fy in recent)
    tot_capex = sum(abs(S["capex"].get(fy, 0) or 0) for fy in recent)
    tot_ni = sum(S["net_income"].get(fy, 0) or 0 for fy in recent)
    outlay = tot_div + tot_bb + tot_capex
    payout = _safe_div(tot_div, tot_ni) if tot_ni > 0 else None
    sh_first, sh_last = S["shares"].get(recent[0]), S["shares"].get(recent[-1])
    dilution = _safe_div((sh_last - sh_first) if sh_first and sh_last else None, sh_first)
    return {"years": len(recent),
            "reinvest_pct": round(_safe_div(tot_capex, outlay) or 0, 3),
            "buyback_pct": round(_safe_div(tot_bb, outlay) or 0, 3),
            "dividend_pct": round(_safe_div(tot_div, outlay) or 0, 3),
            "dividend_payout": round(payout, 3) if payout is not None else None,
            "share_change_pct": round(dilution, 3) if dilution is not None else None,
            "shareholder_friendly": dilution is not None and dilution <= 0.0}


# ── 9) Buffett Scorecard (รวม 4 เสา) ─────────────────────────────────────
def _cagr(series: dict[int, float], fys: list[int]) -> float | None:
    vals = [(fy, series.get(fy)) for fy in fys if isinstance(series.get(fy), (int, float)) and series.get(fy) > 0]
    if len(vals) < 2:
        return None
    (fy0, v0), (fy1, v1) = vals[0], vals[-1]
    yrs = fy1 - fy0
    if yrs <= 0 or v0 <= 0:
        return None
    return (v1 / v0) ** (1 / yrs) - 1


def scorecard(facts: dict, *, price: float | None = None, shares: float | None = None,
              market_cap: float | None = None, beta: float | None = None,
              sic: str | int | None = None) -> dict:
    unit = F.pick_reporting_unit(facts, _c(F._INCOME, "revenue")) if hasattr(F, "pick_reporting_unit") else "USD"
    S = extract_series(facts, unit)
    fys = sorted(S["revenue"].keys())
    if len(fys) < 2:
        raise ValueError("ข้อมูลงบรายปีจาก SEC ไม่พอสำหรับให้คะแนน Buffett (ต้องการอย่างน้อย 2 ปี)")

    # กลุ่มการเงิน (ธนาคาร/ประกัน/อสังหาลงทุน SIC 6000–6799): ROIC/Altman/asset-turnover ใช้ไม่ได้
    # → ใช้ ROE/มูลค่าตามบัญชีแทน และไม่หัก Altman ที่คำนวณไม่ได้
    try:
        sic_int = int(str(sic)) if sic is not None else None
    except (TypeError, ValueError):
        sic_int = None
    is_financial = sic_int is not None and 6000 <= sic_int <= 6799

    latest_fy = fys[-1]
    if not shares:
        shares = S["shares"].get(latest_fy)
    if not market_cap and price and shares:
        market_cap = price * shares

    pio = piotroski(S, fys)
    alt = None if is_financial else altman_z(S, fys, market_cap)
    ben = beneish_m(S, fys)
    oe = owner_earnings(S, latest_fy)
    mo = moat(S, fys, market_cap, beta, is_financial=is_financial)
    oe_val = oe["owner_earnings"] if oe else None
    rdcf = reverse_dcf(oe_val, market_cap)
    rev_cagr = _cagr(S["revenue"], fys)
    conservative_g = min(rev_cagr, 0.10) if rev_cagr is not None else 0.05
    intrinsic = intrinsic_owner_earnings(oe_val, conservative_g, market_cap, shares)
    comp = compounding(oe_val, market_cap, base_growth=(rev_cagr if rev_cagr is not None else 0.06))
    capalloc = capital_allocation(S, fys)

    # ── รวมคะแนน 4 เสา (เสาละ 0–25) ──
    def pillar_moat():
        if not mo:
            return 0, "ข้อมูลไม่พอ"
        s = 0
        s += 12 if mo["roic_latest"] >= 0.15 else (7 if mo["roic_latest"] >= 0.10 else (3 if mo["roic_latest"] >= 0.07 else 0))
        s += min(mo["years_roic_above_15"], 5) * 1.4
        if mo.get("gross_margin_stability") is not None:
            s += mo["gross_margin_stability"] * 6
        return round(min(s, 25), 1), mo["width"]

    def pillar_quality():
        s, notes = 0.0, []
        if is_financial:
            # ธนาคาร/ประกัน: Piotroski (asset turnover/current ratio) + Altman ใช้ไม่ได้ →
            # วัดคุณภาพจาก ROE ที่ยั่งยืน + Beneish (ตรวจแต่งบัญชี) แทน
            roe = mo["roic_latest"] if mo else None
            if roe is not None:
                s += 17 if roe >= 0.15 else (12 if roe >= 0.10 else (6 if roe >= 0.07 else 0))
                notes.append(f"ROE {roe*100:.0f}%")
            if ben:
                s += 0 if ben["manipulation_flag"] else 8
                notes.append("Beneish OK" if not ben["manipulation_flag"] else "⚠️Beneish")
            notes.append("Altman N/A")
            return round(min(s, 25), 1), " · ".join(notes) or "ข้อมูลไม่พอ"
        # บริษัททั่วไป: Piotroski (12) + Altman (8) + Beneish (5)
        if pio:
            s += pio["score"] / 9 * 12
            notes.append(f"Piotroski {pio['score']}/9")
        if alt:
            s += {"ปลอดภัย": 8, "เฝ้าระวัง (เทา)": 4}.get(alt["zone"], 0)
            notes.append(f"Altman {alt['z']}")
        if ben:
            s += 0 if ben["manipulation_flag"] else 5
            notes.append("Beneish OK" if not ben["manipulation_flag"] else "⚠️Beneish")
        return round(min(s, 25), 1), " · ".join(notes) or "ข้อมูลไม่พอ"

    def pillar_value():
        s, notes = 0, []
        mos = intrinsic.get("margin_of_safety") if intrinsic else None
        if mos is not None:
            s += 15 if mos >= 0.30 else (10 if mos >= 0.15 else (6 if mos >= 0 else 0))
            notes.append(f"MOS {mos*100:.0f}%")
        if rdcf and rev_cagr is not None:
            # ตลาดคาดโตต่ำกว่าที่บริษัทเคยทำ = มี room / ถูก
            gap = rev_cagr - rdcf["implied_growth"]
            s += 10 if gap > 0.02 else (6 if gap > -0.02 else 2)
            notes.append(f"ตลาดคิด {rdcf['implied_growth']*100:.1f}%")
        return round(min(s, 25), 1), " · ".join(notes) or "ข้อมูลไม่พอ"

    def pillar_capital():
        if not capalloc:
            return 0, "ข้อมูลไม่พอ"
        s = 0
        if capalloc["shareholder_friendly"]:
            s += 10
        if capalloc.get("dividend_payout") is not None and 0 < capalloc["dividend_payout"] < 0.8:
            s += 5
        if mo and mo["roic_latest"] >= 0.12:
            s += 10   # นำเงินไปลงทุนต่อได้ผลตอบแทนสูง = จัดสรรทุนเก่ง
        note = "เป็นมิตรผู้ถือหุ้น" if capalloc["shareholder_friendly"] else "มี dilution"
        return round(min(s, 25), 1), note

    p1, n1 = pillar_moat()
    p2, n2 = pillar_quality()
    p3, n3 = pillar_value()
    p4, n4 = pillar_capital()
    total = round(p1 + p2 + p3 + p4, 1)
    verdict = ("🟢 คุณภาพเยี่ยมแบบที่ Buffett ชอบ" if total >= 75 else
               "🟡 น่าสนใจ แต่มีจุดต้องระวัง" if total >= 55 else
               "🟠 พอใช้ ต้องศึกษาเพิ่ม" if total >= 40 else
               "🔴 ยังไม่เข้าเกณฑ์ VI คุณภาพ")

    return {
        "entity_name": facts.get("entityName"),
        "reporting_currency": unit,
        "is_financial": is_financial,
        "sector_note": ("กลุ่มการเงิน (ธนาคาร/ประกัน) — ใช้ ROE แทน ROIC และงด Altman Z "
                        "เพราะสูตรออกแบบมาสำหรับบริษัทนอกภาคการเงิน") if is_financial else None,
        "fy_range": [fys[0], fys[-1]],
        "price": price, "market_cap": market_cap, "shares": shares,
        "pillars": {
            "moat": {"score": p1, "max": 25, "note": n1, "detail": mo},
            "quality": {"score": p2, "max": 25, "note": n2,
                        "detail": {"piotroski": pio, "altman": alt, "beneish": ben}},
            "value": {"score": p3, "max": 25, "note": n3,
                      "detail": {"owner_earnings": oe, "reverse_dcf": rdcf, "intrinsic": intrinsic,
                                 "revenue_cagr": round(rev_cagr, 4) if rev_cagr is not None else None}},
            "capital_allocation": {"score": p4, "max": 25, "note": n4, "detail": capalloc},
        },
        "compounding": comp,
        "total_score": total, "max_score": 100, "verdict": verdict,
        "disclaimer": "คะแนนคำนวณจากงบ SEC จริง (auditable) เพื่อการศึกษา ไม่ใช่คำแนะนำการลงทุน — "
                      "ตัวเลขเชิงประมาณ (WACC/maintenance capex/reverse DCF) อิงสมมติฐานที่ระบุไว้",
    }


# ── ชั้น AI: จำแนกคูเมือง / Pre-mortem (Munger Invert) / Buffett×Munger Debate ──────
_AI_SYSTEMS = {
    "moat": (
        "คุณคือนักวิเคราะห์ VI จำแนก 'คูเมืองเศรษฐกิจ' (economic moat) ตามกรอบ Morningstar 5 แหล่ง: "
        "intangibles (แบรนด์/สิทธิบัตร/ใบอนุญาต), switching_costs (ต้นทุนย้ายค่าย), network_effect, "
        "cost_advantage (ต้นทุนต่ำโครงสร้าง), efficient_scale. วิเคราะห์จากคำอธิบายธุรกิจ + ตัวเลข ROIC ที่ให้. "
        "ตอบ JSON เท่านั้น: {sources:[{source, strength:'strong'|'moderate'|'weak'|'none', evidence(อ้างข้อเท็จจริง)}], "
        "durability:'high'|'medium'|'low', width:'wide'|'narrow'|'none', summary(1-2 ประโยคไทย)}. "
        "ยึดหลักฐาน ห้ามแต่ง ถ้าไม่มีหลักฐานให้ strength='none'."
    ),
    "premortem": (
        "คุณคือ Charlie Munger ใช้หลัก 'Invert, always invert'. สมมติว่าอีก 5-10 ปีการลงทุนนี้ล้มเหลว/มูลค่าลดฮวบ "
        "แล้วไล่ย้อนว่าเกิดจากอะไรได้บ้าง (จากความเสี่ยง+ตัวเลขที่ให้). ตอบ JSON เท่านั้น: "
        "{failure_modes:[{scenario, likelihood:'high'|'medium'|'low', early_warning(สัญญาณเตือนล่วงหน้าที่ควรจับตา)}], "
        "red_flags:[str], summary(1-2 ประโยคไทย)}. เน้นเหตุที่เป็นจริงเชิงธุรกิจ/การเงิน ห้ามแต่ง."
    ),
    "debate": (
        "จำลองบทสนทนาตัดสินใจลงทุนระหว่าง Warren Buffett (ฝ่ายเห็นโอกาส) กับ Charlie Munger (ฝ่ายระวังความเสี่ยง) "
        "จากคะแนน/ตัวเลขที่ให้. ตอบ JSON เท่านั้น: {buffett_case:[str (เหตุผลฝ่ายซื้อ 2-4 ข้อ)], "
        "munger_case:[str (เหตุผลฝ่ายระวัง 2-4 ข้อ)], key_swing_factor(ปัจจัยชี้ขาด), "
        "verdict:'น่าลงทุน'|'รอจังหวะ/ราคา'|'ผ่าน', reasoning(1-2 ประโยคไทย)}. อ้างตัวเลขจริง ไม่ให้คำแนะนำเด็ดขาด วิเคราะห์เชิงการศึกษา."
    ),
}


def _summarize_for_ai(sc: dict) -> str:
    P = sc.get("pillars", {})
    mo = (P.get("moat") or {}).get("detail") or {}
    q = (P.get("quality") or {}).get("detail") or {}
    v = (P.get("value") or {}).get("detail") or {}
    pio = (q.get("piotroski") or {})
    alt = (q.get("altman") or {})
    ben = (q.get("beneish") or {})
    iv = (v.get("intrinsic") or {})
    rd = (v.get("reverse_dcf") or {})
    return (
        f"บริษัท: {sc.get('entity_name')} | คะแนนรวม {sc.get('total_score')}/100 ({sc.get('verdict')})\n"
        f"คูเมือง {P.get('moat',{}).get('score')}/25: {mo.get('metric','ROIC')} ล่าสุด "
        f"{mo.get('roic_latest')}, ยืน>15% {mo.get('years_roic_above_15')} ปี, ROIC-WACC {mo.get('roic_minus_wacc')}, "
        f"ความนิ่งมาร์จิน {mo.get('gross_margin_stability')}, width={mo.get('width')}\n"
        f"คุณภาพ {P.get('quality',{}).get('score')}/25: Piotroski {pio.get('score')}/9, "
        f"Altman {alt.get('z')} ({alt.get('zone')}), Beneish {ben.get('m')} ({ben.get('verdict')})\n"
        f"มูลค่า {P.get('value',{}).get('score')}/25: owner earnings, margin of safety {iv.get('margin_of_safety')}, "
        f"ตลาดคาดโต {rd.get('implied_growth')}, รายได้เคยโต {v.get('revenue_cagr')}\n"
        f"การจัดสรรทุน {P.get('capital_allocation',{}).get('score')}/25"
    )


async def ai_analysis(symbol: str, mode: str, sc: dict) -> dict:
    """ต่อยอด scorecard ด้วย AI: จำแนกคูเมือง / pre-mortem / debate. ต้องตั้งค่า LLM ก่อน.
    reuse ai_analyst._run_llm (มี fallback provider) + edgar.get_10k_context (บริบทธุรกิจ/ความเสี่ยง)."""
    if mode not in _AI_SYSTEMS:
        raise ValueError("mode ต้องเป็น moat, premortem หรือ debate")
    from app.config import get_settings
    if not get_settings().llm_enabled():
        return {"error": "ยังไม่ได้ตั้งค่า AI (LLM) — เปิดใช้ได้ที่การตั้งค่า/คีย์ Gemini"}
    from app import ai_analyst, edgar

    summary = _summarize_for_ai(sc)
    ctx_txt = ""
    if mode in ("moat", "premortem"):
        try:
            ctx = await edgar.get_10k_context(symbol) or {}
            biz = (ctx.get("business") or "")[:4000]
            risk = (ctx.get("risk_factors") or "")[:3000]
            ctx_txt = f"\n\n[คำอธิบายธุรกิจจาก 10-K/20-F]\n{biz}"
            if mode == "premortem":
                ctx_txt += f"\n\n[ปัจจัยเสี่ยง]\n{risk}"
        except Exception:  # noqa: BLE001
            ctx_txt = ""
    try:
        result = await ai_analyst._run_llm(_AI_SYSTEMS[mode], summary + ctx_txt)
        return result if isinstance(result, dict) else {"error": "AI ตอบไม่เป็นรูปแบบที่ใช้ได้"}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"AI วิเคราะห์ไม่สำเร็จ: {exc}"}
