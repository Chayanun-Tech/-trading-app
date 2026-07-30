"""Revenue Growth Model — กราฟสไตล์ TrendSpider: ขั้นบันได YoY revenue growth รายไตรมาส
+ P/E ย้อนหลังรายสัปดาห์ ใต้กราฟราคา

ใช้ SEC EDGAR (รายได้/EPS รายไตรมาสจาก companyfacts XBRL) + ราคาย้อนหลังจาก provider ปัจจุบัน
(ปกติคือ Yahoo — resample เป็นรายสัปดาห์เอง). รองรับเฉพาะหุ้นสหรัฐที่ยื่น SEC.

หมายเหตุสำคัญ: หลายบริษัท (เช่น Apple) ยื่น 10-Q แค่ 3 ไตรมาส/ปี — ไตรมาสที่ 4 ไม่ได้ยื่นแยก
(รวมอยู่ใน 10-K ประจำปีเท่านั้น) ถ้านับ YoY แบบ "ถอยไป 4 รายการในลิสต์" จะเพี้ยนทันทีเพราะข้อมูล
มีแค่ 3 จุด/ปี ไม่ใช่ 4 — โมดูลนี้จึงสังเคราะห์ไตรมาสที่ขาด (FY 10-K − 3 ไตรมาสที่มี) และจับคู่ YoY
ด้วย fp/fy จริง (ไม่ใช่นับตำแหน่งในลิสต์) เพื่อกันคลาดเคลื่อน.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app import edgar
from app import sector_profile
from app import sector_metrics
from app.financials import (_entries, _days, _INCOME, _CASHFLOW, _EPS, _SHARES,
                             _is_annual_form, _annual_fp_ok, pick_reporting_unit)
from app.fundamentals import is_equity_symbol

# แท็ก us-gaap สำหรับ heuristic จำแนก sector (ชั้น 2) — ธนาคารดูรายได้ดอกเบี้ย, REIT ดูค่าเสื่อม
_INTEREST_INCOME_CONCEPTS = ["InterestAndDividendIncomeOperating", "InterestAndFeeIncomeLoansAndLeases",
                             "InterestIncomeOperating", "RevenuesNetOfInterestExpense"]
_DEPRECIATION_CONCEPTS = ["DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet",
                          "DepreciationAndAmortization", "Depreciation"]

# แท็กสำหรับ metric เฉพาะ sector (ชั้น 3 ส่วนขยาย)
_EQUITY_CONCEPTS = ["StockholdersEquity"]
_PREFERRED_CONCEPTS = ["PreferredStockValue", "PreferredStockValueOutstanding"]
_CASH_CONCEPTS = ["CashAndCashEquivalentsAtCarryingValue",
                  "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"]
_GAINS_ON_SALE_CONCEPTS = ["GainLossOnDispositionOfRealEstate",
                           "GainLossOnSaleOfPropertiesNetOfApplicableIncomeTaxes",
                           "GainLossOnDispositionOfProperty", "GainLossOnSaleOfPropertyPlantEquipment",
                           "GainLossOnDispositionOfAssets1", "GainsLossesOnSalesOfInvestmentRealEstate"]
_OPERATING_INCOME_CONCEPTS = next(c[2] for c in _INCOME if c[0] == "operating_income")
_GROSS_PROFIT_CONCEPTS = next(c[2] for c in _INCOME if c[0] == "gross_profit")

_REVENUE_CONCEPTS = next(c[2] for c in _INCOME if c[0] == "revenue")
_NET_INCOME_CONCEPTS = next(c[2] for c in _INCOME if c[0] == "net_income")
_OCF_CONCEPTS = next(c[2] for c in _CASHFLOW if c[0] == "operating_cash_flow")
_CAPEX_CONCEPTS = next(c[2] for c in _CASHFLOW if c[0] == "capex")
_ALL_FP = {"Q1", "Q2", "Q3", "Q4"}

# จำนวนแท่งราคาโดยประมาณต่อ 1 ไตรมาส แยกตามความละเอียด — ใช้ตัดช่วงราคา/P/E ให้พอดีกับไตรมาสที่แสดง
_BARS_PER_QUARTER = {"daily": 63, "weekly": 13, "monthly": 3}


def _latest_instant(facts: dict, concepts: list[str], unit: str) -> float | None:
    """ค่างบดุลล่าสุด (instant fact — มีแต่ end ไม่มี start เช่น สินทรัพย์รวม/ส่วนของผู้ถือหุ้น)."""
    best: tuple[str, float] | None = None
    for e in _entries(facts, concepts, unit):
        if "start" in e:  # duration (งบกำไร/กระแสเงินสด) ไม่ใช่ยอดคงเหลือ ณ วันสิ้นงวด
            continue
        end = e.get("end")
        if not end:
            continue
        if best is None or end > best[0]:
            best = (end, e["val"])
    return best[1] if best else None


def _financials_snapshot(facts: dict, unit: str = "USD") -> dict:
    """งบย่อสำหรับ heuristic จำแนก sector (ชั้น 2). ใช้ยอด 'ทั้งปี' (10-K) ล่าสุด ไม่ใช่ TTM
    ที่สังเคราะห์รายไตรมาส — เพราะการจำแนก sector ต้องการความ 'เสถียร/ถูก' มากกว่าความ 'สด'
    และยอดสังเคราะห์รายไตรมาส (Q4 = ทั้งปี − 3 ไตรมาส, capex แบบสะสม YTD) มี noise พอที่จะดัน
    อัตราส่วน capex/รายได้ ให้เพี้ยนจนจำแนกผิดได้ (เช่นบริษัทซอฟต์แวร์ที่ลงทุน datacenter หนัก
    ถูกจับเป็น 'ลงทุนหนัก' ทั้งที่ P/E, FCF ยังใช้ได้ปกติ). ทุก field คืน None ได้ (heuristic รองรับ)."""
    def latest_annual(concepts: list[str]) -> float | None:
        ann = _annual_with_end(facts, concepts, unit)
        return ann[max(ann)]["val"] if ann else None

    return {
        "revenue": latest_annual(_REVENUE_CONCEPTS),
        "net_income": latest_annual(_NET_INCOME_CONCEPTS),
        "ocf": latest_annual(_OCF_CONCEPTS),
        "capex": latest_annual(_CAPEX_CONCEPTS),
        "total_assets": _latest_instant(facts, ["Assets", "Assets"], unit),
        "total_equity": _latest_instant(facts, ["StockholdersEquity",
                                                "EquityAttributableToOwnersOfParent", "Equity"], unit),
        "interest_income": latest_annual(_INTEREST_INCOME_CONCEPTS),
        "depreciation": latest_annual(_DEPRECIATION_CONCEPTS),
    }


# ── ชั้น 3 ส่วนขยาย: metric เฉพาะ sector + เส้นมูลค่ายุติธรรมตาม primary metric ──────────
def _instants_by_end(facts: dict, concepts: list[str], unit: str) -> dict[str, float]:
    """ยอดคงเหลืองบดุล (instant) ทุกวันสิ้นงวด {end: val} — ค่าที่ยื่นหลังสุดชนะ (ปรับปรุงแล้ว)."""
    out: dict[str, float] = {}
    for e in _entries(facts, concepts, unit):
        if "start" in e:  # duration ไม่ใช่ยอดคงเหลือ
            continue
        end = e.get("end")
        if end:
            out[end] = e["val"]
    return out


def _fmt_money(v: float | None) -> str | None:
    if not isinstance(v, (int, float)):
        return None
    a = abs(v)
    if a >= 1e12:
        return f"${v / 1e12:.2f}T"
    if a >= 1e9:
        return f"${v / 1e9:.2f}B"
    if a >= 1e6:
        return f"${v / 1e6:.1f}M"
    return f"${v:,.0f}"


def _bvps_points(facts: dict) -> list[dict]:
    """มูลค่าตามบัญชีต่อหุ้นรายปี (ธนาคาร/ประกัน) — จับคู่ส่วนของผู้ถือหุ้น ณ วันสิ้นปีงบกับจำนวนหุ้น."""
    ann_shares = _annual_shares(facts)
    eq = _instants_by_end(facts, _EQUITY_CONCEPTS, "USD")
    pref = _instants_by_end(facts, _PREFERRED_CONCEPTS, "USD")
    pts = []
    for fy in sorted(ann_shares):
        end, sh = ann_shares[fy]["end"], ann_shares[fy]["val"]
        ps = sector_metrics.book_value_per_share(eq.get(end), pref.get(end), sh)
        if ps and ps > 0:
            pts.append({"period": end, "per_share": ps})
    return pts


def _ffo_points(facts: dict) -> list[dict]:
    """FFO ต่อหุ้นรายปี (REIT) = (กำไรสุทธิ + ค่าเสื่อม − กำไรขายทรัพย์สิน) ÷ จำนวนหุ้น."""
    ann_ni = _annual_with_end(facts, _NET_INCOME_CONCEPTS, "USD")
    ann_dep = _annual_with_end(facts, _DEPRECIATION_CONCEPTS, "USD")
    ann_gain = _annual_with_end(facts, _GAINS_ON_SALE_CONCEPTS, "USD")
    ann_shares = _annual_shares(facts)
    pts = []
    for fy in sorted(ann_ni):
        sh = ann_shares.get(fy, {}).get("val")
        if not sh:
            continue
        f = sector_metrics.ffo(ann_ni[fy]["val"], ann_dep.get(fy, {}).get("val"),
                               ann_gain.get(fy, {}).get("val", 0.0))
        if f is not None and f > 0:
            pts.append({"period": ann_ni[fy]["end"], "per_share": f / sh})
    return pts


def _annual_shares(facts: dict) -> dict[int, dict]:
    """จำนวนหุ้นถัวเฉลี่ยรายปี (10-K) ที่แก้ปัญหา "หน่วยไม่ตรงกันข้ามปี" แล้ว.

    บางบริษัทยื่นจำนวนหุ้นเป็น "พันหุ้น" ในงบเก่าและเป็น "หุ้น" ในงบใหม่ (เช่น ConocoPhillips:
    FY2020 = 1,078,030 แต่ FY2022 = 1,278,163,000 ทั้งที่จำนวนหุ้นจริงใกล้เคียงกัน) ทำให้ค่าต่อหุ้น
    ของปีเก่าใหญ่เกินจริง 1,000 เท่า — ค่าเฉลี่ยหลายปี (normalized FCF/share) จึงระเบิด และราคายุติธรรม
    ที่คำนวณต่อจากนั้นเพี้ยนไปหลักหมื่นเปอร์เซ็นต์.

    วิธีแก้: ยึดปีล่าสุด (งบใหม่สุด = หน่วยที่ตรงกับจำนวนหุ้นจริงวันนี้) เป็นหลัก แล้วปรับเฉพาะปีที่
    ต่างกันระดับ 1,000 เท่า (100–10,000 เท่า) เท่านั้น — ไม่แตะความต่างระดับสิบเท่า เพราะนั่นคือการ
    split หุ้นจริง (เช่น 20:1) ที่ต้องคงไว้ตามที่บริษัทยื่น."""
    ann = _annual_with_end(facts, _SHARES, "shares")
    if len(ann) < 2:
        return ann
    ref = ann[max(ann)]["val"]
    if not ref or ref <= 0:
        return ann
    out: dict[int, dict] = {}
    for fy, m in ann.items():
        v = m["val"]
        if v and v > 0:
            if 100 <= ref / v <= 10_000:
                v *= 1000        # ปีนี้ยื่นเป็น "พันหุ้น" — คูณกลับเป็นจำนวนหุ้นจริง
            elif 100 <= v / ref <= 10_000:
                v /= 1000        # กรณีกลับกัน (งบเก่ายื่นเป็นหุ้น งบใหม่ยื่นเป็นพันหุ้น)
        out[fy] = {**m, "val": v}
    return out


def _fcf_annual_points(facts: dict) -> list[dict]:
    """กระแสเงินสดอิสระรายปีจาก 10-K (OCF − CapEx) พร้อมต่อหุ้น — ใช้ทั้งใน get_revenue_model
    (เป็น fallback ของเส้น P/FCF) และในตัวสแกนมูลค่าทั้ง S&P 500 (value_scanner)."""
    ann_ocf = _annual_with_end(facts, _OCF_CONCEPTS, "USD")
    ann_capex = _annual_with_end(facts, _CAPEX_CONCEPTS, "USD")
    ann_shares = _annual_shares(facts)
    out = []
    for fy in sorted(set(ann_ocf) & set(ann_capex)):
        fcf_val = ann_ocf[fy]["val"] - ann_capex[fy]["val"]
        shares_val = ann_shares.get(fy, {}).get("val")
        out.append({
            "period": ann_ocf[fy]["end"], "fy": fy, "fcf": fcf_val,
            "fcf_per_share": (fcf_val / shares_val) if shares_val else None,
        })
    return out


def _multiple_band(bars: list[dict], points: list[dict], bars_per_q: int) -> dict | None:
    """median + IQR ของ (ราคา ÷ metric ต่อหุ้น) จากอดีต ~2 ปีล่าสุด — ใช้หา 'ตัวคูณ' ที่ตลาดให้."""
    if not bars or len(points) < 2:
        return None
    ratios = []
    for w in bars:
        wd = datetime.fromtimestamp(w["time"], tz=timezone.utc).date().isoformat()
        entry = next((p for p in reversed(points) if p["period"] <= wd and p["per_share"] > 0), None)
        if entry:
            ratios.append(w["close"] / entry["per_share"])
    return sector_profile.median_band(ratios[-(bars_per_q * 8):])


def _fair_from_band(latest_ps: float | None, band: dict | None, basis: str,
                    current_price: float | None) -> dict | None:
    """ราคายุติธรรม = metric ต่อหุ้นล่าสุด × ตัวคูณ median (+ ช่วง p25–p75 เป็น band)."""
    if not band or not latest_ps or latest_ps <= 0:
        return None
    fair = latest_ps * band["median"]
    fv = {
        "basis": basis,
        "per_share": round(latest_ps, 2),
        "median_multiple": round(band["median"], 1),
        "fair_price": round(fair, 2),
        "low": round(latest_ps * band["p25"], 2),
        "high": round(latest_ps * band["p75"], 2),
    }
    if current_price:
        fv["current_price"] = round(current_price, 2)
        fv["upside_pct"] = round((fair / current_price - 1) * 100, 1)
    return fv


def _metric(label: str, display: str | None, hint: str = "") -> dict:
    return {"label": label, "display": display if display is not None else "—", "hint": hint}


def _ttm_eps_latest(facts: dict) -> float | None:
    """EPS แบบ TTM (ผลรวมกำไรต่อหุ้น 4 ไตรมาสล่าสุด) — เป็นฐาน P/E มาตรฐาน (trailing) ที่เว็บการเงิน
    ทั่วไปใช้ (Yahoo/Google) ตรวจแล้วว่าตรงกับ Yahoo trailing EPS. ต่างจาก 'EPS ปีงบล่าสุด' ที่ล้าช้ากว่า
    (ยังไม่รวมไตรมาสใหม่หลังปิดปีงบ) ทำให้ P/E สูงเกินจริงสำหรับบริษัทที่กำไรกำลังโต. สังเคราะห์ไตรมาส
    ที่ SEC ไม่แยกยื่น (มัก Q4) จากงบทั้งปีก่อน แล้วรวม 4 ไตรมาสติดกันล่าสุด. คืน None ถ้าไตรมาสไม่พอ."""
    q = _synthesize_missing_quarter(
        _quarterly_with_fp(facts, _EPS, "USD/shares", prefer_latest_value=True),
        _annual_with_end(facts, _EPS, "USD/shares", prefer_latest_value=True),
    )
    dates = sorted(q)
    if len(dates) < 4:
        return None
    return sum(q[d]["val"] for d in dates[-4:])


def _compute_sector_extras(profile_key: str, facts: dict, bars: list[dict], bars_per_q: int,
                           fin: dict, fcf_annual: list[dict], pe_band: dict | None,
                           pfcf_band: dict | None) -> tuple[list[dict], dict | None, list[str]]:
    """คำนวณ extra metrics (ตัวเลขจริง) + ราคายุติธรรมตาม primary metric ของ sector.
    คืน (extra_metrics, fair_value, extra_warnings)."""
    price = bars[-1]["close"] if bars else None
    metrics: list[dict] = []
    fair: dict | None = None
    warns: list[str] = []

    def pct(v):
        return f"{v * 100:.1f}%" if isinstance(v, (int, float)) else None

    if profile_key in ("bank", "insurance"):
        pts = _bvps_points(facts)
        if pts:
            gate = sector_profile.validate_anchor([{"value": p["per_share"], "date": p["period"]} for p in pts],
                                                  "Book Value/share")
            if gate["ok"]:
                warns.extend(gate["issues"])
                bvps = pts[-1]["per_share"]
                metrics.append(_metric("Book Value / share", f"${bvps:,.2f}",
                                       "มูลค่าตามบัญชีต่อหุ้น — ฐานประเมินธนาคาร/ประกัน"))
                r = sector_metrics.roe(fin.get("net_income"), fin.get("total_equity"))
                if r is not None:
                    metrics.append(_metric("ROE", pct(r), "ผลตอบแทนต่อส่วนของผู้ถือหุ้น"))
                if price:
                    metrics.append(_metric("P/B ปัจจุบัน", f"{price / bvps:.2f}x", "ราคา ÷ มูลค่าตามบัญชีต่อหุ้น"))
                fair = _fair_from_band(bvps, _multiple_band(bars, pts, bars_per_q), "P/B median", price)
            else:
                warns.append(gate["reason"])

    elif profile_key == "reit":
        pts = _ffo_points(facts)
        if pts:
            gate = sector_profile.validate_anchor([{"value": p["per_share"], "date": p["period"]} for p in pts], "FFO/share")
            if gate["ok"]:
                warns.extend(gate["issues"])
                ffops = pts[-1]["per_share"]
                metrics.append(_metric("FFO / share (ปีล่าสุด)", f"${ffops:,.2f}",
                                       "Funds From Operations ต่อหุ้น — แทน EPS สำหรับ REIT"))
                if price:
                    metrics.append(_metric("P/FFO ปัจจุบัน", f"{price / ffops:.1f}x", "ราคา ÷ FFO ต่อหุ้น"))
                fair = _fair_from_band(ffops, _multiple_band(bars, pts, bars_per_q), "P/FFO median", price)
            else:
                warns.append(gate["reason"])

    elif profile_key == "capital_intensive":
        fcf_ps = [e["fcf_per_share"] for e in fcf_annual if e.get("fcf_per_share")]
        norm = sector_metrics.normalized(fcf_ps, 5)
        if norm is not None:
            metrics.append(_metric("Normalized FCF/share (5 ปี)", f"${norm:,.2f}",
                                   "FCF ต่อหุ้นเฉลี่ย 5 ปี — เกลี่ยรอบ capex ก้อนใหญ่"))
            if pfcf_band:
                fair = _fair_from_band(norm, pfcf_band, "Normalized FCF x P/FCF median", price)
        ebit = sector_metrics.ebitda(fin.get("operating_income"), fin.get("depreciation"))
        if ebit is not None:
            metrics.append(_metric("EBITDA (ปีล่าสุด)", _fmt_money(ebit), "กำไรก่อนดอกเบี้ย ภาษี ค่าเสื่อม"))

    elif profile_key == "cyclical":
        ann_eps = _annual_with_end(facts, _EPS, "USD/shares", prefer_latest_value=True)
        eps_vals = [ann_eps[fy]["val"] for fy in sorted(ann_eps)]
        norm = sector_metrics.normalized(eps_vals, 10)
        if norm is not None and norm > 0:
            metrics.append(_metric("Normalized EPS (~10 ปี)", f"${norm:,.2f}",
                                   "EPS เฉลี่ยหลายปี (Shiller) — กลบวัฏจักร กันตีความ P/E ผิด"))
            if pe_band:
                fair = _fair_from_band(norm, pe_band, "Normalized EPS x P/E median", price)

    elif profile_key == "early_stage":
        cash = _latest_instant(facts, _CASH_CONCEPTS, "USD")
        ocf, capex, rev = fin.get("ocf"), fin.get("capex"), fin.get("revenue")
        fcf = (ocf - capex) if isinstance(ocf, (int, float)) and isinstance(capex, (int, float)) else None
        burn = -fcf if isinstance(fcf, (int, float)) else (-ocf if isinstance(ocf, (int, float)) else None)
        if cash is not None:
            metrics.append(_metric("เงินสด", _fmt_money(cash), "เงินสด & รายการเทียบเท่า"))
        runway = sector_metrics.cash_runway_months(cash, burn)
        if runway is not None:
            metrics.append(_metric("Cash runway", f"{runway:.0f} เดือน",
                                   "เหลือเงินกี่เดือนก่อนต้องระดมทุนใหม่ (ถ้าเผาเงินเท่าเดิม)"))
            if runway < 18:
                warns.append(f"⚠️ Cash runway ~{runway:.0f} เดือน — อาจต้องระดมทุน (dilution) ในไม่ช้า")
        elif isinstance(fcf, (int, float)) and fcf >= 0:
            metrics.append(_metric("Cash runway", "ไม่ต้องกังวล", "กระแสเงินสดเป็นบวกแล้ว"))
        if isinstance(burn, (int, float)) and burn > 0:
            metrics.append(_metric("Burn rate", f"{_fmt_money(burn / 12)}/เดือน", "อัตราเผาเงินสดต่อเดือน"))
        # Rule of 40 = โต% + FCF margin%
        ann_rev = _annual_with_end(facts, _REVENUE_CONCEPTS, "USD")
        fys = sorted(ann_rev)
        growth = None
        if len(fys) >= 2 and ann_rev[fys[-2]]["val"]:
            growth = (ann_rev[fys[-1]]["val"] / ann_rev[fys[-2]]["val"] - 1) * 100
        margin = (fcf / rev * 100) if isinstance(fcf, (int, float)) and rev else None
        r40 = sector_metrics.rule_of_40(growth, margin)
        if r40 is not None:
            metrics.append(_metric("Rule of 40", f"{r40:.0f} ({growth:.0f}% โต {margin:+.0f}% FCF)",
                                   "โต% + FCF margin% ≥ 40 = สมดุลดี"))

    elif profile_key == "general":
        # หุ้นทั่วไป: ประเมินด้วย P/E — ใช้ EPS แบบ TTM (trailing 4 ไตรมาส) ให้สอดคล้องกับ pe_band ที่
        # คำนวณจาก TTM EPS อยู่แล้ว และตรงมาตรฐาน trailing P/E (เท่า Yahoo). ถ้าไตรมาสไม่พอสังเคราะห์
        # TTM ค่อย fallback ไป EPS ปีงบล่าสุด (เดิม) กันเคสข้อมูลรายไตรมาสขาด
        ttm_eps = _ttm_eps_latest(facts)
        ann_eps = _annual_with_end(facts, _EPS, "USD/shares", prefer_latest_value=True)
        eps_vals = [ann_eps[fy]["val"] for fy in sorted(ann_eps)]
        eps_for_pe = ttm_eps if (isinstance(ttm_eps, (int, float)) and ttm_eps > 0) \
            else (eps_vals[-1] if eps_vals else None)
        basis = "P/E median (TTM)" if (isinstance(ttm_eps, (int, float)) and ttm_eps > 0) else "P/E median"
        if eps_for_pe and eps_for_pe > 0 and pe_band:
            fair = _fair_from_band(eps_for_pe, pe_band, basis, price)

    # ด่านสุดท้าย: ตัวคูณ median ที่คำนวณจากช่วงที่ metric "เกือบศูนย์" (เช่น FCF ปีที่เจ๊งพอดี)
    # จะพุ่งเป็นหลักร้อย-พันเท่า แล้วดันราคายุติธรรมหลุดโลก (เคยได้ราคายุติธรรม $4,740 ของหุ้นราคา $92)
    # ตัวเลขแบบนี้ไม่ใช่ "โอกาส" แต่เป็นสัญญาณว่าฐานคำนวณพัง — ปิดการแสดงพร้อมบอกเหตุผล
    if fair and fair.get("current_price"):
        ratio = fair["fair_price"] / fair["current_price"]
        if ratio > 5 or ratio < 0.2:
            warns.append(
                f"ปิดการประเมินราคายุติธรรมของหุ้นตัวนี้ — ผลที่ได้ ({fair['basis']} → ${fair['fair_price']:,.2f} "
                f"เทียบราคาจริง ${fair['current_price']:,.2f}) ห่างกันเกิน 5 เท่า มักเกิดจากปีฐานที่ "
                f"{fair['basis'].split()[0]} เกือบศูนย์/ติดลบ ทำให้ตัวคูณ ({fair['median_multiple']}x) ไม่มีความหมาย"
            )
            fair = None

    return metrics, fair, warns


# ── ชั้น 6: Auto-Narrative (ให้ AI สรุปก่อนแสดง) ──────────────────────────────
_VERDICT_SYSTEM = (
    "คุณคือนักวิเคราะห์การเงินเชิงข้อเท็จจริง วิเคราะห์ตัวเลขที่ให้มาแล้วสรุปเป็นภาษาไทย. "
    "ตอบเป็น JSON object เท่านั้น ห้ามมี markdown/backtick/ข้อความนอก JSON. "
    "schema: {verdict: 'cheap'|'fair'|'expensive'|'cannot_assess', "
    "confidence: 'high'|'medium'|'low', headline: str (หนึ่งประโยค), "
    "reasoning: [str] (2-4 ข้อ อ้างตัวเลขจริง), redFlags: [str] (สิ่งที่ตัวเลขซ่อนไว้), "
    "dataQuality: str, nextChecks: [str] (2-3 ข้อ)}. "
    "กฎ: ถ้าข้อมูลมีปัญหาร้ายแรง (เส้น anchor ถูกปิด/ข้อมูลน้อยเกินไป) ต้องตอบ verdict='cannot_assess'. "
    "ห้ามให้คำแนะนำซื้อ/ขาย — วิเคราะห์ข้อเท็จจริงเท่านั้น."
)


async def _generate_verdict(symbol: str, entity: str | None, profile: dict, profile_key: str,
                            extra_metrics: list[dict], fair_value: dict | None,
                            warnings: list[str]) -> dict | None:
    """เรียก LLM สรุป verdict ตาม sector — ใช้โครงสร้าง llm/ai_analyst ที่มีอยู่ (มี fallback provider).
    คืน None ถ้า AI ปิดอยู่หรือเรียกไม่สำเร็จ (ไม่ทำให้ endpoint หลักพัง)."""
    from app.config import get_settings
    if not get_settings().llm_enabled():
        return None
    from app import ai_analyst

    metrics_txt = "; ".join(f"{m['label']}={m['display']}" for m in extra_metrics) or "—"
    fair_txt = "—"
    if fair_value:
        fair_txt = (f"{fair_value['basis']}: ราคายุติธรรม ${fair_value['fair_price']} "
                    f"(ช่วง ${fair_value['low']}–${fair_value['high']}, ตัวคูณ {fair_value['median_multiple']}x)")
        if "current_price" in fair_value:
            fair_txt += f" · ราคาปัจจุบัน ${fair_value['current_price']} (upside {fair_value.get('upside_pct')}%)"
    user = (
        f"หุ้น: {symbol} ({entity or '-'})\n"
        f"ประเภทธุรกิจ: {profile['label']} (primary metric: {profile.get('primary') or '-'})\n"
        f"Metric ที่ปิดเพราะไม่เหมาะกับกลุ่มนี้: {', '.join(profile.get('disabled', [])) or '—'}\n"
        f"ตัวเลขที่คำนวณได้: {metrics_txt}\n"
        f"ประเมินมูลค่า: {fair_txt}\n"
        f"คำเตือน/ปัญหาข้อมูล: {' | '.join(warnings) or '—'}\n"
        "สรุปตาม schema."
    )
    try:
        v = await ai_analyst._run_llm(_VERDICT_SYSTEM, user)
        return v if isinstance(v, dict) else None
    except Exception:  # noqa: BLE001 — AI ล่ม/quota หมด ไม่กระทบส่วนที่เหลือ
        return None


def _quarterly_with_fp(facts: dict, concepts: list[str], unit: str, prefer_latest_value: bool = False) -> dict[str, dict]:
    """เหมือน financials._quarterly_duration แต่เก็บ fp/fy (ไตรมาสบัญชีจริงที่บริษัทยื่น) ไว้ด้วย
    บริษัทที่ปีงบไม่ตรงปฏิทิน (เช่น Microsoft: Q3 FY = ม.ค.-มี.ค.) ต้องใช้ fp/fy ไม่ใช่เดือนปฏิทิน
    ไม่งั้น label ไตรมาสจะไม่ตรงกับที่บริษัท/นักวิเคราะห์อื่นเรียก.

    prefer_latest_value=True: ใช้ค่า 'ยื่นล่าสุด' แทนค่าที่ยื่นครั้งแรก (fp/fy ยังคงยึดตามครั้งแรกเสมอ
    เพื่อ label ไม่เพี้ยน) — จำเป็นสำหรับ EPS เพราะมาตรฐานบัญชีบังคับให้ 'ปรับปรุงย้อนหลัง' EPS ทุกไตรมาส
    ที่เคยรายงานเมื่อบริษัท split หุ้น (เช่น GOOGL split 20:1 ก.ค. 2022 → EPS ไตรมาสเก่าที่โผล่มาเป็นตัวเลข
    เทียบเคียงในงบปีถัดไปจะถูกหารด้วย 20 ให้แล้ว) ถ้าใช้ค่าที่ยื่นครั้งแรกจะเป็นเลขก่อน split ผิดเพี้ยนไปหลายเท่า
    เมื่อเทียบกับราคาย้อนหลังที่ Yahoo ปรับ split ให้แล้วเสมอ."""
    out: dict[str, dict] = {}
    for e in _entries(facts, concepts, unit):
        if "start" not in e or not str(e.get("form", "")).startswith("10-Q"):
            continue
        d = _days(e["start"], e["end"])
        if d is None or d < 80 or d > 100:
            continue
        if e["end"] not in out:
            out[e["end"]] = {"val": e["val"], "fp": e.get("fp"), "fy": e.get("fy")}
        elif prefer_latest_value:
            out[e["end"]]["val"] = e["val"]
    return out


def _quarterly_from_cumulative(facts: dict, concepts: list[str], unit: str) -> dict[str, dict]:
    """เหมือน _quarterly_with_fp แต่รองรับบริษัทที่ยื่นกระแสเงินสด (OCF/CapEx) แบบ 'สะสมตั้งแต่ต้นปีงบ'
    (YTD) ใน 10-Q ของ Q2/Q3 แทนยอดเฉพาะไตรมาสนั้น (พบมากในทางปฏิบัติ — เช่น Apple ยื่น CapEx ของ Q2
    เป็นยอดสะสม 6 เดือน และ Q3 เป็นยอดสะสม 9 เดือนเสมอ ไม่เคยยื่นยอดเฉพาะไตรมาสแยกเลย) ถ้ากรองด้วย
    duration ~90 วันแบบ _quarterly_with_fp ตรง ๆ จะเหลือแค่ Q1 (ที่ยอดสะสม = ยอดไตรมาสเดียวพอดี)
    ทำให้ไตรมาสอื่นหายไปทั้งหมด — ฟังก์ชันนี้เก็บ Q1/Q2/Q3 ทุกแบบ (ทั้งที่ยื่นเป็นยอดเฉพาะไตรมาส ~90 วัน
    หรือยอดสะสม ~180/~270 วัน) แล้วลบไตรมาสก่อนหน้าที่คำนวณแล้วออกเพื่อได้ยอดเฉพาะไตรมาสเสมอ
    (Q2 เดี่ยว = สะสม 6 เดือน − Q1 เดี่ยว, Q3 เดี่ยว = สะสม 9 เดือน − สะสม 6 เดือน) — คำนวณ Q4 แยกทีหลัง
    ด้วย _synthesize_missing_quarter (งบทั้งปี − ผลรวม Q1-Q3) เหมือนเดิม."""
    by_end: dict[str, dict] = {}
    for e in _entries(facts, concepts, unit):
        if "start" not in e or not str(e.get("form", "")).startswith("10-Q"):
            continue
        fp, fy = e.get("fp"), e.get("fy")
        if fp not in ("Q1", "Q2", "Q3") or fy is None:
            continue
        d = _days(e["start"], e["end"])
        if d is None:
            continue
        if e["end"] not in by_end:
            by_end[e["end"]] = {"val": e["val"], "fp": fp, "fy": fy, "days": d}

    by_fy: dict[int, dict[str, dict]] = {}
    for end, m in by_end.items():
        by_fy.setdefault(m["fy"], {})[m["fp"]] = {**m, "end": end}

    out: dict[str, dict] = {}
    for fy, fps in by_fy.items():
        cum = 0.0
        for fp, n_quarters in (("Q1", 1), ("Q2", 2), ("Q3", 3)):
            m = fps.get(fp)
            if m is None:
                break  # ไตรมาสก่อนหน้าขาด คำนวณ Q ถัดไปต่อไม่ได้ (ไม่รู้ว่าต้องลบเท่าไหร่)
            days = m["days"]
            if 80 <= days <= 100:
                standalone = m["val"]  # ยื่นยอดเฉพาะไตรมาสอยู่แล้ว (ไม่ต้องแปลง)
            elif 80 * n_quarters - 15 <= days <= 100 * n_quarters + 15:
                standalone = m["val"] - cum  # ยอดสะสม YTD — ลบไตรมาสก่อนหน้าที่รู้แล้วออก
            else:
                break  # duration ไม่เข้าเกณฑ์ทั้งสองแบบ ข้ามกันเลขเพี้ยน
            out[m["end"]] = {"val": standalone, "fp": fp, "fy": fy}
            cum += standalone
    return out


def _annual_with_end(facts: dict, concepts: list[str], unit: str, prefer_latest_value: bool = False) -> dict[int, dict]:
    """งบทั้งปี (10-K, fp=FY) พร้อมวันที่ปิดงบ — ใช้สังเคราะห์ไตรมาสที่ไม่ได้ยื่นแยก (มักเป็น Q4).
    prefer_latest_value: เหมือน _quarterly_with_fp — ใช้ EPS ที่ปรับปรุงหลัง split แล้ว."""
    out: dict[int, dict] = {}
    for e in _entries(facts, concepts, unit):
        form = str(e.get("form", ""))
        if "start" not in e or not _is_annual_form(form) or not _annual_fp_ok(form, e.get("fp")):
            continue
        d = _days(e["start"], e["end"])
        if d is None or d < 300 or d > 400:
            continue
        fy = e.get("fy")
        if fy is None:
            continue
        if fy not in out or e["end"] > out[fy]["end"]:
            out[fy] = {"end": e["end"], "val": e["val"]}
        elif prefer_latest_value and e["end"] == out[fy]["end"]:
            out[fy]["val"] = e["val"]
    return out


def _synthesize_missing_quarter(quarterly: dict[str, dict], annual: dict[int, dict]) -> dict[str, dict]:
    """เติมไตรมาสที่ SEC ไม่แยกยื่นเป็น 10-Q (ปกติคือ Q4) = งบทั้งปี (10-K) − ผลรวม 3 ไตรมาสที่มี.
    ทำเฉพาะปีที่ขาดพอดี 1 ไตรมาส (กันเดาผิดตอนข้อมูลไม่ครบ/ไม่ตรงกัน)."""
    by_fy: dict[int, dict[str, float]] = {}
    for m in quarterly.values():
        fp, fy = m.get("fp"), m.get("fy")
        if fp in _ALL_FP and fy is not None:
            by_fy.setdefault(fy, {})[fp] = m["val"]

    out = dict(quarterly)
    for fy, ann in annual.items():
        have = by_fy.get(fy, {})
        missing = _ALL_FP - have.keys()
        if len(missing) == 1 and len(have) == 3 and ann["end"] not in out:
            miss_fp = next(iter(missing))
            out[ann["end"]] = {"val": ann["val"] - sum(have.values()), "fp": miss_fp, "fy": fy}
    return out


def _fp_key(meta: dict, end: str) -> tuple[str, int] | None:
    """คีย์ระบุไตรมาส-ปีบัญชีจริง ('Q3', 2025) ใช้จับคู่ YoY แทนการนับตำแหน่งในลิสต์
    (นับตำแหน่งจะเพี้ยนถ้าไตรมาสไหนขาดหาย — เช่นบริษัทที่มีแค่ 3 ไตรมาส/ปี)."""
    fp, fy = meta.get("fp"), meta.get("fy")
    if fp and fy is not None:
        try:
            return (str(fp), int(fy))
        except (TypeError, ValueError):
            pass
    try:
        y, m, _d = map(int, end.split("-"))
        return (f"Q{(m - 1) // 3 + 1}", y)
    except Exception:  # noqa: BLE001
        return None


def _yoy_lookup(meta: dict[str, dict]) -> dict[tuple[str, int], float]:
    """สร้างตาราง (fp, fy) -> ค่า สำหรับจับคู่ YoY ของเมตริกใดก็ได้ (รายได้/กำไร)."""
    lookup: dict[tuple[str, int], float] = {}
    for d, m in meta.items():
        k = _fp_key(m, d)
        if k:
            lookup[k] = m["val"]
    return lookup


def _quarter_label(end: str, meta: dict | None) -> str:
    """ใช้ fp/fy จาก SEC ('Q3', 2025) -> \"Q3'25\" ถ้ามี ไม่งั้น fallback เป็นไตรมาสปฏิทิน."""
    fp, fy = (meta or {}).get("fp"), (meta or {}).get("fy")
    if fp and fy is not None:
        try:
            return f"{fp}'{int(fy) % 100:02d}"
        except (TypeError, ValueError):
            pass
    try:
        y, m, _d = map(int, end.split("-"))
        q = (m - 1) // 3 + 1
        return f"Q{q}'{y % 100:02d}"
    except Exception:  # noqa: BLE001
        return end


