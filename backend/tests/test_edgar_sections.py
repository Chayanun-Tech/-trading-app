import unittest

from app import edgar


class EdgarSectionTests(unittest.TestCase):
    def test_extracts_sections_with_unicode_dash_headings(self):
        business_body = " ".join(["warehouse membership retail operations"] * 100)
        risk_body = " ".join(["competition and operating risk"] * 100)
        mda_body = " ".join(["revenue gross margin and results"] * 100)
        text = (
            "TABLE OF CONTENTS Item 1. Business 3 Item 1A. Risk Factors 9 "
            "Item 1B. Unresolved Staff Comments 18 Item 2. Properties 20 "
            "Item 3. Legal Proceedings 20 Item 4. Mine Safety 20 "
            "PART I "
            f"Item 1—Business {business_body} "
            f"Item 1A—Risk Factors {risk_body} "
            "Item 1B—Unresolved Staff Comments "
            "Item 7—Management's Discussion and Analysis of Financial Condition "
            f"and Results of Operations {mda_body} "
            "Item 8—Financial Statements"
        )

        sections = edgar.extract_filing_sections(text)

        self.assertIn("warehouse membership", sections["business"])
        self.assertIn("competition and operating risk", sections["risk_factors"])
        self.assertIn("revenue gross margin", sections["mda"])

    def test_does_not_use_quoted_cross_references_as_headings(self):
        business_body = " ".join(["actual business description"] * 100)
        text = (
            "See “Item 1—Business” of this report for background. "
            f"Item 1—Business {business_body} "
            "Item 1A—Risk Factors"
        )

        section = edgar.extract_filing_sections(text)["business"]

        self.assertTrue(section.startswith("Item 1—Business actual"))

    def test_extracts_headings_split_by_inline_xbrl_formatting(self):
        business_body = " ".join(["diverse operating subsidiaries"] * 100)
        risk_body = " ".join(["material business risks"] * 100)
        text = (
            f"ITEM 1. Busines s Description {business_body} "
            f"ITEM 1A. Ris k Factors {risk_body} "
            "ITEM 1B. Unresolved Staff Comments"
        )

        sections = edgar.extract_filing_sections(text)

        self.assertIn("diverse operating subsidiaries", sections["business"])
        self.assertIn("material business risks", sections["risk_factors"])

    def test_extracts_form_20f_sections(self):
        business_body = " ".join(["semiconductor manufacturing services"] * 100)
        risk_body = " ".join(["foreign issuer risk disclosure"] * 100)
        mda_body = " ".join(["operating results and cash flow"] * 100)
        text = (
            f"ITEM 3. KEY INFORMATION {risk_body} "
            f"ITEM 4. INFORMATION ON THE COMPANY {business_body} "
            "ITEM 4A. UNRESOLVED STAFF COMMENTS "
            f"ITEM 5. OPERATING AND FINANCIAL REVIEW AND PROSPECTS {mda_body} "
            "ITEM 6. DIRECTORS, SENIOR MANAGEMENT AND EMPLOYEES"
        )

        sections = edgar.extract_20f_sections(text)

        self.assertIn("semiconductor manufacturing", sections["business"])
        self.assertIn("foreign issuer risk", sections["risk_factors"])
        self.assertIn("operating results and cash flow", sections["mda"])

    def test_extracts_combined_items_one_and_two_business_and_properties(self):
        business_body = " ".join(["oil and gas operating properties"] * 100)
        text = (
            f"ITEMS 1 AND 2. BUSINESS AND PROPERTIES {business_body} "
            "ITEM 1A. RISK FACTORS "
            + " ".join(["commodity price and operating risks"] * 100)
            + " ITEM 1B. UNRESOLVED STAFF COMMENTS"
        )

        sections = edgar.extract_filing_sections(text)

        self.assertIn("oil and gas operating properties", sections["business"])


if __name__ == "__main__":
    unittest.main()
