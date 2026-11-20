"""มูลค่าที่แท้จริงแบบ "ฉันทามติหลายโมเดล" (Consensus Intrinsic Value) — ออกแบบให้ลด bias.

ปัญหาของวิธีเดิม (value_scanner → ตัวคูณ median ~2 ปีของหุ้นตัวเอง):
  ถ้าตลาดเคยให้ราคาแพงเกินเหตุ (PLTR P/E 346 เท่า) หรือกำไรทางบัญชีถูกกด (AVGO หลังซื้อ VMware)
  ราคายุติธรรมจะ "ลอก" ความแพง/ความเพี้ยนในอดีตมาด้วย — คือใช้อารมณ์ตลาดมาตัดสินอารมณ์ตลาด.

หลักการของโมดูลนี้:
  1. ฐานคือ "เงินสดที่ธุรกิจสร้างให้ผู้ถือหุ้นจริง" ไม่ใช่ตัวคูณที่ตลาดเคยให้
     • Owner FCF = OCF − CapEx − ค่าตอบแทนเป็นหุ้น (SBC)  ← SBC คือค่าใช้จ่ายจริงที่จ่ายด้วยหุ้น
       (บริษัทเทคหลายตัว FCF ดูดีเพราะจ่ายพนักงานเป็นหุ้น ถ้าไม่หักออก มูลค่าจะสูงเกินจริง)
     • ใช้ "margin มัธยฐาน 5 ปี × รายได้ TTM" — ตัดผลของปีพิเศษ/วัฏจักร แต่ยังโตตามรายได้ปัจจุบัน
  2. สมมติฐานเดียวกันทุกหุ้น (กำหนดตายตัวในโค้ด ไม่ปรับตามความชอบ):
     • อัตราคิดลด 8.5% (หุ้น US) / 9.5% (ADR ต่างชาติ — ความเสี่ยงประเทศ/ค่าเงิน)
     • อัตราโตปี 1–5 = CAGR รายได้ย้อนหลัง จำกัด −5%…25% แล้วค่อย ๆ ลดลงเหลือ 2.5% ในปีที่ 10
  3. หลายโมเดลที่ "ไม่ขึ้นต่อกัน" แล้วเอาค่ากลาง — โมเดลเดียวผิดได้ หลายโมเดลผิดพร้อมกันยากกว่า
     A. DCF ของ Owner FCF (bear/base/bull)
     B. Earnings Power × P/E ที่สมเหตุผลตามการเติบโต (Graham: 8.5 + 1.5g, จำกัด 8–28 เท่า)
     C. ตัวคูณในอดีตของหุ้นเอง (โมเดลเดิม) — ใช้เฉพาะเมื่อตัวคูณไม่สุดโต่ง
     D. ธนาคาร/ประกัน: Justified P/B = (ROE − g)/(r − g)  (FCF ใช้กับธุรกิจการเงินไม่ได้)
  4. เกรดความน่าเชื่อ = โมเดลเห็นตรงกันแค่ไหน + ข้อมูลครบ/สดแค่ไหน (ไม่ใช่ดูจากโมเดลเดียว)
  5. Reverse DCF — "ราคาวันนี้สมมติว่าธุรกิจต้องโตปีละกี่ %" เทียบกับที่เคยโตจริง
     ตัวนี้ช่วยกัน bias ได้ดีที่สุด เพราะไม่ต้องเดาอนาคต แค่ถามว่าความคาดหวังในราคาสมจริงไหม.

ทุกค่าคำนวณเป็น "มูลค่าส่วนผู้ถือหุ้นรวม" ในสกุลเงินงบ แล้วแปลงเป็นราคาต่อหุ้นด้วย
  ราคาเหมาะสม = ราคาปัจจุบัน × (มูลค่าส่วนผู้ถือหุ้น ÷ มูลค่าตลาด)
ซึ่งไม่ต้องยุ่งกับจำนวนหุ้น/split/อัตราแปลง ADR เลย (ตัดต้นเหตุบั๊กเดิมทิ้งทั้งกลุ่ม).
"""
from __future__ import annotations

