import unittest

from app import sector_profile as sp


class ClassifyBySicTests(unittest.TestCase):
    def test_bank_sic(self):
        # JPM/BAC = 6021 national commercial banks
        self.assertEqual(sp.classify_by_sic("6021"), "bank")
        self.assertEqual(sp.classify_by_sic(6022), "bank")
        self.assertEqual(sp.classify_by_sic("6712"), "bank")  # bank holding

    def test_insurance_sic(self):
        self.assertEqual(sp.classify_by_sic("6311"), "insurance")  # BRK.B life insurance
        self.assertEqual(sp.classify_by_sic("6411"), "insurance")

    def test_reit_sic(self):
        # O / PLD / AMT all file as 6798 (REIT)
        self.assertEqual(sp.classify_by_sic("6798"), "reit")

    def test_cyclical_sic(self):
        self.assertEqual(sp.classify_by_sic("3312"), "cyclical")  # X US Steel
        self.assertEqual(sp.classify_by_sic("3334"), "cyclical")  # AA Alcoa aluminum
        self.assertEqual(sp.classify_by_sic("1021"), "cyclical")  # FCX copper mining
        self.assertEqual(sp.classify_by_sic("3711"), "cyclical")  # motor vehicles

    def test_capital_intensive_sic(self):
        self.assertEqual(sp.classify_by_sic("4512"), "capital_intensive")  # DAL/UAL airlines
        self.assertEqual(sp.classify_by_sic("4911"), "capital_intensive")  # electric utility
        self.assertEqual(sp.classify_by_sic("1311"), "capital_intensive")  # oil & gas extraction
        self.assertEqual(sp.classify_by_sic("2911"), "capital_intensive")  # petroleum refining

    def test_biotech_sic(self):
        self.assertEqual(sp.classify_by_sic("2836"), "biotech")  # MRNA biological products

    def test_no_match_returns_none(self):
        self.assertIsNone(sp.classify_by_sic("7372"))  # META prepackaged software
        self.assertIsNone(sp.classify_by_sic(None))
        self.assertIsNone(sp.classify_by_sic(""))
        self.assertIsNone(sp.classify_by_sic("abc"))


class ClassifySectorTests(unittest.TestCase):
    def test_bank_trusts_sic_over_financials(self):
        r = sp.classify_sector("6021", {"net_income": -1e9, "ocf": -1e9})
        self.assertEqual(r["sector"], "bank")
        self.assertEqual(r["profile"], "bank")
        self.assertEqual(r["source"], "sic")

    def test_reit_from_sic(self):
        r = sp.classify_sector("6798", {})
        self.assertEqual(r["profile"], "reit")

    def test_cyclical_from_sic(self):
        for code in ("3312", "3334", "1021"):
            r = sp.classify_sector(code, {"revenue": 20e9, "net_income": 2e9, "ocf": 3e9})
            self.assertEqual(r["profile"], "cyclical", code)

    def test_airline_capital_intensive(self):
        r = sp.classify_sector("4512", {"revenue": 50e9, "net_income": 3e9, "ocf": 6e9})
        self.assertEqual(r["profile"], "capital_intensive")

    def test_early_stage_overrides_cyclical_sic(self):
        # RIVN/LCID = auto SIC 3711 but pre-profit → early_stage must win
        r = sp.classify_sector("3711", {"revenue": 4e9, "net_income": -5e9, "ocf": -4e9})
        self.assertEqual(r["sector"], "early_stage")
        self.assertEqual(r["profile"], "early_stage")
        self.assertEqual(r["source"], "financials")

    def test_biotech_from_sic(self):
        r = sp.classify_sector("2836", {"revenue": 6e9, "net_income": -1e9, "ocf": 1e9})
        # revenue large + ocf positive → not early_stage → biotech from SIC
        self.assertEqual(r["profile"], "biotech")

    def test_meta_general_baseline(self):
        r = sp.classify_sector("7372", {"revenue": 130e9, "net_income": 40e9, "ocf": 70e9,
                                        "capex": 28e9})
        self.assertEqual(r["profile"], "general")

    def test_bank_heuristic_without_sic(self):
        r = sp.classify_sector(None, {"revenue": 100e9, "interest_income": 60e9,
                                      "total_assets": 3000e9, "total_equity": 300e9,
                                      "net_income": 30e9, "ocf": 40e9})
        self.assertEqual(r["sector"], "bank")
        self.assertEqual(r["source"], "financials")

    def test_capital_intensive_heuristic(self):
        r = sp.classify_sector(None, {"revenue": 10e9, "capex": 3e9, "net_income": 1e9,
                                      "ocf": 2e9})
        self.assertEqual(r["profile"], "capital_intensive")

    def test_high_capex_but_fat_margin_stays_general(self):
        # META-like: capex/revenue ~0.35 but ~30% net margin → NOT capital_intensive
        r = sp.classify_sector("7370", {"revenue": 200e9, "capex": 70e9, "net_income": 60e9,
                                        "ocf": 115e9})
        self.assertEqual(r["profile"], "general")

    def test_empty_defaults_to_general(self):
        r = sp.classify_sector(None, {})
        self.assertEqual(r["profile"], "general")
        self.assertEqual(r["source"], "default")


