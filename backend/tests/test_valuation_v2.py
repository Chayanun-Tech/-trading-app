"""ทดสอบตัวจำแนกประเภทและวิธีประเมินของ valuation_v2 ด้วยข้อมูลสมมติ (ไม่ต่อเน็ต)."""
from app import valuation_v2 as v


def _m(**kw):
    base = {"years": 10, "nm": [0.2] * 10, "nm_med5": 0.2, "nm_med10": 0.2, "ttm_nm": 0.2, "ttm_fcf": 0.2, "cv": 0.1,
            "loss_years": 0, "cagr5": 0.07, "cagr10": 0.07, "roe": 0.25, "retention": 0.5, "net_debt": 0.0,
            "norm_fcf": 100.0, "fcf_hist": [0.2] * 10, "fcf_med5": 0.2, "fcf_med10": 0.2, "own_med5": 0.22,
            "own_med10": 0.22, "ttm_own": 0.22, "recent_loss": False, "rev_avg5": 1000.0, "rev_avg7": 1000.0}
    base.update(kw)
    return base


def test_classify_basic_types():
    assert v.classify(_m(), "general", "2080") == "compounder"
    assert v.classify(_m(), "bank", "6021") == "financial"
    assert v.classify(_m(), "general", "6798") == "reit"
    assert v.classify(_m(), "general", "1311") == "commodity"
    assert v.classify(_m(cagr5=0.30), "general", "3674") == "fast"
    assert v.classify(_m(cv=0.9), "general", "3312") == "cyclical"
    assert v.classify(_m(cv=0.4, roe=0.05, fcf_med5=0.05), "general", "5000") == "mature"
    assert v.classify(_m(cv=0.5), "general", "5000") == "cyclical"   # ขอบเขต CV ≥ 0.5 = วัฏจักร


def test_recently_profitable_grower_is_fast_not_cyclical():
    # เคยขาดทุนช่วงต้น (margin แกว่งในสถิติ) แต่ 3 ปีล่าสุดกำไรและโตแรง → fast
    m = _m(cagr5=0.35, cv=2.5, loss_years=3, recent_loss=False, nm_med10=-0.05, nm_med5=0.09, ttm_nm=0.3)
    assert v.classify(m, "general", "7372") == "fast"


def test_unprofitable_and_turnaround():
    assert v.classify(_m(ttm_nm=-0.05, nm_med5=-0.1, nm_med10=-0.1, cagr5=0.4), "general", "7372") == "unprofitable"
    assert v.classify(_m(ttm_nm=0.01, nm_med10=0.2), "general", "3674") == "turnaround"


def test_discount_rate_rises_with_risk_and_is_capped():
    safe, _ = v.discount_rate(_m(cv=0.1, net_debt=0.0, norm_fcf=100.0), "compounder", False)
    risky, _ = v.discount_rate(_m(cv=1.0, net_debt=500.0, norm_fcf=100.0), "unprofitable", True)
    assert safe == 0.07
    assert risky > safe and risky <= 0.13


def test_dcf_path_monotonic_in_growth_and_margin():
    a = v.dcf_path(1000, 0.05, 0.2, 0.2, 0.08, 0)
    assert v.dcf_path(1000, 0.10, 0.2, 0.2, 0.08, 0) > a
    assert v.dcf_path(1000, 0.05, 0.25, 0.25, 0.08, 0) > a
    assert v.dcf_path(1000, 0.05, 0.2, 0.2, 0.10, 0) < a


def test_cyclical_uses_normalized_revenue_not_peak():
    inp = {"revenue_ttm": 2000.0, "cash": 0.0, "debt": 0.0}
    m = _m(fcf_hist=[0.05, 0.3, -0.1, 0.1, 0.25, 0.0, 0.2], rev_avg5=1000.0, cagr10=0.03)
    out = v.value_by_archetype("cyclical", inp, m, 0.085, "3674")
    peak = v.dcf_path(2000.0, 0.03, 0.1, 0.1, 0.085, 0)
    assert out["base"] < peak                       # ใช้รายได้ปกติ (≤ 1.15 × ค่าเฉลี่ย) ไม่ใช่รายได้ยอดรอบ
    assert out["bear"] <= out["base"] <= out["bull"]


def test_confidence_levels():
    hi, lvl = v.confidence(100, 150, _m(cv=0.1), "compounder", None)
    lo, lvl2 = v.confidence(10, 200, _m(cv=1.0), "unprofitable", None)
    assert hi > lo and lvl in ("สูง", "กลาง") and lvl2 == "ต่ำ"