def _group_candles(candles: list, key_fn) -> list[dict]:
    groups: dict = {}
    for c in candles:
        groups.setdefault(key_fn(c), []).append(c)
    out = []
    for key in sorted(groups.keys()):
        grp = groups[key]
        out.append({
            "time": grp[0].time,
            "open": grp[0].open,
            "high": max(x.high for x in grp),
            "low": min(x.low for x in grp),
            "close": grp[-1].close,
        })
    return out


def _fetch_yahoo_quarterly_sync(symbol: str) -> dict[str, dict]:
    """ดึงงบไตรมาสล่าสุดจาก Yahoo (yfinance) แบบ sync — เร็วกว่า SEC มาก เพราะ Yahoo มักอัปเดตราย
    ไตรมาสภายใน 1-2 วันหลังบริษัทประกาศผล (ต่างจาก SEC ที่ต้องรอยื่นเอกสาร 10-Q ทางการ ~1-3 สัปดาห์)
    แต่เป็นตัวเลข 'เบื้องต้น' ที่บริษัทประกาศเอง ยังไม่ผ่านการยื่นอย่างเป็นทางการ — ใช้เสริมเฉพาะไตรมาส
    ล่าสุดที่ SEC ยังไม่มี ไม่ใช้แทนข้อมูล SEC ที่เหลือทั้งหมด. ไม่ cache — ต้องเป็นข้อมูลสดที่สุดเสมอ."""
    import yfinance as yf
    t = yf.Ticker(symbol)
    df = t.quarterly_income_stmt
    if df is None or df.empty:
        df = t.quarterly_financials
    if df is None or df.empty:
        return {}

    def _row(names):
        for n in names:
            if n in df.index:
                return df.loc[n]
        return None

    rev_row = _row(["Total Revenue", "TotalRevenue", "Operating Revenue"])
    ni_row = _row(["Net Income", "Net Income Common Stockholders", "NetIncome"])
    if rev_row is None:
        return {}

    out: dict[str, dict] = {}
    for col in df.columns:
        try:
            end = col.date().isoformat() if hasattr(col, "date") else str(col)[:10]
        except Exception:  # noqa: BLE001
            continue
        rev_val = rev_row.get(col)
        if rev_val is None or rev_val != rev_val:  # NaN
            continue
        ni_val = ni_row.get(col) if ni_row is not None else None
        out[end] = {
            "revenue": float(rev_val),
            "net_income": float(ni_val) if ni_val is not None and ni_val == ni_val else None,
        }
    return out


