"""คำนวณมูลค่าที่แท้จริง (Intrinsic Value) ด้วยหลายโมเดล

โมเดลที่รัน:
  1. DCF (Discounted Cash Flow) — FCF forecast 9 ปี + Terminal Value
  2. Graham Number — √(22.5 × EPS × BVPS)
  3. Peter Lynch Fair Value — EPS × (EPS Growth% × 2)
  4. DDM (Dividend Discount Model) — เฉพาะหุ้นที่จ่ายปันผล
  5. P/E Fair Value — EPS × median sector PE

ข้อมูลมาจาก snapshot พื้นฐาน (yfinance/offline) + SEC EDGAR financials (สำหรับ FCF ย้อนหลัง)
"""
from __future__ import annotations

import math
import statistics
from typing import Any

# ── constants ────────────────────────────────────────────────────────────────

RISK_FREE_RATE = 0.043          # US 10Y Treasury ~4.3%
DEFAULT_WACC = 0.086            # Bond yield × 2
DEFAULT_PERPETUAL_GROWTH = 0.025
FORECAST_YEARS = 9


# ── helpers ──────────────────────────────────────────────────────────────────

def _safe(v: Any, default=None):
    """คืน None ถ้าค่าเป็น None / NaN / inf"""
    if v is None:
        return default
    try:
        f = float(v)
        return default if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return default


def _pct(v: Any) -> float | None:
    """แปลง fraction → percent ถ้าค่าอยู่ในช่วง -1..1"""
    f = _safe(v)
    if f is None:
        return None
    return f * 100 if abs(f) <= 1.5 else f


def _growth_from_financials(financials: dict | None) -> float | None:
    """คำนวณ median FCF growth จาก EDGAR financials (annual)"""
    if not financials:
        return None
    rows = financials.get("rows", [])
    # rows เรียงใหม่สุดก่อน → เก่าสุดท้าย
    fcf_series = []
    for r in rows:
        ocf = _safe(r.get("OperatingCashFlow") or r.get("operating_cash_flow"))
        capex = _safe(r.get("CapitalExpenditure") or r.get("capital_expenditure") or 0)
        if ocf is not None:
            fcf_series.append(ocf - abs(capex))
    if len(fcf_series) < 2:
        return None
    growths = []
    for i in range(len(fcf_series) - 1):
        prev = fcf_series[i + 1]
        curr = fcf_series[i]
        if prev > 0:
            growths.append((curr - prev) / prev)
    if not growths:
        return None
    valid = [g for g in growths if -1 < g < 5]
    return statistics.median(valid) if valid else None


# ── Model 1: DCF ─────────────────────────────────────────────────────────────

def run_dcf(
    base_fcf: float,
    shares: float,
    cash: float,
    debt: float,
    current_price: float,
    growth_1_5: float,
    growth_6_9: float,
    wacc: float = DEFAULT_WACC,
    perpetual_growth: float = DEFAULT_PERPETUAL_GROWTH,
) -> dict:
    warnings: list[str] = []

    if wacc <= perpetual_growth:
        warnings.append("WACC ต้องมากกว่า Perpetual Growth — สมมติฐานไม่สมเหตุสมผล")
    if base_fcf <= 0:
        warnings.append("Base FCF ติดลบ — DCF อาจไม่เหมาะกับบริษัทนี้")
    if growth_1_5 > 0.5:
        warnings.append("Growth Rate ปี 1-5 สูงกว่า 50% — ตรวจสอบสมมติฐาน")

    forecast_rows = []
    prev_fcf = base_fcf
    sum_pv = 0.0
    for i in range(1, FORECAST_YEARS + 1):
        g = growth_1_5 if i <= 5 else growth_6_9
        fcf = prev_fcf * (1 + g)
        pv = fcf / (1 + wacc) ** i
        forecast_rows.append({"year": i, "fcf": round(fcf, 0), "pv": round(pv, 0)})
        sum_pv += pv
        prev_fcf = fcf

    last_fcf = forecast_rows[-1]["fcf"]
    tv = last_fcf * (1 + perpetual_growth) / (wacc - perpetual_growth)
    pv_tv = tv / (1 + wacc) ** FORECAST_YEARS
    ev = sum_pv + pv_tv
    equity = ev + cash - debt
    iv = equity / shares if shares > 0 else 0
    upside = (iv - current_price) / current_price * 100 if current_price > 0 else 0
    mos = (iv - current_price) / iv * 100 if iv > 0 else None
    tv_pct = pv_tv / ev * 100 if ev > 0 else 0

    if tv_pct > 70:
        warnings.append(f"Terminal Value = {tv_pct:.0f}% ของ EV — ผลขึ้นกับสมมติฐานระยะยาวสูงมาก")
    if debt > cash * 3:
        warnings.append("Total Debt สูงกว่า Cash มากกว่า 3× — ความเสี่ยงโครงสร้างทุนสูง")

    return {
        "model": "DCF",
        "intrinsic_value": round(iv, 2),
        "upside_pct": round(upside, 1),
        "margin_of_safety_pct": round(mos, 1) if mos is not None else None,
        "enterprise_value": round(ev, 0),
        "pv_fcf": round(sum_pv, 0),
        "pv_terminal": round(pv_tv, 0),
        "tv_pct_of_ev": round(tv_pct, 1),
        "forecast_rows": forecast_rows,
        "assumptions": {
            "base_fcf": round(base_fcf, 0),
            "growth_1_5": round(growth_1_5 * 100, 1),
            "growth_6_9": round(growth_6_9 * 100, 1),
            "wacc": round(wacc * 100, 1),
            "perpetual_growth": round(perpetual_growth * 100, 1),
        },
        "warnings": warnings,
        "status": _valuation_status(upside),
    }


