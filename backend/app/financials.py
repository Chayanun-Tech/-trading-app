"""แปลง companyfacts (XBRL ดิบจาก SEC) → งบการเงินย้อนหลังหลายปี แบบ macrotrends.

ความท้าทาย: แต่ละบริษัทแท็ก us-gaap ไม่เหมือนกัน → ต้องมี fallback หลายแท็กต่อ 1 เมตริก,
และต้องแยก duration (งบกำไร/กระแสเงินสด: มี start+end) กับ instant (งบดุล: end อย่างเดียว).

annual = form 10-K (fp FY); quarterly = form 10-Q (ช่วง ~90 วัน).
หมายเหตุ v1: รายไตรมาสยังไม่สังเคราะห์ Q4 (= FY − 9 เดือน) — แสดงไตรมาสที่ยื่นจริง.
"""
from __future__ import annotations

from datetime import date

# (key, label, [concepts ตามลำดับ fallback]) — us-gaap ก่อน แล้วต่อด้วย ifrs-full สำหรับหุ้นต่างชาติ
# (ADR) ที่ยื่น 20-F ด้วย taxonomy IFRS (เช่น TSM=TWD, ASML=EUR). _entries รวมทั้งสอง taxonomy ให้.
_INCOME = [
    ("revenue", "รายได้ (Revenue)", ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
                            "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax",
                            "RevenueFromContractsWithCustomers", "Revenue"]),
    ("cost_of_revenue", "ต้นทุนขาย (Cost of Revenue)", ["CostOfGoodsAndServicesSold", "CostOfRevenue",
                            "CostOfGoodsSold", "CostOfSales"]),
    ("gross_profit", "กำไรขั้นต้น (Gross Profit)", ["GrossProfit"]),
    ("operating_income", "กำไรจากการดำเนินงาน (Operating Income)", ["OperatingIncomeLoss",
                            "ProfitLossFromOperatingActivities"]),
    ("net_income", "กำไรสุทธิ (Net Income)", ["NetIncomeLoss", "ProfitLoss",
                            "ProfitLossAttributableToOwnersOfParent"]),
    ("rnd", "ค่าวิจัยและพัฒนา (R&D)", ["ResearchAndDevelopmentExpense"]),
    ("sga", "ค่าใช้จ่ายขาย & บริหาร (SG&A)", ["SellingGeneralAndAdministrativeExpense"]),
    ("interest_expense", "ดอกเบี้ยจ่าย (Interest Expense)", ["InterestExpense", "InterestExpenseNonoperating"]),
    ("income_tax", "ภาษีเงินได้ (Income Tax)", ["IncomeTaxExpenseBenefit"]),
]
_BALANCE = [
    ("total_assets", "สินทรัพย์รวม (Total Assets)", ["Assets"]),
    ("current_assets", "สินทรัพย์หมุนเวียน (Current Assets)", ["AssetsCurrent"]),
    ("cash", "เงินสด & เทียบเท่า (Cash & Equivalents)", ["CashAndCashEquivalentsAtCarryingValue",
                                    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"]),
    ("inventory", "สินค้าคงเหลือ (Inventory)", ["InventoryNet"]),
    ("receivables", "ลูกหนี้การค้า (Receivables)", ["AccountsReceivableNetCurrent"]),
    ("total_liabilities", "หนี้สินรวม (Total Liabilities)", ["Liabilities"]),
    ("current_liabilities", "หนี้สินหมุนเวียน (Current Liabilities)", ["LiabilitiesCurrent"]),
    ("short_term_debt", "หนี้ระยะสั้น (Short-term Debt)", ["DebtCurrent", "LongTermDebtCurrent", "ShortTermBorrowings"]),
    ("long_term_debt", "หนี้ระยะยาว (Long-term Debt)", ["LongTermDebtNoncurrent", "LongTermDebt"]),
    ("total_equity", "ส่วนของผู้ถือหุ้น (Shareholders' Equity)", ["StockholdersEquity",
                            "EquityAttributableToOwnersOfParent", "Equity"]),
    ("retained_earnings", "กำไรสะสม (Retained Earnings)", ["RetainedEarningsAccumulatedDeficit"]),
    ("goodwill", "ค่าความนิยม (Goodwill)", ["Goodwill"]),
]
_CASHFLOW = [
    ("operating_cash_flow", "เงินสดจากการดำเนินงาน (Operating Cash Flow)", ["NetCashProvidedByUsedInOperatingActivities",
                                                      "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
                                                      "CashFlowsFromUsedInOperatingActivities"]),
    ("capex", "ลงทุนในสินทรัพย์ (CapEx)", ["PaymentsToAcquirePropertyPlantAndEquipment",
                                            "PaymentsToAcquireProductiveAssets",
                                            "PurchaseOfPropertyPlantAndEquipment"]),
    ("investing_cash_flow", "เงินสดจากการลงทุน (Investing Cash Flow)", ["NetCashProvidedByUsedInInvestingActivities"]),
    ("financing_cash_flow", "เงินสดจากการจัดหาเงิน (Financing Cash Flow)", ["NetCashProvidedByUsedInFinancingActivities"]),
    ("dividends_paid", "เงินปันผลจ่าย (Dividends Paid)", ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"]),
    ("buybacks", "ซื้อหุ้นคืน (Buybacks)", ["PaymentsForRepurchaseOfCommonStock"]),
]
_EPS = ["EarningsPerShareDiluted", "EarningsPerShareBasic",
        "DilutedEarningsLossPerShare", "BasicEarningsLossPerShare"]
_SHARES = ["WeightedAverageNumberOfDilutedSharesOutstanding",
           "WeightedAverageNumberOfSharesOutstandingBasic",
           "WeightedAverageNumberOfShareOutstandingBasicAndDiluted",
           "WeightedAverageShares", "WeightedAverageSharesOutstanding"]

# taxonomy ที่รองรับ: us-gaap (หุ้น US) + ifrs-full (ADR ต่างชาติที่ยื่น 20-F ด้วย IFRS)
_TAXONOMIES = ("us-gaap", "ifrs-full")
# ฟอร์มงบ "รายปี": 10-K (US), 20-F (foreign private issuer), 40-F (Canada MJDS)
_ANNUAL_FORMS = ("10-K", "20-F", "40-F")


def _days(start: str, end: str) -> int | None:
    try:
        y1, m1, d1 = map(int, start.split("-"))
        y2, m2, d2 = map(int, end.split("-"))
        return (date(y2, m2, d2) - date(y1, m1, d1)).days
    except Exception:
        return None


def _entries(facts: dict, concepts: list[str], unit: str) -> list[dict]:
    """รวม array ของทุกแท็ก fallback (เรียงตามลำดับความสำคัญ) เพื่อต่อช่วงปีให้ครบ.

    บริษัทมักเปลี่ยนแท็ก us-gaap ตามปี (เช่น Apple ใช้ SalesRevenueNet ก่อนปี 2019
    แล้วเปลี่ยนเป็น RevenueFromContractWithCustomer...). ถ้าเอาแค่แท็กแรกจะขาดปีเก่า ๆ
    จึง merge ทุกแท็ก โดยตัวเลือกแก้ทับ (dedup by fy/end) ให้แท็กลำดับต้นชนะเมื่อชนกัน.

    รองรับทั้ง taxonomy us-gaap และ ifrs-full (หุ้น ADR ต่างชาติที่ยื่น 20-F ด้วย IFRS) — หุ้น US
    ไม่มี node ifrs-full จึงได้ผลเหมือนเดิมทุกประการ. concept ลำดับต้นชนะ, us-gaap ชนะ ifrs-full
    เมื่อ concept เดียวกัน (จึงวน concepts เป็นวงนอก, taxonomy เป็นวงใน).
    """
    all_facts = facts.get("facts", {})
    merged: list[dict] = []
    for c in concepts:
        for taxo in _TAXONOMIES:
            node = all_facts.get(taxo, {}).get(c)
            if node:
                arr = node.get("units", {}).get(unit)
                if arr:
                    merged.extend(arr)
    return merged


def pick_reporting_unit(facts: dict, concepts: list[str], per_share: bool = False) -> str:
    """เดาหน่วยสกุลเงินหลักที่บริษัทใช้ยื่นงบสำหรับ concept กลุ่มนี้ (หุ้น US = USD เสมอ; ADR ต่างชาติ
    อาจเป็น TWD/EUR/JPY/CNY). เลือกหน่วยที่มีจำนวน fact มากที่สุด. per_share=True เลือกหน่วยแบบ
    'สกุล/หุ้น' (เช่น TWD/shares) สำหรับ EPS. คืน 'USD'/'USD/shares' เมื่อหาไม่เจอ."""
    all_facts = facts.get("facts", {})
    counts: dict[str, int] = {}
    for c in concepts:
        for taxo in _TAXONOMIES:
            node = all_facts.get(taxo, {}).get(c)
            if not node:
                continue
            for unit, arr in node.get("units", {}).items():
                if ("/" in unit) != per_share:
                    continue
                counts[unit] = counts.get(unit, 0) + len(arr or [])
    if not counts:
        return "USD/shares" if per_share else "USD"
    return max(counts, key=counts.get)


def _is_annual_form(form: str) -> bool:
    return form.startswith(_ANNUAL_FORMS)


def _annual_fp_ok(form: str, fp) -> bool:
    """10-K (US) ยึด fp='FY' เป๊ะเหมือนเดิม (ไม่เปลี่ยนพฤติกรรมหุ้น US). ฟอร์มต่างชาติ (20-F/40-F)
    ยอมรับ fp='FY' หรือ None เพราะบางฉบับไม่ติดแท็ก fp — อาศัยตัวกรอง duration 300–400 วันคุมอีกชั้น."""
    if form.startswith("10-K"):
        return fp == "FY"
    return fp in ("FY", None)


def _annual_duration(entries: list[dict]) -> dict:
    out: dict[int, tuple[str, float]] = {}
    for e in entries:
        form = str(e.get("form", ""))
        if "start" not in e or not _is_annual_form(form):
            continue
        if not _annual_fp_ok(form, e.get("fp")):
            continue
        d = _days(e["start"], e["end"])
        if d is None or d < 300 or d > 400:
            continue
        fy = e.get("fy")
        if fy is None:
            continue
        if fy not in out or e["end"] > out[fy][0]:
            out[fy] = (e["end"], e["val"])
    return {fy: v for fy, (_, v) in out.items()}


def _annual_instant(entries: list[dict]) -> dict:
    out: dict[int, tuple[str, float]] = {}
    for e in entries:
        form = str(e.get("form", ""))
        if "start" in e or not _is_annual_form(form):
            continue
        if not _annual_fp_ok(form, e.get("fp")):
            continue
        fy = e.get("fy")
        if fy is None:
            continue
        if fy not in out or e["end"] > out[fy][0]:
            out[fy] = (e["end"], e["val"])
    return {fy: v for fy, (_, v) in out.items()}


def _quarterly_duration(entries: list[dict]) -> dict:
    out: dict[str, float] = {}
    for e in entries:
        if "start" not in e or not str(e.get("form", "")).startswith("10-Q"):
            continue
        d = _days(e["start"], e["end"])
        if d is None or d < 80 or d > 100:
            continue
        out.setdefault(e["end"], e["val"])
    return out


def _quarterly_instant(entries: list[dict]) -> dict:
    out: dict[str, float] = {}
    for e in entries:
        if "start" in e or not str(e.get("form", "")).startswith("10-Q"):
            continue
        out.setdefault(e["end"], e["val"])
    return out


def _q_label(end: str) -> str:
    """ป้ายไตรมาส = เดือน-ปีที่งบจบ (ชัดกว่า 'Qn' สำหรับบริษัทที่ปีงบไม่จบ ธ.ค.)."""
    try:
        y, m, _ = map(int, end.split("-"))
        return f"{y}-{m:02d}"
    except Exception:
        return end


def _safe_div(a, b):
    if a is None or b in (None, 0):
        return None
    return a / b


def build_financials(facts: dict, freq: str = "annual", max_periods: int | None = None) -> dict:
    """สร้างงบย้อนหลังจาก companyfacts. freq = 'annual' | 'quarterly'."""
    annual = freq == "annual"
    dur = _annual_duration if annual else _quarterly_duration
    inst = _annual_instant if annual else _quarterly_instant
    if max_periods is None:
        max_periods = 15 if annual else 40

    raw: dict[str, dict] = {}
    for key, _, concepts in _INCOME + _CASHFLOW:
        raw[key] = dur(_entries(facts, concepts, "USD"))
    for key, _, concepts in _BALANCE:
        raw[key] = inst(_entries(facts, concepts, "USD"))
    raw["eps_diluted"] = dur(_entries(facts, _EPS, "USD/shares"))
    raw["shares_diluted"] = dur(_entries(facts, _SHARES, "shares"))

    # แกนช่วงเวลา: รวมคีย์ทุกเมตริก แล้วเรียงจากเก่า→ใหม่ เอาท้ายสุด max_periods
    all_keys = set()
    for series in raw.values():
        all_keys.update(series.keys())
    periods = sorted(all_keys)[-max_periods:]

    def derived(fn) -> dict:
        out = {}
        for p in periods:
            v = fn(p)
            if v is not None:
                out[p] = v
        return out

    g = lambda k, p: raw.get(k, {}).get(p)  # noqa: E731

    # เติมกำไรขั้นต้นถ้าขาด, คำนวณ FCF + อัตราส่วน
    raw["gross_profit"] = {p: (g("gross_profit", p) if g("gross_profit", p) is not None
                               else (None if g("revenue", p) is None or g("cost_of_revenue", p) is None
                                     else g("revenue", p) - g("cost_of_revenue", p)))
                           for p in periods}
    raw["gross_profit"] = {p: v for p, v in raw["gross_profit"].items() if v is not None}
    raw["free_cash_flow"] = derived(lambda p: (None if g("operating_cash_flow", p) is None or g("capex", p) is None
                                               else g("operating_cash_flow", p) - g("capex", p)))
    total_debt = derived(lambda p: (None if g("short_term_debt", p) is None and g("long_term_debt", p) is None
                                    else (g("short_term_debt", p) or 0) + (g("long_term_debt", p) or 0)))

    ratios = {
        "gross_margin": derived(lambda p: _safe_div(g("gross_profit", p), g("revenue", p))),
        "operating_margin": derived(lambda p: _safe_div(g("operating_income", p), g("revenue", p))),
        "net_margin": derived(lambda p: _safe_div(g("net_income", p), g("revenue", p))),
        "fcf_margin": derived(lambda p: _safe_div(raw["free_cash_flow"].get(p), g("revenue", p))),
        "roe": derived(lambda p: _safe_div(g("net_income", p), g("total_equity", p))),
        "roa": derived(lambda p: _safe_div(g("net_income", p), g("total_assets", p))),
        "current_ratio": derived(lambda p: _safe_div(g("current_assets", p), g("current_liabilities", p))),
        "debt_to_equity": derived(lambda p: _safe_div(total_debt.get(p), g("total_equity", p))),
    }
    pershare = {
        "eps_diluted": raw["eps_diluted"],
        "revenue_per_share": derived(lambda p: _safe_div(g("revenue", p), g("shares_diluted", p))),
        "fcf_per_share": derived(lambda p: _safe_div(raw["free_cash_flow"].get(p), g("shares_diluted", p))),
        "book_value_per_share": derived(lambda p: _safe_div(g("total_equity", p), g("shares_diluted", p))),
        "shares_diluted": raw["shares_diluted"],
    }

    def metrics(spec: list[tuple], extra: dict | None = None, unit: str = "USD") -> list[dict]:
        rows = []
        for key, label, _ in spec:
            rows.append({"key": key, "label": label, "unit": unit,
                         "values": [raw.get(key, {}).get(p) for p in periods]})
        for key, label, u in (extra or {}).get("items", []):
            src = (extra or {})["src"]
            rows.append({"key": key, "label": label, "unit": u,
                         "values": [src.get(key, {}).get(p) for p in periods]})
        return rows

    income_rows = metrics(_INCOME)
    income_rows.append({"key": "eps_diluted", "label": "กำไรต่อหุ้น (EPS diluted)", "unit": "USD/shares",
                        "values": [raw["eps_diluted"].get(p) for p in periods]})
    cashflow_rows = metrics(_CASHFLOW)
    cashflow_rows.insert(2, {"key": "free_cash_flow", "label": "กระแสเงินสดอิสระ (Free Cash Flow)", "unit": "USD",
                             "values": [raw["free_cash_flow"].get(p) for p in periods]})

    ratio_labels = [
        ("gross_margin", "อัตรากำไรขั้นต้น (Gross Margin)", "%"), ("operating_margin", "อัตรากำไรดำเนินงาน (Operating Margin)", "%"),
        ("net_margin", "อัตรากำไรสุทธิ (Net Margin)", "%"), ("fcf_margin", "อัตรา FCF (FCF Margin)", "%"),
        ("roe", "ผลตอบแทนผู้ถือหุ้น (ROE)", "%"), ("roa", "ผลตอบแทนสินทรัพย์ (ROA)", "%"),
        ("current_ratio", "อัตราส่วนสภาพคล่อง (Current Ratio)", "ratio"), ("debt_to_equity", "หนี้/ทุน (D/E)", "ratio"),
    ]
    pershare_labels = [
        ("eps_diluted", "กำไรต่อหุ้น (EPS diluted)", "USD/shares"), ("revenue_per_share", "รายได้/หุ้น (Revenue/Share)", "USD/shares"),
        ("fcf_per_share", "FCF/หุ้น (FCF/Share)", "USD/shares"), ("book_value_per_share", "มูลค่าทางบัญชี/หุ้น (Book Value/Share)", "USD/shares"),
        ("shares_diluted", "จำนวนหุ้นเฉลี่ย (Shares, avg)", "shares"),
    ]

    period_labels = [str(p) if annual else _q_label(p) for p in periods]
    return {
        "symbol": (facts.get("entityName") or "").upper() or None,
        "entity_name": facts.get("entityName"),
        "cik": facts.get("cik"),
        "freq": freq,
        "periods": period_labels,
        "groups": [
            {"key": "income", "label": "งบกำไรขาดทุน", "metrics": income_rows},
            {"key": "balance", "label": "งบดุล", "metrics": metrics(_BALANCE)},
            {"key": "cashflow", "label": "งบกระแสเงินสด", "metrics": cashflow_rows},
            {"key": "ratios", "label": "อัตราส่วน & มาร์จิน",
             "metrics": [{"key": k, "label": lb, "unit": u, "values": [ratios[k].get(p) for p in periods]}
                         for k, lb, u in ratio_labels]},
            {"key": "pershare", "label": "ต่อหุ้น",
             "metrics": [{"key": k, "label": lb, "unit": u, "values": [pershare[k].get(p) for p in periods]}
                         for k, lb, u in pershare_labels]},
        ],
        "disclaimer": "ข้อมูลจาก SEC EDGAR (งบที่บริษัทยื่นจริง). บางแท็กอาจคลาดเคลื่อน/ขาดช่วง — ตรวจงบจริงประกอบ",
    }


def latest_snapshot(facts: dict) -> dict:
    """สร้าง snapshot ปัจจัยพื้นฐาน 'ล่าสุด' จาก EDGAR (เสถียร ไม่โดน rate limit เหมือน yfinance).

    ใช้เป็นฐานให้สาย VI: roe/margin/growth/หนี้/สภาพคล่อง/FCF คำนวณจากงบได้เลย ไม่ต้องใช้ราคา.
    ส่วนที่ต้องใช้ราคา (P/E, P/B, PEG, fcf_yield, market_cap) เติมทีหลังใน fundamentals.py.
    """
    fin = build_financials(facts, "annual")
    n = len(fin["periods"])
    if n == 0:
        return {}
    last = n - 1

    def val(group_key: str, metric_key: str, i: int = last):
        grp = next((g for g in fin["groups"] if g["key"] == group_key), None)
        if not grp:
            return None
        m = next((x for x in grp["metrics"] if x["key"] == metric_key), None)
        if not m or not (0 <= i < len(m["values"])):
            return None
        return m["values"][i]

    rev, rev_prev = val("income", "revenue"), (val("income", "revenue", last - 1) if last >= 1 else None)
    ni, ni_prev = val("income", "net_income"), (val("income", "net_income", last - 1) if last >= 1 else None)
    equity = val("balance", "total_equity")
    shares = val("pershare", "shares_diluted")
    cash = val("balance", "cash")
    std, ltd = val("balance", "short_term_debt"), val("balance", "long_term_debt")
    total_debt = None if std is None and ltd is None else (std or 0) + (ltd or 0)
    div_paid = val("cashflow", "dividends_paid")
    return {
        "symbol": fin.get("symbol"),
        "long_name": fin.get("entity_name"),
        "sector": None,
        "summary": None,
        "revenue": rev,
        "net_income": ni,
        "eps": val("income", "eps_diluted"),
        "shares": shares,
        "total_equity": equity,
        "cash": cash,
        "total_debt": total_debt,
        "fcf": val("cashflow", "free_cash_flow"),
        "dividends_paid": div_paid,
        # ค่าต่อหุ้นสำหรับสาย IV (Graham/DDM) — คำนวณจากงบ EDGAR โดยตรง
        "bvps": (equity / shares if equity and shares and shares > 0 else None),
        "dps": (abs(div_paid) / shares if div_paid and shares and shares > 0 else None),
        "roe": val("ratios", "roe"),
        "gross_margin": val("ratios", "gross_margin"),
        "operating_margin": val("ratios", "operating_margin"),
        "current_ratio": val("ratios", "current_ratio"),
        "debt_to_equity": val("ratios", "debt_to_equity"),
        "revenue_growth": (None if not rev or not rev_prev else (rev - rev_prev) / abs(rev_prev)),
        "earnings_growth": (None if not ni or not ni_prev else (ni - ni_prev) / abs(ni_prev)),
    }