async def _preliminary_quarter(symbol: str, quarters_full: list[dict]) -> dict | None:
    """หาไตรมาสล่าสุดจาก Yahoo ที่ 'ใหม่กว่า' ไตรมาสล่าสุดที่มีใน SEC แล้ว — ถ้ามี คืนเป็นไตรมาส
    'เบื้องต้น' (preliminary) เพิ่มเข้ากราฟ ต้องคำนวณ YoY ได้ด้วย (เทียบกับไตรมาสเดียวกันปีก่อนจาก SEC
    ก่อน แม่นกว่า ถ้าไม่มีค่อย fallback ไปหาในข้อมูล Yahoo เอง) ไม่งั้นโชว์ไปก็เทียบไม่ได้ ไม่มีประโยชน์."""
    try:
        yahoo_q = await asyncio.to_thread(_fetch_yahoo_quarterly_sync, symbol)
    except Exception:  # noqa: BLE001 — yfinance ล่ม/rate-limit ก็ไม่ทำให้ endpoint หลักพังไปด้วย
        return None
    if not yahoo_q:
        return None

    latest_sec = quarters_full[-1]["period"] if quarters_full else ""
    newest_end = max(yahoo_q.keys())
    if newest_end <= latest_sec:
        return None  # SEC มีข้อมูลทันแล้ว ไม่ต้องเสริม

    y = yahoo_q[newest_end]
    target_dt = datetime.strptime(newest_end, "%Y-%m-%d")
    try:
        prior_year_target = target_dt.replace(year=target_dt.year - 1)
    except ValueError:  # 29 ก.พ.
        prior_year_target = target_dt.replace(year=target_dt.year - 1, day=28)

    prev_rev = prev_ni = None
    best_diff = None
    for qf in quarters_full:
        try:
            qd = datetime.strptime(qf["period"], "%Y-%m-%d")
        except ValueError:
            continue
        diff = abs((qd - prior_year_target).days)
        if diff <= 20 and (best_diff is None or diff < best_diff):
            best_diff, prev_rev, prev_ni = diff, qf["revenue"], qf.get("net_income")
    if prev_rev is None:
        for end, vals in yahoo_q.items():
            if end == newest_end:
                continue
            try:
                ed = datetime.strptime(end, "%Y-%m-%d")
            except ValueError:
                continue
            if abs((ed - prior_year_target).days) <= 20:
                prev_rev, prev_ni = vals["revenue"], vals.get("net_income")
                break
    if not prev_rev:
        return None

    yoy = (y["revenue"] - prev_rev) / abs(prev_rev)
    profit_yoy = None
    if y.get("net_income") is not None and prev_ni:
        profit_yoy = (y["net_income"] - prev_ni) / abs(prev_ni)

    return {
        "period": newest_end,
        "label": _quarter_label(newest_end, None) + "*",
        "revenue": y["revenue"], "yoy_pct": yoy,
        "net_income": y.get("net_income"), "profit_yoy_pct": profit_yoy,
        "preliminary": True,
    }