# ── Model 2: Graham Number ────────────────────────────────────────────────────

def run_graham(eps: float, bvps: float, current_price: float) -> dict:
    warnings: list[str] = []
    if eps <= 0 or bvps <= 0:
        warnings.append("EPS หรือ Book Value ติดลบ — Graham Number ใช้ไม่ได้กับบริษัทนี้")
        return {"model": "Graham Number", "intrinsic_value": None, "warnings": warnings,
                "status": "N/A", "upside_pct": None, "margin_of_safety_pct": None}
    iv = math.sqrt(22.5 * eps * bvps)
    upside = (iv - current_price) / current_price * 100 if current_price > 0 else 0
    mos = (iv - current_price) / iv * 100 if iv > 0 else None
    if iv > current_price * 3:
        warnings.append("Graham Number สูงกว่าราคาปัจจุบันมากกว่า 3× — อาจเหมาะกับหุ้น deep value เท่านั้น")
    return {
        "model": "Graham Number",
        "intrinsic_value": round(iv, 2),
        "upside_pct": round(upside, 1),
        "margin_of_safety_pct": round(mos, 1) if mos is not None else None,
        "formula": f"√(22.5 × EPS {eps:.2f} × BVPS {bvps:.2f})",
        "warnings": warnings,
        "status": _valuation_status(upside),
    }


# ── Model 3: Peter Lynch Fair Value & Score ───────────────────────────────────

def run_peter_lynch(eps: float, pe: float, eps_growth_pct: float,
                    div_yield_pct: float, current_price: float) -> dict:
    warnings: list[str] = []
    # Fair Value = EPS × (EPS growth% × 2)  — Lynch's "PEG 1 = fair"
    if eps > 0 and eps_growth_pct > 0:
        fair_value = eps * (eps_growth_pct * 2)
        upside = (fair_value - current_price) / current_price * 100 if current_price > 0 else 0
        mos = (fair_value - current_price) / fair_value * 100 if fair_value > 0 else None
    else:
        fair_value = None
        upside = None
        mos = None
        warnings.append("EPS หรือ EPS Growth ≤ 0 — ไม่สามารถคำนวณ Lynch Fair Value ได้")

    # Peter Lynch Score (PEG ratio แบบ Lynch)
    if pe > 0:
        score = (eps_growth_pct + div_yield_pct) / pe
        if score < 1:
            score_status = "Overvalued"
        elif score <= 1.5:
            score_status = "Fairly Valued"
        elif score <= 2.5:
            score_status = "Undervalued"
        else:
            score_status = "Very Undervalued"
    else:
        score = None
        score_status = "N/A"
        warnings.append("P/E ไม่เป็นบวก — ไม่สามารถคำนวณ Peter Lynch Score ได้")

    return {
        "model": "Peter Lynch",
        "intrinsic_value": round(fair_value, 2) if fair_value else None,
        "upside_pct": round(upside, 1) if upside is not None else None,
        "margin_of_safety_pct": round(mos, 1) if mos is not None else None,
        "lynch_score": round(score, 2) if score is not None else None,
        "lynch_score_status": score_status,
        "formula": f"EPS {eps:.2f} × (growth {eps_growth_pct:.1f}% × 2)",
        "warnings": warnings,
        "status": _valuation_status(upside) if upside is not None else "N/A",
    }


# ── Model 4: DDM (Dividend Discount) ─────────────────────────────────────────