import statistics
from datetime import date

from app import edgar, sector_profile
from app.financials import _entries, pick_reporting_unit
from app.revenue_model import (_CAPEX_CONCEPTS, _NET_INCOME_CONCEPTS, _OCF_CONCEPTS,
                               _REVENUE_CONCEPTS, _annual_with_end, _quarterly_from_cumulative,
                               _quarterly_with_fp, _synthesize_missing_quarter)

# ── สมมติฐานกลาง (ใช้เหมือนกันทุกหุ้น — เปลี่ยนที่นี่ที่เดียว) ─────────────────────
DISCOUNT_US = 0.085       # ดอกเบี้ยพันธบัตร 10 ปี ~4.3% + ส่วนชดเชยความเสี่ยงหุ้น ~4.2%
DISCOUNT_FOREIGN = 0.095  # +1% ความเสี่ยงประเทศ/ค่าเงิน
TERMINAL_GROWTH = 0.025
GROWTH_CAP = (-0.05, 0.25)
YEARS = 10
PE_FLOOR, PE_CAP = 8.0, 28.0

_SBC = ["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense",
        "AdjustmentsForSharebasedPayments"]
_CASH = ["CashAndCashEquivalentsAtCarryingValue", "CashAndCashEquivalents",
         "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"]
_ST_INVEST = ["ShortTermInvestments", "MarketableSecuritiesCurrent",
              "AvailableForSaleSecuritiesDebtSecuritiesCurrent", "CurrentFinancialAssetsAtFairValueThroughProfitOrLoss"]
_DEBT_TOTAL = ["LongTermDebt", "DebtLongtermAndShorttermCombinedAmount", "Borrowings"]
_DEBT_PARTS = [["LongTermDebtNoncurrent", "NoncurrentPortionOfNoncurrentBorrowings", "LongTermBorrowings"],
               ["LongTermDebtCurrent", "DebtCurrent", "CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings"],
               ["ShortTermBorrowings", "CommercialPaper"]]
_EQUITY = ["StockholdersEquity", "EquityAttributableToOwnersOfParent"]


# ── ดึงข้อมูลจาก SEC ────────────────────────────────────────────────────────────

def _ttm(facts: dict, concepts: list[str], unit: str, cumulative: bool) -> tuple[float | None, str | None]:
    """ยอด 12 เดือนล่าสุด: รวม 4 ไตรมาสล่าสุด (ถ้าใหม่กว่างบปี) ไม่งั้นใช้งบปีล่าสุด."""
    ann = _annual_with_end(facts, concepts, unit)
    q = (_quarterly_from_cumulative if cumulative else _quarterly_with_fp)(facts, concepts, unit)
    q = _synthesize_missing_quarter(q, ann)
    last_ann = max(ann.values(), key=lambda m: m["end"]) if ann else None
    ends = sorted(q)
    if last_ann:
        # TTM = งบปีล่าสุด + ไตรมาสหลังปิดปี − ไตรมาสเดียวกันของปีก่อน (วิธีมาตรฐาน ใช้ได้แม้ Q4 ขาด)
        after = [e for e in ends if e > last_ann["end"]]
        prior = [_year_before(e, ends) for e in after]
        if after and all(prior):
            return (last_ann["val"] + sum(q[e]["val"] for e in after)
                    - sum(q[p]["val"] for p in prior)), after[-1]
        return last_ann["val"], last_ann["end"]
    if len(ends) >= 4:
        span = (date.fromisoformat(ends[-1]) - date.fromisoformat(ends[-4])).days
        if 250 <= span <= 300:  # 4 ไตรมาสติดกันจริง (ห่าง ~273 วัน)
            return sum(q[e]["val"] for e in ends[-4:]), ends[-1]
    return None, None


def _year_before(end: str, ends: list[str]) -> str | None:
    d = date.fromisoformat(end)
    for e in ends:
        if abs((d - date.fromisoformat(e)).days - 365) <= 20:
            return e
    return None