def _resample(candles: list, granularity: str) -> list[dict]:
    """รวมแท่งรายวันเป็นความละเอียดที่ต้องการ: daily (แยกแท่งเดิม) / weekly (สัปดาห์ปฏิทิน ISO) / monthly (เดือนปฏิทิน)."""
    if granularity == "daily":
        return [{"time": c.time, "open": c.open, "high": c.high, "low": c.low, "close": c.close} for c in candles]
    if granularity == "monthly":
        return _group_candles(candles, lambda c: (datetime.fromtimestamp(c.time, tz=timezone.utc).year,
                                                    datetime.fromtimestamp(c.time, tz=timezone.utc).month))
    return _group_candles(candles, lambda c: datetime.fromtimestamp(c.time, tz=timezone.utc).isocalendar()[:2])


def _cagr(series: list[tuple[str, float]], years: int | None) -> float | None:
    """อัตราเติบโตทบต้น (%/ปี) ของ TTM series ย้อนหลัง `years` ปี (None = ตลอดอายุข้อมูล).

    series: list ของ (date_iso, value) เรียงเก่า→ใหม่. คืน None (ไม่ระเบิด) เมื่อ:
      • ข้อมูลน้อยกว่า 2 จุด
      • ฐาน/ปลายทาง ≤ 0 (คิด CAGR จากฐานติดลบไม่มีความหมาย)
      • ข้อมูลย้อนหลังไม่ถึงหน้าต่างที่ขอ (เช่นขอ 10 ปีแต่มีแค่ 6 ปี)
    """
    pts = [(d, v) for d, v in series if isinstance(v, (int, float))]
    if len(pts) < 2:
        return None
    latest_d, latest_v = pts[-1]
    if latest_v <= 0:
        return None
    ld = datetime.strptime(latest_d[:10], "%Y-%m-%d")
    if years is None:
        base_d, base_v = pts[0]
    else:
        try:
            target = ld.replace(year=ld.year - years)
        except ValueError:  # 29 ก.พ. ในปีที่ไม่ใช่อธิกสุรทิน
            target = ld.fromordinal(ld.toordinal() - int(years * 365.25))
        earlier = [(d, v) for d, v in pts if datetime.strptime(d[:10], "%Y-%m-%d") <= target]
        if not earlier:               # ข้อมูลย้อนหลังไม่ถึงหน้าต่างนี้
            return None
        base_d, base_v = earlier[-1]
    if base_v <= 0:
        return None
    yrs = (ld - datetime.strptime(base_d[:10], "%Y-%m-%d")).days / 365.25
    if yrs < 0.75:                    # ช่วงสั้นเกินไป CAGR ไม่มีความหมาย
        return None
    return round(((latest_v / base_v) ** (1.0 / yrs) - 1.0) * 100, 1)


