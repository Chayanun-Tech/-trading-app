"""กันบั๊ก 2 ตัวที่เจอตอนสแกน S&P 500 + Nasdaq-100 (2026-11-17):
1. EPS ปน 2 ฐาน split (NFLX split 10:1) → P/E median เพี้ยน
2. บริษัทที่ SIC ไม่ใช่อสังหาฯ ถูกเดาเป็น REIT จากงบ (AMZN/MU/T/CHTR) → ใช้ P/FFO ผิดกลุ่ม
"""
from app import revenue_model as m
from app import sector_profile as sp


def test_mixed_split_basis_eps_is_normalized():
    ref_shares = 4_340_000_000  # หลัง split 10:1
    eps = {
        "2025-03-31": {"val": 0.68},  # ปรับย้อนหลังแล้ว (ฐานหลัง split)
        "2025-06-30": {"val": 7.35},  # ยังเป็นฐานก่อน split
    }
    ni = {"2025-03-31": {"val": 2.95e9}, "2025-06-30": {"val": 3.19e9}}
    out = m._normalize_eps_points(eps, ni, ref_shares)
    assert out["2025-03-31"]["val"] == 0.68
    assert abs(out["2025-06-30"]["val"] - 0.735) < 1e-9


def test_normal_buyback_drift_is_not_treated_as_split():
    eps = {"q": {"val": 2.0}}
    ni = {"q": {"val": 2.0 * 1.05e9}}  # หุ้นต่างจากฐาน 5% (ซื้อคืน) ไม่ใช่ split
    assert m._normalize_eps_points(eps, ni, 1.0e9)["q"]["val"] == 2.0


def test_tiny_or_sign_mismatched_eps_left_alone():
    ni = {"a": {"val": 1e8}, "b": {"val": -5e8}}
    eps = {"a": {"val": 0.01}, "b": {"val": 0.5}}
    out = m._normalize_eps_points(eps, ni, 1e9)
    assert out["a"]["val"] == 0.01 and out["b"]["val"] == 0.5


_CAPEX_HEAVY = {  # ค่าเสื่อมสูงเทียบกำไร + capex > 15% ของรายได้ (หน้าตาแบบ AMZN ช่วงทุ่ม AI)
    "revenue": 700e9, "net_income": 70e9, "ocf": 130e9, "capex": -120e9,
    "total_assets": 700e9, "total_equity": 300e9, "depreciation": 60e9,
}


def test_known_non_realestate_sic_never_becomes_reit():
    r = sp.classify_sector("5961", _CAPEX_HEAVY)  # 5961 = ค้าปลีกออนไลน์ (AMZN)
    assert r["profile"] != "reit"


def test_missing_sic_can_still_infer_reit():
    assert sp.classify_sector(None, _CAPEX_HEAVY)["profile"] == "reit"


def test_reit_sic_still_reit():
    assert sp.classify_sector("6798", {})["profile"] == "reit"