def run_ddm(dps: float, growth: float, current_price: float,
            discount_rate: float = DEFAULT_WACC) -> dict:
    warnings: list[str] = []
    if dps <= 0:
        return {"model": "DDM", "intrinsic_value": None, "warnings": ["หุ้นนี้ไม่จ่ายปันผล — DDM ใช้ไม่ได้"],
                "status": "N/A", "upside_pct": None, "margin_of_safety_pct": None}
    if discount_rate <= growth:
        warnings.append("Discount rate ≤ growth rate — ปรับ growth ลงให้ต่ำกว่า discount rate")
        growth = discount_rate * 0.5
    iv = dps * (1 + growth) / (discount_rate - growth)
    upside = (iv - current_price) / current_price * 100 if current_price > 0 else 0
    mos = (iv - current_price) / iv * 100 if iv > 0 else None
    return {
        "model": "DDM",
        "intrinsic_value": round(iv, 2),
        "upside_pct": round(upside, 1),
        "margin_of_safety_pct": round(mos, 1) if mos is not None else None,
        "formula": f"DPS {dps:.2f} × (1+{growth*100:.1f}%) / ({discount_rate*100:.1f}% - {growth*100:.1f}%)",
        "warnings": warnings,
        "status": _valuation_status(upside),
    }


# ── Model 5: P/E Fair Value ───────────────────────────────────────────────────

def run_pe_fair_value(eps: float, target_pe: float, current_price: float) -> dict:
    if eps <= 0 or target_pe <= 0:
        return {"model": "P/E Fair Value", "intrinsic_value": None,
                "warnings": ["EPS หรือ target P/E ≤ 0"], "status": "N/A",
                "upside_pct": None, "margin_of_safety_pct": None}
    iv = eps * target_pe
    upside = (iv - current_price) / current_price * 100 if current_price > 0 else 0
    mos = (iv - current_price) / iv * 100 if iv > 0 else None
    return {
        "model": "P/E Fair Value",
        "intrinsic_value": round(iv, 2),
        "upside_pct": round(upside, 1),
        "margin_of_safety_pct": round(mos, 1) if mos is not None else None,
        "formula": f"EPS {eps:.2f} × target P/E {target_pe:.1f}",
        "warnings": [],
        "status": _valuation_status(upside),
    }


# ── Status helper ─────────────────────────────────────────────────────────────

def _valuation_status(upside_pct: float | None) -> str:
    if upside_pct is None:
        return "N/A"
    if upside_pct > 50:
        return "Strongly Undervalued"
    if upside_pct > 20:
        return "Undervalued"
    if upside_pct > -10:
        return "Fairly Valued"
    if upside_pct > -30:
        return "Overvalued"
    return "Highly Overvalued"


# ── Sensitivity Heatmap ───────────────────────────────────────────────────────

def sensitivity_heatmap(
    base_fcf: float, shares: float, cash: float, debt: float, current_price: float,
    growth_1_5: float, growth_6_9: float,
) -> dict:
    """WACC (แกน Y) × Perpetual Growth (แกน X) → IV per share"""
    waccs = [0.07, 0.08, 0.086, 0.09, 0.10, 0.11]
    perps = [0.010, 0.015, 0.020, 0.025, 0.030, 0.035]
    rows = []
    for w in waccs:
        row = []
        for p in perps:
            if w <= p:
                row.append(None)
            else:
                r = run_dcf(base_fcf, shares, cash, debt, current_price,
                            growth_1_5, growth_6_9, wacc=w, perpetual_growth=p)
                row.append(r["intrinsic_value"])
        rows.append({"wacc": round(w * 100, 1), "values": row})
    return {"wacc_axis": [round(w * 100, 1) for w in waccs],
            "perp_axis": [round(p * 100, 1) for p in perps],
            "rows": rows,
            "current_price": current_price}


# ── Main entry point ──────────────────────────────────────────────────────────