def _growth_stats(ttm_revenue: list[tuple[str, float]], ttm_eps: list[tuple[str, float]],
                  current_price: float | None) -> dict:
    """สถิติการเติบโต (CAGR รายได้/EPS 3/5/10 ปี/ตลอดอายุ) + ค่าปัจจุบัน — ป้อนแผงคาดการณ์ (ข้อ 4)."""
    cur_eps = ttm_eps[-1][1] if ttm_eps else None
    cur_rev = ttm_revenue[-1][1] if ttm_revenue else None
    cur_pe = (round(current_price / cur_eps, 1)
              if isinstance(current_price, (int, float)) and isinstance(cur_eps, (int, float)) and cur_eps > 0
              else None)
    windows = {"y3": 3, "y5": 5, "y10": 10, "life": None}
    return {
        "current_eps": round(cur_eps, 2) if isinstance(cur_eps, (int, float)) else None,
        "current_revenue": cur_rev,
        "current_price": round(current_price, 2) if isinstance(current_price, (int, float)) else None,
        "current_pe": cur_pe,
        "revenue_cagr": {k: _cagr(ttm_revenue, w) for k, w in windows.items()},
        "eps_cagr": {k: _cagr(ttm_eps, w) for k, w in windows.items()},
    }


async def _price_bars(symbol: str, granularity: str) -> list[dict]:
    """ราคาย้อนหลัง (USD) จาก provider ปัจจุบัน แล้ว resample ตามความละเอียด — ดึงไม่ได้ก็คืน []
    (ยังโชว์กล่องการเติบโตได้แม้ไม่มีกราฟราคา/P/E) เหมือนที่ get_revenue_model ทำ."""
    from app.main import provider  # lazy import กันวน circular import (ตามแบบ trend_radar.py)
    get_history = getattr(provider, "get_history", None)
    try:
        candles = await get_history(symbol, "1d", max_bars=8000) if get_history \
            else await provider.get_candles(symbol, "1d", 1000)
    except Exception:  # noqa: BLE001
        candles = []
    return _resample(candles, granularity) if candles else []


def _annual_rows_from_sec(facts: dict, money_unit: str, eps_unit: str) -> list[dict]:
    """แถวงบ 'รายปี' ต่อ 1 ปีงบจาก 20-F/10-K (รายได้/กำไร/OCF/CapEx/EPS) — สกุลเงินตามที่บริษัทยื่น."""
    ann_rev = _annual_with_end(facts, _REVENUE_CONCEPTS, money_unit)
    ann_ni = _annual_with_end(facts, _NET_INCOME_CONCEPTS, money_unit)
    ann_ocf = _annual_with_end(facts, _OCF_CONCEPTS, money_unit)
    ann_capex = _annual_with_end(facts, _CAPEX_CONCEPTS, money_unit)
    ann_eps = _annual_with_end(facts, _EPS, eps_unit, prefer_latest_value=True)
    rows = []
    for fy in sorted(ann_rev):
        ocf = ann_ocf.get(fy, {}).get("val")
        capex = ann_capex.get(fy, {}).get("val")
        rows.append({
            "fy": fy, "end": ann_rev[fy]["end"], "revenue": ann_rev[fy]["val"],
            "net_income": ann_ni.get(fy, {}).get("val"), "ocf": ocf, "capex": capex,
            "fcf": (ocf - capex) if ocf is not None and capex is not None else None,
            "eps": ann_eps.get(fy, {}).get("val"),
        })
    return rows


async def _fetch_yahoo_annual(symbol: str) -> list[dict]:
    """งบรายปีจาก Yahoo fundamentals-timeseries (~4 ปีล่าสุด, สกุลเงินที่บริษัทยื่น) — ใช้เป็น fallback
    เมื่อ SEC ไม่มีงบของ ADR ตัวนั้นเลย. คืน rows เรียงตามปีเหมือน _annual_rows_from_sec."""
    import time as _t

    import httpx
    from app.fundamentals import _YA_UA, _yahoo_crumb
    types = "annualTotalRevenue,annualNetIncome,annualDilutedEPS,annualBasicEPS,annualOperatingCashFlow"
    cookie, crumb = await _yahoo_crumb()
    url = (f"https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{symbol}"
           f"?symbol={symbol}&type={types}&period1=0&period2={int(_t.time())}&crumb={crumb}")
    async with httpx.AsyncClient(headers=_YA_UA, timeout=20, follow_redirects=True) as c:
        r = await c.get(url, headers={"Cookie": cookie})
        r.raise_for_status()
        result = r.json().get("timeseries", {}).get("result", []) or []
    field = {"annualTotalRevenue": "revenue", "annualNetIncome": "net_income",
             "annualDilutedEPS": "eps", "annualBasicEPS": "eps_basic",
             "annualOperatingCashFlow": "ocf"}
    by_end: dict[str, dict] = {}
    for item in result:
        typ = (item.get("meta", {}).get("type") or [None])[0]
        key = field.get(typ)
        if not key:
            continue
        for v in item.get(typ) or []:
            if not v:
                continue
            end = v.get("asOfDate")
            raw = (v.get("reportedValue") or {}).get("raw")
            if end and raw is not None:
                by_end.setdefault(end, {})[key] = raw
    rows = []
    for end in sorted(by_end):
        m = by_end[end]
        rev = m.get("revenue")
        if rev is None:
            continue
        rows.append({
            "fy": int(end[:4]), "end": end, "revenue": rev,
            "net_income": m.get("net_income"), "ocf": m.get("ocf"), "capex": None,
            "fcf": None, "eps": m.get("eps") if m.get("eps") is not None else m.get("eps_basic"),
        })
    return rows


