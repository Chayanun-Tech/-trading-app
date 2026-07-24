import unittest

from app import sector_metrics as sm


class SectorMetricsTests(unittest.TestCase):
    def test_book_value_per_share(self):
        self.assertAlmostEqual(sm.book_value_per_share(1000, 100, 10), 90.0)
        self.assertAlmostEqual(sm.book_value_per_share(1000, None, 10), 100.0)
        self.assertIsNone(sm.book_value_per_share(1000, 0, 0))
        self.assertIsNone(sm.book_value_per_share(None, 0, 10))

    def test_ffo(self):
        # NI 100 + depreciation 50 - gains 20 = 130
        self.assertAlmostEqual(sm.ffo(100, 50, 20), 130.0)
        self.assertAlmostEqual(sm.ffo(100, 50), 150.0)
        self.assertIsNone(sm.ffo(100, None))

    def test_affo(self):
        self.assertAlmostEqual(sm.affo(130, 30, 5), 95.0)
        self.assertIsNone(sm.affo(None, 30))

    def test_ebitda(self):
        self.assertAlmostEqual(sm.ebitda(200, 60), 260.0)
        self.assertIsNone(sm.ebitda(200, None))

    def test_roe(self):
        self.assertAlmostEqual(sm.roe(30, 300), 0.1)
        self.assertIsNone(sm.roe(30, 0))

    def test_normalized(self):
        self.assertAlmostEqual(sm.normalized([1, 2, 3, 4, 5], 3), 4.0)  # mean(3,4,5)
        self.assertAlmostEqual(sm.normalized([2, 4], 10), 3.0)          # fewer than n
        self.assertIsNone(sm.normalized([], 5))

    def test_cash_runway(self):
        # cash 1200, burning 600/yr → 50/mo → 24 months
        self.assertAlmostEqual(sm.cash_runway_months(1200, 600), 24.0)
        self.assertIsNone(sm.cash_runway_months(1200, 0))     # not burning
        self.assertIsNone(sm.cash_runway_months(1200, -600))  # cash-flow positive

    def test_rule_of_40(self):
        self.assertAlmostEqual(sm.rule_of_40(30, 15), 45.0)
        self.assertIsNone(sm.rule_of_40(30, None))

    def test_net_margin(self):
        self.assertAlmostEqual(sm.net_margin(30, 300), 0.1)
        self.assertIsNone(sm.net_margin(30, 0))


if __name__ == "__main__":
    unittest.main()