def _instant_series(facts: dict, concepts: list[str], unit: str) -> dict[str, float]:
    """ยอดคงเหลือทุกวันที่ (end → ค่า) — concept ลำดับต้นชนะ."""
    out: dict[str, float] = {}
    for c in concepts:
        for e in _entries(facts, [c], unit):
            if "start" not in e:
                out.setdefault(e["end"], e["val"])
    return out


def _annual(facts: dict, concepts: list[str], unit: str) -> dict[int, float]:
    return {fy: m["val"] for fy, m in _annual_with_end(facts, concepts, unit).items()}


def _instant(facts: dict, concepts: list[str], unit: str) -> float | None:
    """ค่ายอดคงเหลือ (งบดุล) ล่าสุด — concept ลำดับต้นชนะเมื่อวันที่เท่ากัน."""
    best = None
    for c in concepts:
        for e in _entries(facts, [c], unit):
            if "start" in e or not str(e.get("form", "")).startswith(("10-Q", "10-K", "20-F", "40-F", "6-K")):
                continue
            if best is None or e["end"] > best[0]:
                best = (e["end"], e["val"])
    return best[1] if best else None


def _debt(facts: dict, unit: str) -> float:
    total = _instant(facts, _DEBT_TOTAL, unit)
    parts = [_instant(facts, p, unit) or 0.0 for p in _DEBT_PARTS]
    # LongTermDebt มักรวมส่วนครบกำหนดใน 1 ปีแล้ว — บวกเฉพาะเงินกู้ระยะสั้นเพิ่ม
    return (total + parts[2]) if total else sum(parts)


def gather_inputs(facts: dict) -> dict:
    """ดึงตัวเลขดิบทั้งหมดที่โมเดลต้องใช้ (สกุลเงินตามงบ) — แยกออกมาเพื่อทดสอบได้โดยไม่ต้องต่อเน็ต."""
    unit = pick_reporting_unit(facts, _REVENUE_CONCEPTS)
    rev = _annual(facts, _REVENUE_CONCEPTS, unit)
    ocf = _annual(facts, _OCF_CONCEPTS, unit)
    capex = _annual(facts, _CAPEX_CONCEPTS, unit)
    sbc = _annual(facts, _SBC, unit)
    ni = _annual(facts, _NET_INCOME_CONCEPTS, unit)
    # ส่วนผู้ถือหุ้น ณ วันปิดงบแต่ละปี (เป็นยอดคงเหลือ ไม่ใช่ยอดช่วงเวลา) — ใช้หา ROE ย้อนหลัง
    eq_at = _instant_series(facts, _EQUITY, unit)
    ni_end = _annual_with_end(facts, _NET_INCOME_CONCEPTS, unit)
    eq = {fy: eq_at[m["end"]] for fy, m in ni_end.items() if m["end"] in eq_at}
    rev_ttm, rev_end = _ttm(facts, _REVENUE_CONCEPTS, unit, cumulative=False)
    ni_ttm, ni_end_d = _ttm(facts, _NET_INCOME_CONCEPTS, unit, cumulative=False)
    ocf_ttm, ocf_end = _ttm(facts, _OCF_CONCEPTS, unit, cumulative=True)
    return {
        "unit": unit,
        "revenue": rev, "ocf": ocf, "capex": capex, "sbc": sbc, "net_income": ni, "equity_hist": eq,
        "revenue_ttm": rev_ttm,
        "data_end": max([d for d in (rev_end, ni_end_d, ocf_end) if d], default=None),
        "ocf_ttm": ocf_ttm,
        "capex_ttm": _ttm(facts, _CAPEX_CONCEPTS, unit, cumulative=True)[0],
        "sbc_ttm": _ttm(facts, _SBC, unit, cumulative=True)[0],
        "ni_ttm": ni_ttm,
        "cash": (_instant(facts, _CASH, unit) or 0.0) + (_instant(facts, _ST_INVEST, unit) or 0.0),
        "debt": _debt(facts, unit),
        "equity": _instant(facts, _EQUITY, unit),
    }