class ProfileTests(unittest.TestCase):
    def test_bank_disables_fcf_pfcf(self):
        self.assertTrue(sp.is_disabled("bank", "fcf"))
        self.assertTrue(sp.is_disabled("bank", "pfcf"))
        self.assertFalse(sp.is_disabled("bank", "pb"))

    def test_reit_disables_pe(self):
        self.assertTrue(sp.is_disabled("reit", "pe"))
        self.assertTrue(sp.is_disabled("reit", "pfcf"))

    def test_general_disables_nothing(self):
        self.assertFalse(sp.is_disabled("general", "pe"))
        self.assertFalse(sp.is_disabled("general", "pfcf"))

    def test_unknown_profile_falls_back_general(self):
        self.assertEqual(sp.get_profile("nonexistent"), sp.PROFILES["general"])


class ValidateAnchorTests(unittest.TestCase):
    def test_all_negative_closes_line(self):
        series = [{"value": -5}, {"value": -3}, {"value": -1}]
        r = sp.validate_anchor(series, "FCF")
        self.assertFalse(r["ok"])

    def test_base_shift_when_early_values_negative(self):
        series = [{"value": -5, "date": "2020"}, {"value": -2, "date": "2021"},
                  {"value": 10, "date": "2022"}, {"value": 12, "date": "2023"},
                  {"value": 11, "date": "2024"}]
        r = sp.validate_anchor(series, "EPS")
        self.assertTrue(r["ok"])
        self.assertEqual(r["base_idx"], 2)
        self.assertTrue(any("เลื่อนจุดฐาน" in i for i in r["issues"]))

    def test_clean_series_no_base_shift(self):
        series = [{"value": 10}, {"value": 11}, {"value": 12}, {"value": 13}]
        r = sp.validate_anchor(series, "Revenue")
        self.assertTrue(r["ok"])
        self.assertEqual(r["base_idx"], 0)
        self.assertEqual(r["issues"], [])

    def test_incomplete_quarters_warned(self):
        series = [{"value": 10, "quarters_used": 4}, {"value": 11, "quarters_used": 2},
                  {"value": 12, "quarters_used": 4}]
        r = sp.validate_anchor(series, "TTM FCF")
        self.assertTrue(any("ไม่ครบ 4 ไตรมาส" in i for i in r["issues"]))

    def test_outlier_flagged(self):
        # 55 → 300 is a >400% jump within the drawn range (no base shift skips it)
        series = [{"value": 50}, {"value": 55}, {"value": 300}, {"value": 310}]
        r = sp.validate_anchor(series, "Revenue")
        self.assertTrue(any(">400%" in i for i in r["issues"]))


class MedianBandTests(unittest.TestCase):
    def test_too_few_points(self):
        self.assertIsNone(sp.median_band([10, 12, 11], min_points=8))

    def test_median_and_iqr(self):
        vals = [10, 11, 12, 13, 14, 15, 16, 17]
        band = sp.median_band(vals, min_points=8)
        self.assertIsNotNone(band)
        self.assertLess(band["p25"], band["median"])
        self.assertLess(band["median"], band["p75"])

    def test_outlier_removed_before_median(self):
        vals = [10, 11, 12, 13, 14, 15, 16, 1000]
        band = sp.median_band(vals, min_points=8)
        # 1000 is a wild outlier; median should stay near the cluster
        self.assertLess(band["median"], 20)

    def test_ignores_nonpositive_and_nan(self):
        vals = [10, 11, 12, 13, 14, 15, 16, 17, -5, 0, float("nan")]
        band = sp.median_band(vals, min_points=8)
        self.assertEqual(band["n"], 8)


class FairValueBandTests(unittest.TestCase):
    def test_projects_line_and_band(self):
        prices = [{"close": 100 + i} for i in range(10)]
        anchor = [{"per_share": 10, "date": f"20{20+i}"} for i in range(10)]
        r = sp.fair_value_band(prices, anchor, min_points=8)
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["line"]), 10)
        self.assertTrue(all(lo <= mid <= hi for lo, mid, hi
                            in zip(r["lower"], r["line"], r["upper"])))

    def test_insufficient_points(self):
        prices = [{"close": 100} for _ in range(3)]
        anchor = [{"per_share": 10} for _ in range(3)]
        r = sp.fair_value_band(prices, anchor, min_points=8)
        self.assertFalse(r["ok"])


class PercentileTests(unittest.TestCase):
    def test_median_matches(self):
        self.assertEqual(sp.percentile([1, 2, 3, 4, 5], 50), 3)

    def test_interpolation(self):
        self.assertAlmostEqual(sp.percentile([0, 10], 25), 2.5)


if __name__ == "__main__":
    unittest.main()
