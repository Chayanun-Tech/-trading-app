from app import consensus_value as cv


def _inp(**kw):
    base = {
        "revenue": {2021: 100.0, 2022: 110.0, 2023: 121.0, 2024: 133.1, 2025: 146.41},
        "ocf": {y: v * 0.30 for y, v in {2021: 100.0, 2022: 110.0, 2023: 121.0, 2024: 133.1, 2025: 146.41}.items()},
        "capex": {y: 5.0 for y in range(2021, 2026)},
        "sbc": {y: 3.0 for y in range(2021, 2026)},
        "net_income": {y: v * 0.20 for y, v in {2021: 100.0, 2022: 110.0, 2023: 121.0, 2024: 133.1, 2025: 146.41}.items()},
        "equity_hist": {}, "revenue_ttm": 150.0, "ocf_ttm": 45.0, "capex_ttm": 5.0, "sbc_ttm": 3.0,
        "ni_ttm": 30.0, "cash": 20.0, "debt": 10.0, "equity": 100.0,
    }
    base.update(kw)
    return base


def test_revenue_cagr():
    assert abs(cv.revenue_cagr(_inp()["revenue"]) - 0.10) < 1e-9
    assert cv.revenue_cagr({2024: 1.0, 2025: 2.0}) is None  # ข้อมูลน้อยเกินไป


def test_owner_fcf_subtracts_capex_and_sbc():
    m = cv.owner_fcf_margins(_inp())
    assert abs(m[-1] - (45 - 5 - 3) / 150) < 1e-9


def test_reverse_dcf_recovers_growth():
    eq = cv.dcf_equity(10.0, 0.12, 0.085, 5.0)
    g = cv.reverse_dcf_growth(10.0, eq, 0.085, 5.0)
    assert abs(g - 0.12) < 1e-6


def test_dcf_skipped_when_business_burns_cash():
    inp = _inp(ocf={y: 1.0 for y in range(2021, 2026)}, ocf_ttm=1.0)
    assert cv.model_dcf(inp, 0.1, 0.085) is None


def test_combine_grades_by_agreement():
    agree = [{"model": "a", "low": 90, "base": 100, "high": 110},
             {"model": "b", "low": 100, "base": 120, "high": 140}]
    r = cv.combine(agree, market_cap_ccy=100.0, price=50.0)
    assert r["grade"] == "A" and r["fair_price"] == 55.0  # median(100,120)=110 → 50×1.1
    far = [{"model": "a", "low": 90, "base": 100, "high": 110},
           {"model": "b", "low": 280, "base": 300, "high": 320}]
    assert cv.combine(far, 100.0, 50.0)["grade"] == "C"
    assert cv.combine(agree[:1], 100.0, 50.0)["grade"] == "C"  # โมเดลเดียว เชื่อไม่ได้


def test_pe_is_capped_for_hyper_growth():
    r = cv.model_earnings_power(_inp(), g=0.60)
    assert r["base"] <= 150 * 0.20 * cv.PE_CAP + 1e-6


def test_cagr_handles_missing_years():
    rev = {2018: 50.0, 2019: 55.0, 2022: 80.0, 2023: 90.0, 2025: 100.0}  # ไม่มีปี 2020
    g = cv.revenue_cagr(rev)
    assert g is not None and abs(g - (100 / 80) ** (1 / 3) + 1) < 1e-9