# ── โมเดล (ฟังก์ชันบริสุทธิ์ — ทดสอบได้) ─────────────────────────────────────────

def revenue_cagr(rev: dict[int, float], max_years: int = 5) -> float | None:
    yrs = sorted(y for y, v in rev.items() if v and v > 0)
    if len(yrs) < 3:
        return None
    last = yrs[-1]
    first = next(y for y in yrs if y >= last - max_years)  # ปีที่มีข้อมูลจริง (ข้ามปีที่ขาด)
    n = last - first
    if n < 2:
        return None
    return (rev[last] / rev[first]) ** (1 / n) - 1


def _margins(num: dict[int, float], rev: dict[int, float], years: int = 5) -> list[float]:
    ys = sorted(y for y in num if rev.get(y) and rev[y] > 0)[-years:]
    return [num[y] / rev[y] for y in ys]


def owner_fcf_margins(inp: dict) -> list[float]:
    """margin ของ Owner FCF (OCF − CapEx − SBC) รายปี + TTM."""
    rev, ocf, capex, sbc = inp["revenue"], inp["ocf"], inp["capex"], inp["sbc"]
    fcf = {y: ocf[y] - abs(capex.get(y, 0.0)) - abs(sbc.get(y, 0.0)) for y in ocf if y in rev}
    out = _margins(fcf, rev)
    if inp.get("revenue_ttm") and inp.get("ocf_ttm") is not None:
        out.append((inp["ocf_ttm"] - abs(inp.get("capex_ttm") or 0) - abs(inp.get("sbc_ttm") or 0))
                   / inp["revenue_ttm"])
    return out


def dcf_equity(base_fcf: float, g1: float, r: float, net_cash: float,
               g_t: float = TERMINAL_GROWTH, years: int = YEARS) -> float:
    """DCF 10 ปี: ปี 1–5 โต g1, ปี 6–10 ลดแบบเส้นตรงลงหา g_t, แล้วต่อด้วย terminal value."""
    pv, fcf = 0.0, base_fcf
    for i in range(1, years + 1):
        g = g1 if i <= 5 else g1 + (g_t - g1) * (i - 5) / (years - 5)
        fcf *= 1 + g
        pv += fcf / (1 + r) ** i
    tv = fcf * (1 + g_t) / (r - g_t)
    return pv + tv / (1 + r) ** years + net_cash


def reverse_dcf_growth(base_fcf: float, market_cap: float, r: float, net_cash: float) -> float | None:
    """อัตราโตปี 1–5 ที่ทำให้ DCF = มูลค่าตลาดพอดี (bisection) — 'ราคาวันนี้คาดหวังการโตเท่าไหร่'."""
    if base_fcf <= 0 or market_cap <= 0:
        return None
    lo, hi = -0.30, 1.00
    if dcf_equity(base_fcf, hi, r, net_cash) < market_cap:
        return hi  # ต้องโตเกิน 100%/ปี ถึงจะคุ้มราคา
    if dcf_equity(base_fcf, lo, r, net_cash) > market_cap:
        return lo
    for _ in range(60):
        mid = (lo + hi) / 2
        if dcf_equity(base_fcf, mid, r, net_cash) < market_cap:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def model_dcf(inp: dict, g: float, r: float) -> dict | None:
    margins = owner_fcf_margins(inp)
    rev = inp.get("revenue_ttm")
    if len(margins) < 3 or not rev or rev <= 0:
        return None
    m = statistics.median(margins)
    if m <= 0.01:
        return None  # ธุรกิจยังสร้างเงินสดให้เจ้าของไม่ได้จริง — DCF ไม่มีความหมาย
    base = rev * m
    net_cash = inp["cash"] - inp["debt"]
    return {
        "model": "DCF (Owner FCF)",
        "low": dcf_equity(base, g * 0.5, r + 0.01, net_cash),
        "base": dcf_equity(base, g, r, net_cash),
        "high": dcf_equity(base, min(g * 1.3, 0.35) if g > 0 else g * 0.7, r - 0.01, net_cash),
        "note": f"FCF margin {m * 100:.1f}% · โต {g * 100:.1f}%/ปี · คิดลด {r * 100:.0f}%",
        "base_fcf": base,
    }


