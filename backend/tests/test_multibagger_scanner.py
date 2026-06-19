import unittest

from app.multibagger_scanner import prefilter_universe, score_candidate


class MultibaggerScannerTests(unittest.TestCase):
    def test_prefilter_excludes_warrants_and_illiquid_rows(self):
        rows = [
            {"symbol": "GOOD", "name": "Good Co Common Stock", "lastsale": "$10",
             "volume": "500000", "marketCap": "500000000", "country": "United States",
             "sector": "Technology", "industry": "Software"},
            {"symbol": "BADW", "name": "Bad Co Warrant", "lastsale": "$2",
             "volume": "500000", "marketCap": "100000000", "country": "United States",
             "sector": "Technology", "industry": "Software"},
            {"symbol": "THIN", "name": "Thin Co Common Stock", "lastsale": "$2",
             "volume": "1000", "marketCap": "100000000", "country": "United States",
             "sector": "Technology", "industry": "Software"},
        ]
        result = prefilter_universe(
            rows, min_market_cap=50_000_000, max_market_cap=1_000_000_000,
            min_price=1, min_dollar_volume=1_000_000,
        )
        self.assertEqual([row["symbol"] for row in result], ["GOOD"])

    def test_quality_growth_company_scores_above_weak_company(self):
        base = {
            "symbol": "TEST", "name": "Test", "market_cap": 500_000_000,
            "dollar_volume": 5_000_000, "sector": "Technology", "industry": "Software",
        }
        strong = score_candidate(base, {
            "revenue_growth": 0.30, "earnings_growth": 0.35, "gross_margin": 0.70,
            "operating_margin": 0.20, "profit_margin": 0.15, "roe": 0.25,
            "debt_to_equity": 0.2, "current_ratio": 2.2, "fcf": 10_000_000,
            "fcf_yield": 0.05, "pe": 24, "peg": 1.1,
        })
        weak = score_candidate(base, {
            "revenue_growth": -0.10, "earnings_growth": -0.20, "gross_margin": 0.15,
            "operating_margin": -0.10, "profit_margin": -0.15, "roe": -0.10,
            "debt_to_equity": 3.0, "current_ratio": 0.7, "fcf": -10_000_000,
            "fcf_yield": -0.05, "pe": None, "peg": None,
        })
        self.assertGreater(strong["score"], weak["score"])
        self.assertTrue(weak["red_flags"])


if __name__ == "__main__":
    unittest.main()