async def _build_annual_model(symbol: str, granularity: str, include_ai: bool, *,
                              facts: dict | None, rows: list[dict], money_unit: str,
                              source: str) -> dict:
    """สร้างโมเดล 'รายปี' สำหรับหุ้น ADR/foreign issuer ที่ไม่มีงบรายไตรมาสใน SEC — โครงสร้าง JSON
    เข้ากันได้กับ get_revenue_model (frontend ใช้ต่อได้ทันที) แต่ quarters เป็นราย 'ปีงบ' และ P/E
    แปลงเป็นระดับ ADR (USD) โดยประมาณด้วยการ calibrate EPS ท้องถิ่นกับ EPS ที่ Yahoo รายงาน."""
    by_fy = {r["fy"]: r for r in rows}

    def yoy(cur, prev):
        return (cur - prev) / abs(prev) if (cur is not None and prev) else None

    quarters_full = []
    for r in sorted(rows, key=lambda x: x["fy"]):
        p = by_fy.get(r["fy"] - 1) or {}
        quarters_full.append({
            "period": r["end"], "label": f"FY{r['fy'] % 100:02d}", "fy": r["fy"],
            "revenue": r["revenue"], "yoy_pct": yoy(r["revenue"], p.get("revenue")),
            "net_income": r["net_income"], "profit_yoy_pct": yoy(r["net_income"], p.get("net_income")),
            "ocf": r["ocf"], "ocf_yoy_pct": yoy(r["ocf"], p.get("ocf")), "fcf": r["fcf"],
        })
    quarters = [q for q in quarters_full if q["yoy_pct"] is not None]
    if len(quarters) < 2:
        raise ValueError("ข้อมูลงบรายปีไม่พอคำนวณการเติบโต (ต้องมีอย่างน้อย 2 ปีติดกัน)")

    bars = await _price_bars(symbol, granularity)
    price = bars[-1]["close"] if bars else None
    bars_per_q = _BARS_PER_QUARTER[granularity]
    is_adr = money_unit != "USD"

    # ── P/E ระดับ ADR (USD) โดยประมาณ: calibrate EPS ท้องถิ่นล่าสุดกับ EPS (USD) ที่ Yahoo รายงาน ──
    eps_rows = [r for r in rows if isinstance(r.get("eps"), (int, float))]
    yahoo_eps_usd = None
    try:
        from app import fundamentals
        snap = await fundamentals.fetch_yahoo_fundamentals(symbol)
        yahoo_eps_usd = snap.get("eps")
    except Exception:  # noqa: BLE001 — Yahoo ล่ม/บล็อก ก็ยังโชว์กราฟการเติบโตได้ (ไม่มี P/E)
        yahoo_eps_usd = None

    pe_series: list[dict] = []
    pe_reference = None
    pe_band = None
    eps_usd_series: list[tuple[str, float]] = []
    eps_approx = False
    # ตัวคูณแปลง EPS (สกุลท้องถิ่น/ต่อหุ้นสามัญ) → EPS ระดับ ADR สกุล USD:
    #   k = EPS(USD ที่ Yahoo รายงานระดับ ADR) ÷ EPS(ท้องถิ่น ปีล่าสุด)  — รวม FX + อัตราส่วน ADR ในตัวเดียว
    # ถ้าหุ้นรายงาน USD อยู่แล้ว k≈1 (ไม่กระทบ). ถ้าเป็น ADR แต่ดึง Yahoo ไม่ได้ → งด P/E ไปเลย เพราะ
    # เอาราคา USD ไปหารด้วย EPS สกุลท้องถิ่นจะได้ค่า P/E มั่ว (เช่น TWD) — ยอมไม่มี P/E ดีกว่าโชว์ผิด
    latest_local_eps = eps_rows[-1]["eps"] if eps_rows else None
    k = None
    if eps_rows and price and latest_local_eps and latest_local_eps > 0:
        if yahoo_eps_usd and yahoo_eps_usd > 0:
            k = yahoo_eps_usd / latest_local_eps
            eps_approx = abs(k - 1.0) > 1e-6      # ต่างจาก 1 = มีการแปลง FX/ADR → ติดป้าย 'ประมาณการ'
        elif not is_adr:
            k = 1.0                               # รายงาน USD อยู่แล้ว และไม่มี Yahoo ให้เทียบ
    if k is not None:
        eps_usd_series = [(r["end"], r["eps"] * k) for r in eps_rows
                          if isinstance(r.get("eps"), (int, float))]
        for w in bars:
            wd = datetime.fromtimestamp(w["time"], tz=timezone.utc).date().isoformat()
            eps_usd = next((v for d, v in reversed(eps_usd_series) if d <= wd and v > 0), None)
            if eps_usd:
                pe_series.append({"time": w["time"], "pe": round(w["close"] / eps_usd, 2)})
        recent_pe = [p["pe"] for p in pe_series[-(bars_per_q * 8):]]
        pe_band = sector_profile.median_band(recent_pe) if recent_pe else None
        if pe_band:
            pe_reference = {"median": round(pe_band["median"], 1),
                            "low": round(pe_band["p25"], 1), "high": round(pe_band["p75"], 1),
                            "label": f"{round(pe_band['median'])}x P/E (median, ~ช่วงหลัง"
                                     + (" · ADR USD ประมาณการ)" if eps_approx else ")")}

    # ── กล่องประเมินมูลค่าตาม sector (ใช้ SIC — เชื่อถือได้แม้งบเป็นสกุลต่างชาติ) ──
    sic = None
    if facts is not None:
        try:
            sic = (await edgar.get_submissions(symbol)).get("sic") or None
        except Exception:  # noqa: BLE001
            sic = None
    fin_snapshot = _financials_snapshot(facts, money_unit) if facts is not None else {}
    sector_info = sector_profile.classify_sector(sic, fin_snapshot)
    profile_key = sector_info["profile"]
    profile = sector_profile.get_profile(profile_key)
    warnings = list(profile.get("warnings", []))

    # ราคายุติธรรม = EPS(USD ระดับ ADR) ปีล่าสุด × ตัวคูณ P/E median — ตรงไปตรงมา เหมาะกับ ADR ที่ไม่มี
    # เมตริกเฉพาะกลุ่มครบ (ธนาคาร/REIT ต่างชาติจะได้ P/E เป็นแนวทางกว้าง ๆ ติดป้ายว่าโดยประมาณ)
    fair_value = None
    if pe_band and eps_usd_series and price:
        fair_value = _fair_from_band(eps_usd_series[-1][1], pe_band,
                                     "P/E median (รายปี" + (" · ADR USD" if eps_approx else "") + ")", price)

    ann_rev_series = [(r["end"], r["revenue"]) for r in rows if r.get("revenue") is not None]
    growth_stats = _growth_stats(ann_rev_series, eps_usd_series, price)

    cur = money_unit if money_unit != "USD" else "USD"
    adr_note = (f"หุ้นต่างชาติ (ADR/foreign issuer) ยื่นงบเป็นราย 'ปี' (แบบ 20-F) ในสกุล {cur} — "
                f"จึงแสดงการเติบโตรายปีแทนรายไตรมาส (บริษัทกลุ่มนี้ไม่ยื่นงบรายไตรมาสต่อ SEC). "
                f"อัตราเติบโต YoY เป็นสัดส่วนจึงแม่นแม้ตัวเลขเป็นสกุล {cur}"
                + ("; P/E เป็นระดับ ADR (USD) โดยประมาณ (สมมติ FX/อัตราส่วน ADR คงที่)" if eps_approx else "")) \
        if is_adr else \
        "หุ้นนี้มีงบรายปีแต่ไม่พอสร้างมุมมองรายไตรมาส — แสดงการเติบโตรายปีแทน"

    verdict = await _generate_verdict(symbol, (facts or {}).get("entityName"), profile, profile_key,
                                      [], fair_value, warnings) if include_ai else None

    window = max(len(quarters) * bars_per_q, 60)
    return {
        "symbol": symbol,
        "entity_name": (facts or {}).get("entityName"),
        "granularity": granularity,
        "period_type": "annual",
        "is_adr": is_adr,
        "reporting_currency": money_unit,
        "adr_note": adr_note,
        "data_source": source,
        "quarters": quarters,
        "price_candles": bars[-window:],
        "pe_series": pe_series[-window:],
        "pe_reference": pe_reference,
        "fcf_annual": [],
        "fcf_quarterly": [],
        "pfcf_series": [],
        "pfcf_reference": None,
        "sic": sic,
        "sector": sector_info["sector"],
        "sector_label": profile["label"],
        "sector_source": sector_info["source"],
        "profile": {
            "key": profile_key,
            "label": profile["label"],
            "primary": profile.get("primary"),
            "disabled": profile.get("disabled", []),
            "extra_metrics": profile.get("extra_metrics", []),
        },
        "warnings": warnings,
        "extra_metrics": [],
        "fair_value": fair_value,
        "growth_stats": growth_stats,
        "verdict": verdict,
        "has_preliminary": False,
        "disclaimer": adr_note + " — เพื่อการศึกษาเท่านั้น ไม่ใช่คำแนะนำการลงทุน",
    }


