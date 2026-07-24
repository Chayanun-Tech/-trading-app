import asyncio
import time
import unittest
from unittest import mock

from app import value_scanner


def _row(symbol, upside, profile="general"):
    return {
        "symbol": symbol, "name": f"{symbol} Inc", "gics_sector": "Technology",
        "profile_key": profile, "profile_label": "ทั่วไป", "sector_source": "sic",
        "basis": "P/E median", "per_share": 5.0, "median_multiple": 20.0,
        "fair_price": 100.0, "low": 90.0, "high": 110.0,
        "price": round(100.0 / (1 + upside / 100), 2), "upside_pct": upside, "warnings": [],
    }


CACHED = {
    "as_of": time.time(), "universe_count": 500, "success_count": 4,
    "results": [_row("AAA", 45.0), _row("BBB", 12.0), _row("CCC", 30.0, "bank"),
                _row("DDD", -8.0)],
}

CAPS = {"AAA": 300e9, "BBB": 50e9, "CCC": 5e9}  # DDD ไม่มี market cap ในตาราง


def _format(**kwargs):
    args = {"min_upside_pct": 20.0, "limit": 40, "min_market_cap": None,
            "max_market_cap": None, "profile": None}
    args.update(kwargs)
    with mock.patch.object(value_scanner, "_market_cap_lookup",
                           mock.AsyncMock(return_value=CAPS)):
        return asyncio.run(value_scanner._format_result(CACHED, **args))


class ValueScannerFormatTests(unittest.TestCase):
    def test_filters_by_min_upside_and_sorts_by_upside_desc(self):
        out = _format()
        self.assertEqual([c["symbol"] for c in out["candidates"]], ["AAA", "CCC"])
        self.assertEqual(out["candidate_count"], 2)

    def test_market_cap_range_filters_out_symbols_without_a_known_cap(self):
        out = _format(min_upside_pct=-100.0, min_market_cap=1e9)
        self.assertEqual([c["symbol"] for c in out["candidates"]], ["AAA", "CCC", "BBB"])

    def test_max_market_cap_keeps_only_smaller_companies(self):
        out = _format(max_market_cap=10e9)
        self.assertEqual([c["symbol"] for c in out["candidates"]], ["CCC"])

    def test_profile_filter_restricts_to_one_sector_profile(self):
        out = _format(profile="bank")
        self.assertEqual([c["symbol"] for c in out["candidates"]], ["CCC"])

    def test_limit_truncates_candidates_but_count_keeps_total(self):
        out = _format(limit=1)
        self.assertEqual(len(out["candidates"]), 1)
        self.assertEqual(out["candidate_count"], 2)

    def test_profile_options_come_from_sector_profiles(self):
        keys = [p["key"] for p in value_scanner.profile_options()]
        self.assertIn("bank", keys)
        self.assertIn("general", keys)


if __name__ == "__main__":
    unittest.main()