def model_earnings_power(inp: dict, g: float) -> dict | None:
    margins = _margins(inp["net_income"], inp["revenue"])
    if inp.get("ni_ttm") is not None and inp.get("revenue_ttm"):
        margins.append(inp["ni_ttm"] / inp["revenue_ttm"])
    rev = inp.get("revenue_ttm")
    if len(margins) < 3 or not rev or rev <= 0:
        return None
    m = statistics.median(margins)
    if m <= 0.01:
        return None
    pe = max(PE_FLOOR, min(PE_CAP, 8.5 + 1.5 * max(g, 0) * 100))
    earnings = rev * m
    return {
        "model": "Earnings Power × P/E ตามการเติบโต",
        "low": earnings * pe * 0.8, "base": earnings * pe, "high": earnings * pe * 1.2,
        "note": f"net margin {m * 100:.1f}% · P/E สมเหตุผล {pe:.1f} เท่า",
    }


def model_justified_pb(inp: dict, r: float) -> dict | None:
    eq = inp.get("equity")
    roes = []
    hist_eq = inp.get("equity_hist") or {}
    for y, n in inp["net_income"].items():
        e = hist_eq.get(y)
        if e and e > 0:
            roes.append(n / e)
    if inp.get("ni_ttm") and eq and eq > 0:
        roes.append(inp["ni_ttm"] / eq)
    if not eq or eq <= 0 or len(roes) < 2:
        return None
    roe = statistics.median(roes[-5:])
    g = 0.04
    pb = max(0.5, min(4.0, (roe - g) / (r - g)))
    return {"model": "Justified P/B", "low": eq * pb * 0.8, "base": eq * pb, "high": eq * pb * 1.2,
            "note": f"ROE {roe * 100:.1f}% → P/B {pb:.2f} เท่า"}


def combine(models: list[dict], market_cap_ccy: float, price: float) -> dict | None:
    """รวมหลายโมเดล → กรอบราคาต่อหุ้น + เกรดจากความเห็นตรงกันของโมเดล."""
    ms = [m for m in models if m and m["base"] > 0]
    if not ms or market_cap_ccy <= 0:
        return None
    to_px = price / market_cap_ccy
    base = statistics.median(m["base"] for m in ms) * to_px
    low = statistics.median(max(m["low"], 0) for m in ms) * to_px
    high = statistics.median(m["high"] for m in ms) * to_px
    low, high = min(low, base), max(high, base)
    bases = [m["base"] for m in ms]
    spread = max(bases) / min(bases)
    if len(ms) >= 2 and spread <= 1.6:
        grade = "A"
    elif len(ms) >= 2 and spread <= 2.5:
        grade = "B"
    else:
        grade = "C"
    return {
        "low": round(low, 2), "fair_price": round(base, 2), "high": round(high, 2),
        "grade": grade, "spread": round(spread, 2), "models_used": len(ms),
        "models": [{"model": m["model"], "fair": round(m["base"] * to_px, 2), "note": m.get("note")}
                   for m in ms],
    }


# ── ตัวประกอบร่าง ───────────────────────────────────────────────────────────────

async def _fx_to_usd(ccy: str) -> float | None:
    if ccy == "USD":
        return 1.0
    try:
        from app.main import provider
        return (await provider.get_quote(f"{ccy}USD=X")).price
    except Exception:  # noqa: BLE001
        return None