def build_iv_report(snapshot: dict, financials: dict | None = None) -> dict:
    """สร้าง IV report จาก snapshot (yfinance/offline) + optional SEC financials"""
    price = _safe(snapshot.get("price") or snapshot.get("current_price")) or 0
    eps = _safe(snapshot.get("eps")) or 0
    pe = _safe(snapshot.get("pe")) or 0
    bvps = _safe(snapshot.get("book_value_per_share") or snapshot.get("bvps")) or 0
    shares = _safe(snapshot.get("shares_outstanding") or snapshot.get("shares")) or 0
    cash = _safe(snapshot.get("cash") or snapshot.get("total_cash")) or 0
    debt = _safe(snapshot.get("total_debt") or snapshot.get("debt")) or 0
    market_cap = _safe(snapshot.get("market_cap")) or 0
    fcf = _safe(snapshot.get("free_cash_flow") or snapshot.get("fcf")) or 0
    rev_growth = _safe(snapshot.get("revenue_growth")) or 0
    earn_growth = _safe(snapshot.get("earnings_growth")) or 0
    div_yield = _safe(snapshot.get("dividend_yield")) or 0
    dps = _safe(snapshot.get("dividend_rate") or snapshot.get("dps")) or 0

    # EPS growth: ใช้จาก snapshot ก่อน ถ้าไม่มีใช้ revenue_growth
    eps_growth = _safe(snapshot.get("eps_growth") or earn_growth or rev_growth) or 0
    eps_growth_pct = _pct(eps_growth) or 0

    # FCF base: ใช้จาก snapshot โดยตรง ถ้า 0 ลองคำนวณจาก market_cap/pe
    base_fcf = fcf
    if base_fcf <= 0 and market_cap > 0 and pe > 0 and eps > 0:
        base_fcf = eps * shares  # ประมาณ net income แทน

    # Growth สำหรับ DCF
    hist_growth = _growth_from_financials(financials)
    raw_growth = hist_growth or _safe(rev_growth) or 0.10
    # cap growth ที่ 35% (ปี 1-5) และ 20% (ปี 6-9)
    growth_1_5 = max(0.02, min(raw_growth, 0.35))
    growth_6_9 = max(0.01, min(raw_growth * 0.5, 0.20))

    models: list[dict] = []

    # DCF
    if base_fcf > 0 and shares > 0:
        models.append(run_dcf(base_fcf, shares, cash, debt, price, growth_1_5, growth_6_9))
    else:
        models.append({"model": "DCF", "intrinsic_value": None, "status": "N/A",
                       "upside_pct": None, "margin_of_safety_pct": None,
                       "warnings": ["ไม่มีข้อมูล FCF หรือจำนวนหุ้น — ไม่สามารถรัน DCF ได้"]})

    # Graham Number
    models.append(run_graham(eps, bvps, price))

    # Peter Lynch
    models.append(run_peter_lynch(eps, pe, eps_growth_pct,
                                  _pct(div_yield) or 0, price))

    # DDM (เฉพาะหุ้นจ่ายปันผล)
    models.append(run_ddm(dps, max(0.01, min(eps_growth_pct / 100, 0.08)), price))

    # P/E Fair Value (ใช้ PE เฉลี่ย 15× สำหรับ value / หรือ PE ปัจจุบันเป็น reference)
    sector_pe = _safe(snapshot.get("sector_pe") or snapshot.get("forward_pe")) or 15.0
    models.append(run_pe_fair_value(eps, sector_pe, price))

    # สรุปช่วง IV จากโมเดลที่มีค่า
    valid_ivs = [m["intrinsic_value"] for m in models if m.get("intrinsic_value") is not None]
    if valid_ivs:
        iv_min = min(valid_ivs)
        iv_max = max(valid_ivs)
        iv_median = round(statistics.median(valid_ivs), 2)
        mos_median = round((iv_median - price) / iv_median * 100, 1) if iv_median > 0 else None
    else:
        iv_min = iv_max = iv_median = mos_median = None

    # Sensitivity heatmap (DCF เท่านั้น)
    heatmap = None
    dcf_model = next((m for m in models if m["model"] == "DCF" and m.get("intrinsic_value")), None)
    if dcf_model and base_fcf > 0 and shares > 0:
        heatmap = sensitivity_heatmap(base_fcf, shares, cash, debt, price, growth_1_5, growth_6_9)

    return {
        "symbol": snapshot.get("symbol", "").upper(),
        "long_name": snapshot.get("long_name"),
        "current_price": price,
        "currency": snapshot.get("currency", "USD"),
        "models": models,
        "summary": {
            "iv_min": round(iv_min, 2) if iv_min is not None else None,
            "iv_max": round(iv_max, 2) if iv_max is not None else None,
            "iv_median": iv_median,
            "margin_of_safety_pct": mos_median,
            "status": _valuation_status((iv_median - price) / price * 100 if iv_median and price else None),
            "models_count": len(valid_ivs),
        },
        "heatmap": heatmap,
        "data_used": {
            "eps": eps, "pe": pe, "bvps": bvps, "base_fcf": round(base_fcf, 0),
            "shares": round(shares / 1e6, 2), "cash": round(cash / 1e9, 2),
            "debt": round(debt / 1e9, 2), "growth_1_5_pct": round(growth_1_5 * 100, 1),
            "growth_6_9_pct": round(growth_6_9 * 100, 1),
        },
    }