async def get_revenue_model(symbol: str, refresh: bool = False, max_quarters: int = 40,
                            granularity: str = "weekly", include_ai: bool = False) -> dict:
    symbol = (symbol or "").strip().upper()
    if not symbol:
        raise ValueError("กรุณาระบุสัญลักษณ์หุ้น")
    if not is_equity_symbol(symbol):
        raise ValueError("ใช้ได้กับหุ้นรายตัวเท่านั้น (ไม่รองรับคริปโต/forex/ดัชนี)")
    if granularity not in _BARS_PER_QUARTER:
        raise ValueError("granularity ต้องเป็น daily, weekly หรือ monthly")

    try:
        facts = await edgar.get_company_facts(symbol, force_refresh=refresh)
    except ValueError:
        # ไม่มีงบใน SEC เลย (ADR บางตัว/หุ้นต่างชาติที่ไม่ยื่น SEC) — ลองสร้างโมเดลรายปีจาก Yahoo ก่อนยอมแพ้
        try:
            rows = await _fetch_yahoo_annual(symbol)
        except Exception:  # noqa: BLE001
            rows = []
        if len(rows) >= 3:
            money_unit = "USD"  # Yahoo timeseries ไม่บอกสกุลตรง ๆ — ถือว่าเป็นสกุลที่บริษัทยื่น (ป้ายเป็นกลาง)
            return await _build_annual_model(symbol, granularity, include_ai,
                                             facts=None, rows=rows, money_unit=money_unit, source="yahoo")
        raise ValueError("โมเดลนี้ใช้งบจาก SEC EDGAR — ไม่พบงบของหุ้นตัวนี้ทั้งใน SEC และ Yahoo "
                          "(อาจไม่ใช่หุ้นจดทะเบียน/ADR ที่มีข้อมูลสาธารณะ)")

    raw_revenue_meta = _synthesize_missing_quarter(
        _quarterly_with_fp(facts, _REVENUE_CONCEPTS, "USD"),
        _annual_with_end(facts, _REVENUE_CONCEPTS, "USD"),
    )
    raw_eps_meta = _synthesize_missing_quarter(
        _quarterly_with_fp(facts, _EPS, "USD/shares", prefer_latest_value=True),
        _annual_with_end(facts, _EPS, "USD/shares", prefer_latest_value=True),
    )
    raw_eps = {d: m["val"] for d, m in raw_eps_meta.items()}
    raw_ni_meta = _synthesize_missing_quarter(
        _quarterly_with_fp(facts, _NET_INCOME_CONCEPTS, "USD"),
        _annual_with_end(facts, _NET_INCOME_CONCEPTS, "USD"),
    )
    raw_ocf_meta = _synthesize_missing_quarter(
        _quarterly_from_cumulative(facts, _OCF_CONCEPTS, "USD"),
        _annual_with_end(facts, _OCF_CONCEPTS, "USD"),
    )

    # กระแสเงินสดอิสระรายปี (10-K) — เก็บไว้เป็น fallback เผื่อบริษัทไหนข้อมูลรายไตรมาสไม่พอสังเคราะห์ TTM ได้
    ann_shares = _annual_shares(facts)
    fcf_annual = _fcf_annual_points(facts)

    # CapEx รายไตรมาส — ใช้ _quarterly_from_cumulative (แปลงยอดสะสม YTD กลับเป็นยอดเฉพาะไตรมาสให้ ไม่ทิ้ง
    # ทิ้งเหมือนเดิม) แล้วสังเคราะห์ไตรมาสที่ขาด/มักเป็น Q4 จากงบทั้งปี เพื่อคำนวณ FCF ต่อไตรมาสแล้วรวมเป็น
    # TTM ด้านล่าง — ให้เส้น "ราคาตาม FCF" อัปเดตทุกไตรมาสแทนที่จะเป็นขั้นบันไดรายปีเหมือนก่อนแก้ไข
    raw_capex_meta = _synthesize_missing_quarter(
        _quarterly_from_cumulative(facts, _CAPEX_CONCEPTS, "USD"),
        _annual_with_end(facts, _CAPEX_CONCEPTS, "USD"),
    )
    capex_by_key = _yoy_lookup(raw_capex_meta)

    dates = sorted(raw_revenue_meta.keys())
    if len(dates) < 5:
        # หุ้น ADR/foreign issuer (เช่น TSM, ASML, Toyota) ยื่น 20-F รายปีในสกุลท้องถิ่น (ifrs-full/us-gaap)
        # ไม่ยื่นงบรายไตรมาสต่อ SEC — ตรวจหน่วยสกุลเงินจริงแล้วสร้าง "โมเดลรายปี" แทนการ error
        money_unit = pick_reporting_unit(facts, _REVENUE_CONCEPTS)
        eps_unit = pick_reporting_unit(facts, _EPS, per_share=True)
        rows = _annual_rows_from_sec(facts, money_unit, eps_unit)
        if len([r for r in rows if r.get("revenue") is not None]) >= 3:
            return await _build_annual_model(symbol, granularity, include_ai,
                                             facts=facts, rows=rows, money_unit=money_unit, source="sec")
        raise ValueError("ข้อมูลรายได้จาก SEC ไม่พอสำหรับสร้างโมเดล (ต้องมีรายไตรมาส ≥5 หรือรายปี ≥3)")

    # จับคู่ YoY ด้วยไตรมาสบัญชีจริง (fp, fy-1) ไม่ใช่นับถอยหลัง 4 ตำแหน่งในลิสต์
    rev_by_key = _yoy_lookup(raw_revenue_meta)
    ni_by_key = _yoy_lookup(raw_ni_meta)
    ocf_by_key = _yoy_lookup(raw_ocf_meta)

    quarters_full = []
    for d in dates:
        meta = raw_revenue_meta[d]
        rev = meta["val"]
        k = _fp_key(meta, d)
        yoy = None
        profit_yoy = None
        net_income = None
        ocf = None
        ocf_yoy = None
        capex = None
        fcf = None
        fy = k[1] if k else None
        if k:
            prev = rev_by_key.get((k[0], k[1] - 1))
            if prev:
                yoy = (rev - prev) / abs(prev)
            net_income = ni_by_key.get(k)
            ni_prev = ni_by_key.get((k[0], k[1] - 1))
            if net_income is not None and ni_prev:
                profit_yoy = (net_income - ni_prev) / abs(ni_prev)
            ocf = ocf_by_key.get(k)
            ocf_prev = ocf_by_key.get((k[0], k[1] - 1))
            if ocf is not None and ocf_prev:
                ocf_yoy = (ocf - ocf_prev) / abs(ocf_prev)
            capex = capex_by_key.get(k)
            if ocf is not None and capex is not None:
                fcf = ocf - capex
        quarters_full.append({"period": d, "label": _quarter_label(d, meta), "revenue": rev, "yoy_pct": yoy,
                              "net_income": net_income, "profit_yoy_pct": profit_yoy,
                              "ocf": ocf, "ocf_yoy_pct": ocf_yoy, "fcf": fcf, "fy": fy})

    quarters = [q for q in quarters_full if q["yoy_pct"] is not None][-max_quarters:]
    if not quarters:
        raise ValueError("ยังไม่มีไตรมาสที่คำนวณ YoY ได้ครบ (ต้องมีงบย้อนหลังอย่างน้อย 5 ไตรมาส)")

    # เสริมไตรมาสล่าสุดจาก Yahoo ถ้า SEC ยังไม่มี (บริษัทประกาศผลแล้วแต่ยังไม่ยื่น 10-Q ทางการ) —
    # ดึงสดทุกครั้ง ไม่ cache เพื่อให้ทันข่าวที่สุด ไม่พลาดโอกาสระหว่างรอ SEC
    prelim = await _preliminary_quarter(symbol, quarters_full)
    if prelim:
        quarters = (quarters + [prelim])[-max_quarters:]

    # TTM EPS (ผลรวม EPS 4 ไตรมาสล่าสุด ตามลำดับเวลาจริง — สังเคราะห์ไตรมาสขาดแล้วจึงครบ 4/ปี)
    eps_dates = sorted(raw_eps.keys())
    ttm_eps_by_date = [
        (eps_dates[i], sum(raw_eps[d] for d in eps_dates[i - 3:i + 1]))
        for i in range(3, len(eps_dates))
    ]

    # TTM FCF ต่อไตรมาส (ผลรวม FCF 4 ไตรมาสติดกันล่าสุด ตามลำดับเวลาจริงใน quarters_full ซึ่งเรียงจากเก่า
    # ไปใหม่แล้ว) — เกลี่ยความผันผวนตามฤดูกาล/CapEx ก้อนใหญ่รายไตรมาสออกเหมือน TTM revenue ด้านบน
    # ใช้แทนขั้นบันไดรายปีเดิม (fcf_annual) ให้เส้น "ราคาตาม FCF" ขยับทุกไตรมาสแทนที่จะรอ 10-K ทั้งปี
    # ไตรมาสไหนคำนวณ FCF ไม่ได้ (ไม่มี CapEx แบบ ~90 วันแยกยื่น — บางบริษัทยื่นแบบสะสม YTD เท่านั้น)
    # จะข้ามหน้าต่าง TTM นั้นไปเลย กันเลขเพี้ยนจากข้อมูลไม่ครบ
    fcf_quarterly = []
    for i in range(3, len(quarters_full)):
        window = quarters_full[i - 3:i + 1]
        if all(q["fcf"] is not None for q in window):
            fy = quarters_full[i]["fy"]
            shares_val = (ann_shares.get(fy, {}).get("val") if fy is not None else None) \
                or (ann_shares.get(fy - 1, {}).get("val") if fy is not None else None)
            ttm_fcf = sum(q["fcf"] for q in window)
            fcf_quarterly.append({
                "period": quarters_full[i]["period"], "fy": fy, "fcf": ttm_fcf,
                "fcf_per_share": (ttm_fcf / shares_val) if shares_val else None,
            })

    from app.main import provider  # lazy import กันวน circular import (ตามแบบ trend_radar.py)
    get_history = getattr(provider, "get_history", None)
    try:
        candles = await get_history(symbol, "1d", max_bars=8000) if get_history \
            else await provider.get_candles(symbol, "1d", 1000)
    except Exception:  # noqa: BLE001 — ราคาย้อนหลังดึงไม่ได้ก็ยังโชว์กล่อง YoY ได้ (ไม่มีกราฟราคา/P/E)
        candles = []
    bars = _resample(candles, granularity) if candles else []

    pe_series = []
    for w in bars:
        w_date = datetime.fromtimestamp(w["time"], tz=timezone.utc).date().isoformat()
        ttm = next((val for qd, val in reversed(ttm_eps_by_date) if qd <= w_date), None)
        if ttm and ttm > 0:
            pe_series.append({"time": w["time"], "pe": round(w["close"] / ttm, 2)})

    # อ้างอิงจากช่วง ~2 ปีล่าสุดเท่านั้น (ไม่ใช่ทั้ง 6 ปี) — EPS ย้อนหลังของหุ้นที่เคย split นาน ๆ มาแล้ว
    # (เช่น GOOGL 2022, NVDA 2021/2024) อาจไม่ถูกปรับปรุงย้อนหลังครบทุกไตรมาสตามข้อมูลที่ SEC ให้มา
    # ช่วงใกล้ปัจจุบันเชื่อถือได้กว่ามาก จึงใช้คำนวณเส้นอ้างอิงแทนค่ามัธยฐานทั้งช่วง
    bars_per_q = _BARS_PER_QUARTER[granularity]
    pe_reference = None
    recent_pe = pe_series[-(bars_per_q * 8):]  # ~2 ปีล่าสุด
    if recent_pe:
        pe_sorted = sorted(p["pe"] for p in recent_pe)
        median = pe_sorted[len(pe_sorted) // 2]
        pe_reference = {"median": round(median, 1), "label": f"{round(median)}x P/E (median, ~2 ปีล่าสุด)"}

    # P/FCF (ราคา ÷ TTM Free Cash Flow ต่อหุ้น) — ใช้ fcf_quarterly (TTM รายไตรมาส) ถ้าข้อมูลพอ
    # อัปเดตทุกไตรมาสแทนที่จะรอ 10-K ทั้งปี ถ้าไม่พอ (บริษัทยื่น CapEx แบบสะสม YTD เท่านั้น) fallback
    # กลับไปใช้ fcf_annual (ขั้นบันไดรายปี) แทน
    fcf_series_for_pfcf = fcf_quarterly if len(fcf_quarterly) >= 3 else fcf_annual
    pfcf_series = []
    for w in bars:
        w_date = datetime.fromtimestamp(w["time"], tz=timezone.utc).date().isoformat()
        entry = next((e for e in reversed(fcf_series_for_pfcf) if e["period"] <= w_date and e["fcf_per_share"]), None)
        if entry and entry["fcf_per_share"] > 0:
            pfcf_series.append({"time": w["time"], "pfcf": round(w["close"] / entry["fcf_per_share"], 2)})

    pfcf_reference = None
    recent_pfcf = pfcf_series[-(bars_per_q * 8):]  # ~2 ปีล่าสุด
    if recent_pfcf:
        pfcf_sorted = sorted(p["pfcf"] for p in recent_pfcf)
        median_pfcf = pfcf_sorted[len(pfcf_sorted) // 2]
        pfcf_reference = {"median": round(median_pfcf, 1), "label": f"{round(median_pfcf)}x P/FCF (median, ~2 ปีล่าสุด)"}

    # ── ชั้น 2-5: Sector-aware valuation ────────────────────────────────────
    # จำแนก sector จาก SIC ของ SEC (แม่นสุด) → เลือก profile → ตรวจ validation gate → เตือน
    try:
        subs = await edgar.get_submissions(symbol)
        sic = subs.get("sic") or None
    except Exception:  # noqa: BLE001 — ดึง SIC ไม่ได้ก็ยังจำแนกจากงบได้ (heuristic)
        sic = None

    fin_snapshot = _financials_snapshot(facts)
    sector_info = sector_profile.classify_sector(sic, fin_snapshot)
    profile_key = sector_info["profile"]
    profile = sector_profile.get_profile(profile_key)
    warnings = list(profile.get("warnings", []))

    # ชั้น 4: validation gate สำหรับเส้น "ราคาตาม FCF ต่อหุ้น" — กันเส้นระเบิดเมื่อ FCF เคยติดลบ
    if fcf_series_for_pfcf:
        fcf_check = sector_profile.validate_anchor(
            [{"value": e.get("fcf_per_share"), "date": e.get("period")} for e in fcf_series_for_pfcf],
            "FCF ต่อหุ้น")
        if not fcf_check["ok"]:
            warnings.append(fcf_check["reason"])
        else:
            warnings.extend(fcf_check["issues"])

    # ชั้น 5: median + IQR band แทนเส้น median เดี่ยว (บอกผู้ใช้ว่า fair value เป็นช่วง ไม่ใช่จุดเดียว)
    pe_band = sector_profile.median_band([p["pe"] for p in recent_pe]) if recent_pe else None
    if pe_reference and pe_band:
        pe_reference["low"] = round(pe_band["p25"], 1)
        pe_reference["high"] = round(pe_band["p75"], 1)

    # ชั้น 3: เคารพ metric ที่ปิดตาม sector — P/FCF ไม่มีความหมายกับธนาคาร/REIT
    pfcf_band = sector_profile.median_band([p["pfcf"] for p in recent_pfcf]) if recent_pfcf else None
    if sector_profile.is_disabled(profile_key, "pfcf"):
        pfcf_reference = None
    elif pfcf_reference and pfcf_band:
        pfcf_reference["low"] = round(pfcf_band["p25"], 1)
        pfcf_reference["high"] = round(pfcf_band["p75"], 1)

    # ชั้น 3 ส่วนขยาย: metric เฉพาะ sector (ตัวเลขจริง) + ราคายุติธรรมตาม primary metric
    extra_metrics, fair_value, extra_warns = _compute_sector_extras(
        profile_key, facts, bars, bars_per_q, fin_snapshot, fcf_annual, pe_band, pfcf_band)
    warnings.extend(extra_warns)

    # ข้อ 4: สถิติการเติบโต (CAGR รายได้/EPS หลายหน้าต่าง) สำหรับแผงคาดการณ์มูลค่าอนาคต ฝั่ง frontend
    # TTM revenue = ผลรวมรายได้ 4 ไตรมาสติดกัน (เกลี่ยฤดูกาล เทียบข้ามปีได้) — คู่ขนานกับ ttm_eps_by_date
    rev_dates = sorted(raw_revenue_meta.keys())
    ttm_revenue = [(rev_dates[i], sum(raw_revenue_meta[d]["val"] for d in rev_dates[i - 3:i + 1]))
                   for i in range(3, len(rev_dates))]
    growth_stats = _growth_stats(ttm_revenue, ttm_eps_by_date,
                                 bars[-1]["close"] if bars else None)

    # ชั้น 6: AI narrative — เรียกเฉพาะเมื่อผู้ใช้ร้องขอ (ai=true) และตั้งค่าคีย์ AI แล้ว
    verdict = await _generate_verdict(symbol, facts.get("entityName"), profile, profile_key,
                                      extra_metrics, fair_value, warnings) if include_ai else None

    window = max(len(quarters) * bars_per_q, 60)
    return {
        "symbol": symbol,
        "entity_name": facts.get("entityName"),
        "granularity": granularity,
        "quarters": quarters,
        "price_candles": bars[-window:],
        "pe_series": pe_series[-window:],
        "pe_reference": pe_reference,
        "fcf_annual": fcf_annual,
        "fcf_quarterly": fcf_quarterly,
        "pfcf_series": pfcf_series[-window:],
        "pfcf_reference": pfcf_reference,
        "sic": sic,
        "sector": sector_info["sector"],
        "sector_label": profile["label"],
        "sector_source": sector_info["source"],
        "profile": {
            "key": profile_key,
            "label": profile["label"],
            "primary": profile.get("primary"),
            "disabled": profile.get("disabled", []),
            "extra_metrics": profile.get("extra_metrics", []),
        },
        "warnings": warnings,
        "extra_metrics": extra_metrics,
        "fair_value": fair_value,
        "growth_stats": growth_stats,
        "verdict": verdict,
        "has_preliminary": bool(prelim),
        "disclaimer": "ข้อมูลจาก SEC EDGAR (งบที่ยื่นจริง) + ราคาย้อนหลังจาก provider ปัจจุบัน — "
                      "P/E ช่วงเก่า (หลายปีก่อน) ของหุ้นที่เคย split อาจคลาดเคลื่อนถ้า SEC ไม่มีข้อมูลปรับปรุงย้อนหลังครบ — "
                      + ("ไตรมาสล่าสุด (มี * ต่อท้าย) เป็นตัวเลขเบื้องต้นจาก Yahoo ที่บริษัทประกาศเอง "
                         "ยังไม่ผ่านการยื่น 10-Q อย่างเป็นทางการกับ SEC อาจมีการปรับแก้ภายหลัง — "
                         if prelim else "")
                      + "เพื่อการศึกษาเท่านั้น ไม่ใช่คำแนะนำการลงทุน",
    }


async def sector_fair_value(symbol: str, *, granularity: str = "weekly", refresh: bool = False,
                            max_bars: int = 900) -> dict:
    """ประเมิน "ราคายุติธรรมตาม sector" ของหุ้นตัวเดียว — ตรรกะเดียวกับกล่องประเมินมูลค่าในแท็บ
    📈 โมเดลรายได้ (จำแนก sector → เลือก metric หลักของกลุ่ม → median multiple + band p25–p75)
    แต่ตัดส่วนที่ตัวสแกนไม่ได้ใช้ออก (กล่อง YoY, ซีรีส์กราฟ, ไตรมาสเบื้องต้นจาก Yahoo, AI verdict)
    เพื่อให้รันไล่ทั้ง S&P 500 ได้ในเวลาที่ยอมรับได้.

    ต่างจาก get_revenue_model จุดเดียว: สร้าง band ของ P/FCF จาก FCF "รายปี" (10-K) แทน TTM ราย
    ไตรมาส — เป็นชุดข้อมูลเดียวกับที่โมเดลหลัก fallback ไปใช้อยู่แล้วเมื่อข้อมูลรายไตรมาสไม่พอ ผลจึง
    ต่างกันได้เล็กน้อยเฉพาะกลุ่ม "ลงทุนหนัก" (ที่ใช้ P/FCF เป็นฐาน) ส่วน P/E, P/B, P/FFO เหมือนกันทุกประการ.

    คืน dict ที่มี fair_value (อาจเป็น None ถ้ากลุ่มนั้นประเมินด้วย multiple ไม่ได้ เช่น biotech)
    ยก ValueError ถ้าไม่ใช่หุ้น US ที่ยื่น SEC หรือข้อมูลไม่พอ."""
    symbol = (symbol or "").strip().upper()
    if not symbol:
        raise ValueError("กรุณาระบุสัญลักษณ์หุ้น")
    if granularity not in _BARS_PER_QUARTER:
        raise ValueError("granularity ต้องเป็น daily, weekly หรือ monthly")

    facts = await edgar.get_company_facts(symbol, force_refresh=refresh)

    # EPS TTM รายไตรมาส (สำหรับ P/E band) — สังเคราะห์ไตรมาสที่ไม่ได้ยื่นแยกเหมือนโมเดลหลัก
    raw_eps = {d: m["val"] for d, m in _synthesize_missing_quarter(
        _quarterly_with_fp(facts, _EPS, "USD/shares", prefer_latest_value=True),
        _annual_with_end(facts, _EPS, "USD/shares", prefer_latest_value=True),
    ).items()}
    eps_dates = sorted(raw_eps)
    ttm_eps_by_date = [(eps_dates[i], sum(raw_eps[d] for d in eps_dates[i - 3:i + 1]))
                       for i in range(3, len(eps_dates))]
    fcf_annual = _fcf_annual_points(facts)

    from app.main import provider  # lazy import กันวน circular import (ตามแบบ get_revenue_model)
    get_history = getattr(provider, "get_history", None)
    candles = await get_history(symbol, "1d", max_bars=max_bars) if get_history \
        else await provider.get_candles(symbol, "1d", max_bars)
    if not candles:
        raise ValueError("ดึงราคาย้อนหลังไม่ได้ — ประเมินตัวคูณ (multiple) ที่ตลาดให้ไม่ได้")
    bars = _resample(candles, granularity)
    bars_per_q = _BARS_PER_QUARTER[granularity]

    def _band(per_share_at):
        """median+IQR ของ (ราคา ÷ metric ต่อหุ้น) จาก ~2 ปีล่าสุด (8 ไตรมาส)."""
        ratios = []
        for w in bars[-(bars_per_q * 8):]:
            w_date = datetime.fromtimestamp(w["time"], tz=timezone.utc).date().isoformat()
            ps = per_share_at(w_date)
            if ps and ps > 0:
                ratios.append(w["close"] / ps)
        return sector_profile.median_band(ratios)

    pe_band = _band(lambda d: next((v for qd, v in reversed(ttm_eps_by_date) if qd <= d), None))
    pfcf_band = _band(lambda d: next((e["fcf_per_share"] for e in reversed(fcf_annual)
                                      if e["period"] <= d and e["fcf_per_share"]), None))

    try:
        sic = (await edgar.get_submissions(symbol)).get("sic") or None
    except Exception:  # noqa: BLE001 — ดึง SIC ไม่ได้ก็ยังจำแนกจากงบได้ (heuristic)
        sic = None

    fin_snapshot = _financials_snapshot(facts)
    sector_info = sector_profile.classify_sector(sic, fin_snapshot)
    profile_key = sector_info["profile"]
    profile = sector_profile.get_profile(profile_key)
    if sector_profile.is_disabled(profile_key, "pfcf"):
        pfcf_band = None

    extra_metrics, fair_value, extra_warns = _compute_sector_extras(
        profile_key, facts, bars, bars_per_q, fin_snapshot, fcf_annual, pe_band, pfcf_band)

    return {
        "symbol": symbol,
        "entity_name": facts.get("entityName"),
        "price": round(bars[-1]["close"], 2),
        "sic": sic,
        "sector": sector_info["sector"],
        "sector_label": profile["label"],
        "sector_source": sector_info["source"],
        "profile_key": profile_key,
        "primary": profile.get("primary"),
        "fair_value": fair_value,
        "extra_metrics": extra_metrics,
        # แยกคำเตือน 2 แบบ: profile_warnings = ข้อความประจำกลุ่มธุรกิจ (เหมือนกันทุกตัวในกลุ่ม)
        # ส่วน data_warnings = ปัญหาที่พบในข้อมูลของ "หุ้นตัวนี้" จริง ๆ (ฐานติดลบ/ข้อมูลไม่ครบ/M&A)
        "profile_warnings": list(profile.get("warnings", [])),
        "data_warnings": extra_warns,
    }
