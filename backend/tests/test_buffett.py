"""Deterministic tests for the Buffett scoring engine — synthetic financials, no network."""
from app import buffett as B


def _series(**metrics):
    """Build S = {metric: {fy: value}} from keyword lists (oldest→newest for fys 2020..)."""
    fys = None
    S = {}
    for name, vals in metrics.items():
        fys = fys or list(range(2020, 2020 + len(vals)))
        S[name] = {fy: v for fy, v in zip(range(2020, 2020 + len(vals)), vals) if v is not None}
    return S, list(range(2020, 2020 + len(next(iter(metrics.values())))))


def test_owner_earnings_uses_maintenance_capex():
    S = {"ocf": {2024: 100.0}, "capex": {2024: 30.0}, "da": {2024: 20.0}}
    oe = B.owner_earnings(S, 2024)
    assert oe["maintenance_capex"] == 20.0        # min(capex, D&A)
    assert oe["owner_earnings"] == 80.0           # OCF - maintenance capex
    assert oe["fcf"] == 70.0                       # OCF - capex


def test_owner_earnings_no_da_falls_back_to_capex():
    S = {"ocf": {2024: 100.0}, "capex": {2024: 30.0}, "da": {}}
    oe = B.owner_earnings(S, 2024)
    assert oe["maintenance_capex"] == 30.0
    assert oe["owner_earnings"] == 70.0


def test_reverse_dcf_recovers_growth():
    # ราคาตลาด = มูลค่า DCF ที่ g=10% → reverse DCF ต้องได้ implied_growth ≈ 10%
    mc = B._dcf_value(100.0, 0.10, B._DISCOUNT_RATE)
    rd = B.reverse_dcf(100.0, mc)
    assert abs(rd["implied_growth"] - 0.10) < 0.01


def test_reverse_dcf_none_on_bad_input():
    assert B.reverse_dcf(None, 1000) is None
    assert B.reverse_dcf(-5, 1000) is None
    assert B.reverse_dcf(100, 0) is None


def test_altman_safe_vs_distress():
    strong = {"total_assets": {2024: 100.0}, "current_assets": {2024: 60.0}, "current_liabilities": {2024: 20.0},
              "retained_earnings": {2024: 50.0}, "operating_income": {2024: 25.0},
              "total_liabilities": {2024: 30.0}, "revenue": {2024: 120.0}}
    z = B.altman_z(strong, [2024], market_cap=300.0)
    assert z["zone"] == "ปลอดภัย" and z["z"] > 2.99

    weak = {"total_assets": {2024: 100.0}, "current_assets": {2024: 20.0}, "current_liabilities": {2024: 60.0},
            "retained_earnings": {2024: -30.0}, "operating_income": {2024: -10.0},
            "total_liabilities": {2024: 95.0}, "revenue": {2024: 30.0}}
    z2 = B.altman_z(weak, [2024], market_cap=10.0)
    assert z2["zone"] == "เสี่ยงล้มละลาย" and z2["z"] < 1.81


def test_piotroski_perfect_improvement():
    # ทุกตัวชี้วัดดีขึ้น YoY + งบแข็ง → ควรได้คะแนนสูง (>=7)
    S, fys = _series(
        net_income=[5.0, 12.0], total_assets=[100.0, 100.0], ocf=[8.0, 20.0],
        current_assets=[40.0, 55.0], current_liabilities=[30.0, 25.0],
        gross_profit=[30.0, 45.0], revenue=[80.0, 100.0],
        long_term_debt=[40.0, 30.0], shares=[10.0, 10.0],
    )
    pio = B.piotroski(S, fys)
    assert pio["score"] >= 7 and pio["max"] == 9


def test_moat_wide_when_roic_high_and_persistent():
    fys = list(range(2015, 2025))
    S = {"operating_income": {fy: 30.0 for fy in fys}, "income_tax": {fy: 6.0 for fy in fys},
         "interest_expense": {fy: 1.0 for fy in fys}, "total_equity": {fy: 50.0 for fy in fys},
         "cash": {fy: 10.0 for fy in fys}, "short_term_debt": {fy: 0.0 for fy in fys},
         "long_term_debt": {fy: 40.0 for fy in fys},
         "gross_profit": {fy: 60.0 for fy in fys}, "revenue": {fy: 100.0 for fy in fys}}
    mo = B.moat(S, fys, market_cap=500.0, beta=1.0)
    assert mo["roic_latest"] > 0.15 and mo["years_roic_above_15"] >= 5
    assert mo["width"].startswith("กว้าง")


def test_moat_financial_uses_roe():
    fys = [2022, 2023, 2024]
    S = {"net_income": {fy: 16.0 for fy in fys}, "total_equity": {fy: 100.0 for fy in fys},
         "operating_income": {}, "gross_profit": {}, "revenue": {fy: 200.0 for fy in fys},
         "cash": {}, "short_term_debt": {}, "long_term_debt": {}, "income_tax": {}, "interest_expense": {}}
    mo = B.moat(S, fys, market_cap=500.0, beta=1.0, is_financial=True)
    assert mo["metric"] == "ROE" and abs(mo["roic_latest"] - 0.16) < 1e-6


def test_capital_allocation_flags_dilution():
    fys = list(range(2020, 2025))
    S = {"dividends_paid": {fy: 10.0 for fy in fys}, "buybacks": {fy: 0.0 for fy in fys},
         "capex": {fy: 20.0 for fy in fys}, "net_income": {fy: 30.0 for fy in fys},
         "shares": {2020: 100.0, 2021: 102.0, 2022: 104.0, 2023: 106.0, 2024: 110.0}}
    ca = B.capital_allocation(S, fys)
    assert ca["share_change_pct"] > 0 and ca["shareholder_friendly"] is False
