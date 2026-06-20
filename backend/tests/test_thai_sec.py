import unittest

from app import thai_sec


SAMPLE_HTML = """
<table id="gPP06T05">
  <tr><th>Name</th><th>Year</th><th>Received date</th><th>Detail</th></tr>
  <tr>
    <td>PTT PUBLIC COMPANY LIMITED</td><td>2025</td><td>12/03/2026</td>
    <td><a href="/public/idisc/Download?FILEID=ptt.zip">view</a></td>
  </tr>
  <tr>
    <td>PTT PUBLIC COMPANY LIMITED</td>
    <td>2024 (Change of business/shareholders)</td><td>10/03/2025</td>
    <td><a href="https://market.sec.or.th/file/ptt-2024.pdf">view</a></td>
  </tr>
  <tr>
    <td>PTT OIL AND RETAIL BUSINESS PUBLIC COMPANY LIMITED</td>
    <td>2025</td><td>15/03/2026</td>
    <td><a href="/public/idisc/Download?FILEID=or.zip">view</a></td>
  </tr>
</table>
"""


class ThaiSecTests(unittest.TestCase):
    def test_parse_reports_html(self):
        rows = thai_sec.parse_reports_html(SAMPLE_HTML)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["company_name"], "PTT PUBLIC COMPANY LIMITED")
        self.assertTrue(rows[0]["url"].startswith("https://market.sec.or.th/"))

    def test_company_name_matching_does_not_mix_ptt_and_or(self):
        ptt = thai_sec._company_match_score(
            "PTT Public Company Limited", "PTT PUBLIC COMPANY LIMITED",
        )
        or_score = thai_sec._company_match_score(
            "PTT Public Company Limited",
            "PTT OIL AND RETAIL BUSINESS PUBLIC COMPANY LIMITED",
        )
        self.assertEqual(ptt, 1.0)
        self.assertLess(or_score, 0.82)

    def test_is_thai_symbol(self):
        self.assertTrue(thai_sec.is_thai_symbol("KBANK.BK"))
        self.assertTrue(thai_sec.is_thai_symbol("ptt.bk"))
        self.assertFalse(thai_sec.is_thai_symbol("AAPL"))


if __name__ == "__main__":
    unittest.main()