async def consensus_fair_value(symbol: str, *, refresh: bool = False) -> dict | None:
    """ประเมินหุ้น 1 ตัว. คืน None ถ้าไม่มีงบ SEC/ราคา."""
    from app import revenue_model
    from app.fundamentals import fetch_yahoo_fundamentals

    symbol = symbol.upper()
    hist = None
    try:  # โมเดล C (ตัวคูณในอดีต) + จำแนก sector + ดึงงบ SEC ใหม่ (refresh) ในคราวเดียว
        hist = await revenue_model.sector_fair_value(symbol, refresh=refresh)
    except Exception:  # noqa: BLE001
        pass
    try:
        facts = await edgar.get_company_facts(symbol, force_refresh=refresh and hist is None)
        snap = await fetch_yahoo_fundamentals(symbol)
    except Exception:  # noqa: BLE001
        return None

    inp = gather_inputs(facts)
    mcap_usd = snap.get("market_cap")
    fx = await _fx_to_usd(inp["unit"])
    price = (hist or {}).get("price")
    if not price:
        try:
            from app.main import provider
            price = (await provider.get_quote(symbol)).price
        except Exception:  # noqa: BLE001
            price = None
    if not mcap_usd or not fx or not price:
        return None
    mcap = mcap_usd / fx  # มูลค่าตลาดในสกุลเงินงบ

    foreign = inp["unit"] != "USD"
    r = DISCOUNT_FOREIGN if foreign else DISCOUNT_US
    cagr = revenue_cagr(inp["revenue"])
    g = max(GROWTH_CAP[0], min(GROWTH_CAP[1], cagr if cagr is not None else 0.03))
    profile = (hist or {}).get("profile_key") or "general"
    warnings: list[str] = []

    models: list[dict | None] = []
    if profile in ("bank", "insurance"):
        # FCF ของธุรกิจการเงินไม่มีความหมาย (เงินฝาก/เบี้ยประกันไหลผ่านกระแสเงินสด) — ใช้ P/B + กำไร
        models.append(model_justified_pb(inp, r))
        models.append(model_earnings_power(inp, min(g, 0.10)))
    elif profile != "reit":  # REIT: FCF/กำไรทางบัญชีถูกค่าเสื่อมบิดเบือน — ใช้ P/FFO ในอดีตอย่างเดียว
        models.append(model_dcf(inp, g, r))
        models.append(model_earnings_power(inp, g))

    hv = (hist or {}).get("fair_value")
    if hv and hv.get("fair_price"):
        level = sector_profile.multiple_sanity(hv.get("basis"), hv.get("median_multiple"))["level"]
        if level != "extreme":
            k = mcap / price  # แปลงราคาต่อหุ้นกลับเป็นมูลค่ารวม
            models.append({"model": f"ตัวคูณในอดีต ({hv['basis'].split(' ')[0]})",
                           "low": hv["low"] * k, "base": hv["fair_price"] * k, "high": hv["high"] * k,
                           "note": f"median {hv['median_multiple']} เท่า"})
        else:
            warnings.append(f"ตัดโมเดลตัวคูณในอดีตออก ({hv['median_multiple']} เท่า สูงผิดปกติ)")

    res = combine([m for m in models if m], mcap, price)
    if res is None:
        return None

    # Reverse DCF — ราคาวันนี้คาดหวังการโตเท่าไหร่
    dcf = next((m for m in models if m and m["model"].startswith("DCF")), None)
    implied = reverse_dcf_growth(dcf["base_fcf"], mcap, r, inp["cash"] - inp["debt"]) if dcf else None

    if inp["data_end"] and (date.today() - date.fromisoformat(inp["data_end"])).days > 200:
        warnings.append(f"งบล่าสุดถึง {inp['data_end']} — ข้อมูลอาจไม่สด")
        if res["grade"] == "A":
            res["grade"] = "B"
    if res["models_used"] == 1 and profile == "reit":
        res["grade"] = "B" if res["grade"] == "C" else res["grade"]

    return {
        "symbol": symbol, "price": round(price, 2), "currency": inp["unit"], "profile": profile,
        **res,
        "upside_pct": round((res["fair_price"] - price) / price * 100, 1),
        "hist_growth_pct": round(cagr * 100, 1) if cagr is not None else None,
        "implied_growth_pct": round(implied * 100, 1) if implied is not None else None,
        "discount_rate_pct": r * 100,
        "data_end": inp["data_end"],
        "warnings": warnings,
    }
