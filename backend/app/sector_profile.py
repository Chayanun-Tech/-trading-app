"""Sector-Aware Valuation Engine — ชั้น classifier + profile + validation gate + median band.

ปัญหาเดิม: โมเดล valuation ตัวเดียว (P/E, FCF, P/FCF) ใช้กับทุกหุ้นเหมือนกันหมด แต่ metric
พวกนี้ "ไม่มีความหมาย" หรือ "ตีความกลับด้าน" กับบางอุตสาหกรรม:
  • ธนาคาร — FCF/EV ไม่มีความหมาย (หนี้คือวัตถุดิบ ไม่ใช่ภาระ), ต้องดู P/B + ROE
  • REIT — P/E ใช้ไม่ได้ (ค่าเสื่อมทางบัญชีกด EPS ต่ำเกินจริง), ต้องดู FFO/AFFO
  • วัฏจักร (เหล็ก/เหมือง) — P/E ต่ำที่ยอดวัฏจักร = อันตราย ไม่ใช่ถูก
  • เติบโต/ยังไม่กำไร — multiple ดั้งเดิมใช้ไม่ได้ ต้องดู cash runway

โมดูลนี้เพิ่ม "ชั้น" ก่อนคำนวณ:
  [2] classify_sector  — เดา sector จาก SIC ของ SEC (แม่นสุด) → fallback heuristic จากงบ
  [3] PROFILES         — แต่ละ sector บอกว่าใช้ metric ไหน ปิดไหน เตือนอะไร
  [4] validate_anchor  — gate กันเส้น "ระเบิด" (base ติดลบ/ค่าติดลบระหว่างทาง/outlier)
  [5] median_band      — เส้นมูลค่าจาก median(P/X) ทั้งช่วง + band IQR แทนเส้นเดียวที่ขึ้นกับวันเดียว

ฟังก์ชันทั้งหมดเป็น pure (ไม่ยิงเน็ต/ไม่แตะ I/O) เพื่อทดสอบง่ายและเรียกซ้ำได้.
"""
from __future__ import annotations

import re
from statistics import median as _median

# ── ชั้น 2: Sector Classifier ────────────────────────────────────────────────
# SIC (Standard Industrial Classification) code จาก SEC แม่นกว่า sector ของ Yahoo มาก
# เพราะเป็นรหัสที่บริษัทยื่นเองกับ ก.ล.ต. สหรัฐ ไม่ใช่การจัดกลุ่มแบบ marketing.
# แต่ละกลุ่มจับคู่กับ "profile key" (ดูชั้น 3) — หลาย SIC map เข้า profile เดียวกันได้.
_SIC_PATTERNS: list[tuple[str, list[str]]] = [
    # โครงสร้างงบพิเศษ — เชื่อ SIC เสมอ ไม่ให้ heuristic ทับ
    ("bank",       [r"^60\d{2}$", r"^6712$"]),                 # 6000-6099 ธนาคาร + holding
    ("insurance",  [r"^63\d{2}$", r"^64\d{2}$"]),              # 6300-6499 ประกัน
    ("reit",       [r"^6798$"]),                               # REIT
    ("realestate", [r"^65\d{2}$"]),                            # อสังหาฯ operating
    # วัฏจักร (deep cyclical) — เหล็ก/โลหะ/เหมืองโลหะ/รถยนต์
    ("cyclical",   [r"^33\d{2}$", r"^10\d{2}$", r"^12\d{2}$", r"^371[0-9]$"]),
    # ลงทุนหนัก — สายการบิน/สาธารณูปโภค/พลังงาน (น้ำมัน-ก๊าซ)
    ("capital_intensive", [r"^4512$", r"^49\d{2}$", r"^13\d{2}$", r"^29\d{2}$", r"^45[1-2]\d$"]),
    # ยา/ไบโอเทค
    ("biotech",    [r"^2836$", r"^8731$", r"^283[0-6]$"]),
]

# fine sector → profile key (ที่มีจริงใน PROFILES)
_SECTOR_TO_PROFILE = {
    "bank": "bank",
    "insurance": "insurance",
    "reit": "reit",
    "realestate": "reit",          # อสังหาฯ operating ใช้ profile REIT (FFO/P-B) เช่นกัน
    "cyclical": "cyclical",
    "capital_intensive": "capital_intensive",
    "biotech": "biotech",
    "early_stage": "early_stage",
    "general": "general",
}


def classify_by_sic(sic: str | int | None) -> str | None:
    """จับคู่ SIC code → fine sector key. คืน None ถ้าไม่ตรง pattern ไหน (ให้ไป heuristic ต่อ)."""
    if sic is None:
        return None
    s = str(sic).strip()
    if not s or not s.isdigit():
        return None
    s = s.zfill(4)
    for sector, patterns in _SIC_PATTERNS:
        if any(re.match(p, s) for p in patterns):
            return sector
    return None


def infer_from_financials(f: dict) -> str:
    """เดา sector จากโครงสร้างงบเมื่อ SIC ไม่ช่วย (fallback). ทุก field เป็น optional —
    ถ้าไม่มีข้อมูลก็ตกลง 'general' อย่างปลอดภัย ไม่ระเบิด.

    f: {revenue, net_income, ocf, capex, total_assets, total_equity, depreciation, interest_income}
    (ค่าล่าสุด TTM หรือรายปี — หน่วยดอลลาร์)
    """
    def num(k):
        v = f.get(k)
        return float(v) if isinstance(v, (int, float)) else None

    revenue = num("revenue")
    net_income = num("net_income")
    ocf = num("ocf")
    capex = num("capex")
    total_assets = num("total_assets")
    total_equity = num("total_equity")
    depreciation = num("depreciation")
    interest_income = num("interest_income")

    # ธนาคาร: รายได้ดอกเบี้ยเป็นสัดส่วนใหญ่ + leverage สูงมาก (สินทรัพย์/ทุน > 6 เท่า)
    if (interest_income and revenue and revenue > 0 and interest_income / revenue > 0.4
            and total_assets and total_equity and total_equity > 0
            and total_assets / total_equity > 6):
        return "bank"

    # REIT: ค่าเสื่อมมหาศาลเทียบกำไร + capex สูงเทียบรายได้
    if (depreciation and net_income and net_income != 0
            and depreciation / abs(net_income) > 0.8
            and capex and revenue and revenue > 0 and abs(capex) / revenue > 0.15):
        return "reit"

    # เติบโต/ยังไม่กำไร: รายได้เล็กมาก หรือ ทั้งกำไรและกระแสเงินสดดำเนินงานติดลบ
    # (จำกัดไม่ให้บริษัทใหญ่ที่ขาดทุนปีเดียวหลุดมาเป็น early_stage)
    if revenue is not None and revenue < 50e6:
        return "early_stage"
    if (net_income is not None and net_income < 0 and ocf is not None and ocf < 0
            and (revenue is None or revenue < 10e9)):
        return "early_stage"

    # ลงทุนหนัก: capex เกิน 25% ของรายได้ — แต่ต้องกำไรบาง (net margin < 12%) ด้วย
    # เพื่อแยก "ธุรกิจลงทุนหนักกำไรบาง" จริง (สาธารณูปโภค/สายการบิน/ท่อ) ออกจากบริษัทเทค/ซอฟต์แวร์
    # ที่ลงทุน datacenter หนักแต่กำไรอ้วน (META/GOOGL) ซึ่ง P/E, FCF ยังใช้ได้ปกติ ไม่ควรถูกจับผิดกลุ่ม
    if (capex and revenue and revenue > 0 and abs(capex) / revenue > 0.25
            and (net_income is None or net_income / revenue < 0.12)):
        return "capital_intensive"

    return "general"


def classify_sector(sic: str | int | None, financials: dict | None = None) -> dict:
    """ชั้น 2 เต็มรูปแบบ: SIC ก่อน → heuristic เสริม. คืน {sector, profile, source}.

    ลำดับความสำคัญ:
      1. โครงสร้างงบพิเศษ (bank/insurance/reit/realestate) จาก SIC — เชื่อเสมอ
      2. early_stage จากงบ (กระแสเงินสด/กำไรติดลบ) — สำคัญกว่า SIC วัฏจักร/ลงทุนหนัก
         เพราะบริษัทยังไม่กำไร ใช้ multiple แบบวัฏจักรไม่ได้ ไม่ว่า SIC จะบอกว่าอะไร
      3. SIC ที่เหลือ (cyclical/capital_intensive/biotech)
      4. heuristic จากงบ (bank-like/reit-like/capital_intensive/early_stage)
      5. general
    """
    fin = financials or {}
    sic_sector = classify_by_sic(sic)

    if sic_sector in ("bank", "insurance", "reit", "realestate"):
        return {"sector": sic_sector, "profile": _SECTOR_TO_PROFILE[sic_sector], "source": "sic"}

    early = infer_from_financials(fin) == "early_stage"
    if early:
        return {"sector": "early_stage", "profile": "early_stage", "source": "financials"}

    if sic_sector:
        return {"sector": sic_sector, "profile": _SECTOR_TO_PROFILE[sic_sector], "source": "sic"}

    inferred = infer_from_financials(fin)
    return {"sector": inferred, "profile": _SECTOR_TO_PROFILE.get(inferred, "general"),
            "source": "financials" if inferred != "general" else "default"}


# ── ชั้น 3: Sector Profiles ──────────────────────────────────────────────────
# แต่ละ profile กำหนด: metric ที่ใช้ได้ (anchors/multiples), metric ที่ต้อง "ปิด" (disabled),
# metric หลัก (primary), การ normalize (เฉลี่ยกี่ปี), และคำเตือนประจำ sector.
PROFILES: dict[str, dict] = {
    "general": {
        "label": "ทั่วไป",
        "anchors": ["revenue", "fcf", "eps"],
        "multiples": ["pe", "pfcf", "ps"],
        "disabled": [],
        "primary": "fcf",
        "warnings": [],
    },
    "bank": {
        "label": "ธนาคาร / สถาบันการเงิน",
        "anchors": ["book_value", "eps"],
        "multiples": ["pb", "pe"],
        "disabled": ["fcf", "pfcf", "ps", "ev_ebitda"],
        "primary": "book_value",
        "extra_metrics": ["roe", "nim", "tier1", "npl"],
        "warnings": [
            "FCF และ EV/EBITDA ไม่มีความหมายกับธนาคาร — capex ไม่ใช่ต้นทุนหลัก และหนี้คือวัตถุดิบ ไม่ใช่ภาระ",
            "ควรดู P/B ควบคู่ ROE: P/B ที่เหมาะสม ≈ (ROE − g) / (r − g)",
        ],
    },
    "insurance": {
        "label": "ประกัน",
        "anchors": ["book_value", "eps"],
        "multiples": ["pb", "pe"],
        "disabled": ["fcf", "pfcf"],
        "primary": "book_value",
        "extra_metrics": ["combined_ratio", "roe"],
        "warnings": [
            "กำไรผันผวนตามเหตุการณ์ภัยพิบัติ/การลงทุน (mark-to-market) — ใช้กำไรเฉลี่ย 5 ปีดีกว่า TTM",
        ],
    },
    "reit": {
        "label": "REIT / อสังหาริมทรัพย์",
        "anchors": ["ffo", "affo"],
        "multiples": ["p_ffo", "p_affo", "pb"],
        "disabled": ["pe", "pfcf"],
        "primary": "ffo",
        "extra_metrics": ["nav_per_share", "occupancy", "debt_to_ebitda"],
        "warnings": [
            "P/E ใช้ไม่ได้ — ค่าเสื่อมทางบัญชีทำให้ EPS ต่ำเกินจริงมาก",
            "FFO = กำไรสุทธิ + ค่าเสื่อม − กำไรจากการขายทรัพย์สิน",
        ],
    },
    "capital_intensive": {
        "label": "ธุรกิจลงทุนหนัก (สายการบิน/สาธารณูปโภค/พลังงาน)",
        "anchors": ["ebitda", "normalized_fcf", "revenue"],
        "multiples": ["ev_ebitda", "pe", "p_normalized_fcf"],
        "disabled": [],
        "primary": "ebitda",
        "normalize_fcf": 5,       # ใช้ FCF เฉลี่ย 5 ปีแทน TTM
        "warnings": [
            "FCF ผันผวนตามรอบ capex — ควรดู FCF เฉลี่ยหลายปีแทน TTM ปีเดียว",
            "EV/EBITDA เหมาะกว่า P/E เพราะโครงสร้างหนี้ต่างกันมากในกลุ่มนี้",
        ],
    },
    "cyclical": {
        "label": "วัฏจักร (เหล็ก/โลหะ/เหมือง/รถยนต์)",
        "anchors": ["normalized_eps", "revenue"],
        "multiples": ["pe", "shiller_pe", "ps"],
        "disabled": [],
        "primary": "normalized_eps",
        "normalize_earnings": 10,
        "warnings": [
            "⚠️ ตีความกลับด้าน: P/E ต่ำที่ยอดวัฏจักร = อันตราย ไม่ใช่ถูก (กำไรกำลังจะหด)",
            "ควรใช้ EPS เฉลี่ยหลายปี (Shiller) แทน TTM เพื่อเห็นกำลังทำกำไรกลางวัฏจักร",
        ],
    },
    "early_stage": {
        "label": "เติบโต / ยังไม่ทำกำไร",
        "anchors": ["revenue", "gross_profit"],
        "multiples": ["ps", "p_gross_profit", "ev_revenue"],
        "disabled": ["pe", "pfcf"],
        "primary": "revenue",
        "extra_metrics": ["cash_runway_months", "burn_rate", "rule_of_40"],
        "warnings": [
            "กำไร/FCF ติดลบ — valuation multiple แบบดั้งเดิม (P/E, P/FCF) ใช้ไม่ได้",
            "ดู cash runway เป็นหลัก: มีเงินเหลือกี่เดือนก่อนต้องระดมทุนใหม่",
        ],
    },
    "biotech": {
        "label": "ยา / ไบโอเทค",
        "anchors": [],
        "multiples": [],
        "disabled": ["pe", "pfcf", "ps", "ev_ebitda"],
        "primary": None,
        "warnings": [
            "⛔ โมเดล multiple ใช้ไม่ได้กับ pre-revenue biotech",
            "มูลค่าขึ้นกับ pipeline, ผล clinical trial และ probability of approval",
        ],
    },
}


def get_profile(profile_key: str) -> dict:
    """คืน profile dict (fallback เป็น general ถ้าไม่รู้จัก key)."""
    return PROFILES.get(profile_key, PROFILES["general"])


def is_disabled(profile_key: str, metric: str) -> bool:
    """metric นี้ถูกปิดสำหรับ sector นี้หรือไม่ (เช่น pfcf ปิดสำหรับ bank/reit)."""
    return metric in get_profile(profile_key).get("disabled", [])


# ── ยูทิลิตี้สถิติ (ใช้ในชั้น 4-5) ─────────────────────────────────────────────
def percentile(values: list[float], pct: float) -> float:
    """เปอร์เซ็นไทล์แบบ interpolate เชิงเส้น (pct 0-100). ต้องมี values อย่างน้อย 1 ตัว."""
    if not values:
        raise ValueError("percentile ต้องการอย่างน้อย 1 ค่า")
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    rank = (pct / 100.0) * (len(xs) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(xs) - 1)
    frac = rank - lo
    return xs[lo] + (xs[hi] - xs[lo]) * frac


def remove_outliers_iqr(values: list[float], k: float = 1.5) -> list[float]:
    """ตัด outlier ด้วยกฎ IQR (นอกช่วง [Q1 − k·IQR, Q3 + k·IQR]). คืนค่าที่เหลือ
    (ถ้าตัดจนหมดหรือน้อยเกินไป คืนชุดเดิมกันหาย median ไม่ได้)."""
    if len(values) < 4:
        return list(values)
    q1 = percentile(values, 25)
    q3 = percentile(values, 75)
    iqr = q3 - q1
    lo, hi = q1 - k * iqr, q3 + k * iqr
    kept = [v for v in values if lo <= v <= hi]
    return kept if kept else list(values)


# ── ชั้น 4: Data Validation Gate ─────────────────────────────────────────────
def validate_anchor(series: list[dict], anchor_name: str) -> dict:
    """รันก่อนวาดเส้น "ราคาตาม X" ทุกครั้ง กันเส้นระเบิดจากฐานติดลบ/ค่าผิดปกติ.

    series: list ของ {value: float, date?: str, quarters_used?: int} เรียงตามเวลา (เก่า→ใหม่)
    คืน:
      {ok: False, reason} — ปิดเส้นนี้ไปเลย (X ไม่เคยเป็นบวกอย่างมีนัยสำคัญ)
      {ok: True, base_idx, issues} — วาดได้ แต่เลื่อนจุดฐานไป base_idx และมีคำเตือน issues
    """
    issues: list[str] = []
    values = [p.get("value") for p in series]
    values = [v for v in values if isinstance(v, (int, float))]
    if not values:
        return {"ok": False, "reason": f"{anchor_name}: ไม่มีข้อมูลตัวเลข — ปิดเส้นนี้"}

    positives = [v for v in values if v > 0]
    if not positives:
        return {"ok": False, "reason": f"{anchor_name} ไม่เคยเป็นบวกอย่างมีนัยสำคัญ — ปิดเส้นนี้"}
    threshold = _median(positives) * 0.1

    # A. เลื่อนจุดฐานให้พ้นช่วงต้นที่ค่า ≤ 0 หรือเล็กจิ๋ว (base เกือบศูนย์ทำให้เส้นพุ่งเวอร์)
    base_idx = next((i for i, p in enumerate(series)
                     if isinstance(p.get("value"), (int, float))
                     and p["value"] > 0 and p["value"] > threshold), None)
    if base_idx is None:
        return {"ok": False, "reason": f"{anchor_name}: ทุกค่าต่ำกว่าเกณฑ์นัยสำคัญ — ปิดเส้นนี้"}
    if base_idx > 0:
        where = series[base_idx].get("date", f"จุดที่ {base_idx}")
        issues.append(f"เลื่อนจุดฐานไป {where} เพราะช่วงก่อนหน้า {anchor_name} ≤ 0 หรือเล็กเกินไป")

    # B. ค่าติดลบระหว่างทาง → ควรเว้นช่องว่างในกราฟ (ไม่วาดค่าที่ไร้ความหมาย)
    gaps = sum(1 for p in series[base_idx:]
               if isinstance(p.get("value"), (int, float)) and p["value"] <= 0)
    if gaps:
        issues.append(f"{anchor_name} ติดลบ/เป็นศูนย์ {gaps} จุดหลังฐาน — เว้นช่องว่างในกราฟช่วงนั้น")

    # C. จุดที่ใช้ข้อมูลไม่ครบ 4 ไตรมาส (interpolate) — เส้นช่วงนั้นเชื่อถือได้น้อยกว่า
    incomplete = sum(1 for p in series if isinstance(p.get("quarters_used"), int)
                     and p["quarters_used"] < 4)
    if incomplete:
        issues.append(f"⚠️ {incomplete} จุดใช้ข้อมูลไม่ครบ 4 ไตรมาส (ประมาณค่า) — เส้นช่วงนั้นเป็นเส้นประ")

    # D. outlier ที่อาจเกิดจาก data error หรือ M&A (เปลี่ยน >400% ในหนึ่งช่วง)
    wild = 0
    prev = None
    for p in series[base_idx:]:
        v = p.get("value")
        if isinstance(v, (int, float)) and v > 0:
            if prev is not None and prev > 0:
                g = v / prev
                if g > 5 or g < 0.2:
                    wild += 1
            prev = v
    if wild:
        issues.append(f"พบการเปลี่ยนแปลง >400% {wild} ครั้ง — ตรวจสอบว่าเป็น M&A จริงหรือข้อมูลผิด")

    return {"ok": True, "base_idx": base_idx, "issues": issues}


# ── เพดานความสมเหตุสมผลของตัวคูณ ────────────────────────────────────────────
# ตัวคูณ median = "ราคาที่ตลาดเคยให้หุ้นตัวนี้" ไม่ใช่มูลค่าที่แท้จริง — หุ้นที่ตลาดให้ราคาแพง
# มาตลอด 2 ปีจะได้ราคายุติธรรมสูงตามไปด้วย แล้วโผล่ขึ้นหัวตาราง "undervalue" ทั้งที่ยังแพงอยู่
# (เคสจริง: median P/E 346x → ราคายุติธรรมสูงกว่าราคาจริง 94% ทั้งที่ P/E ปัจจุบันยังเกิน 170x)
# ด่านนี้ต่างจากด่าน "ห่างกันเกิน 5 เท่า" ใน revenue_model — ด่านนั้นจับ *ฐานพัง*, ด่านนี้จับ
# *ฐานเฟ้อ* ซึ่งตัวเลขออกมาดูสมเหตุผลทุกอย่าง ยกเว้นระดับของตัวคูณเอง
#   (rich, extreme) — เกิน rich = เตือน, เกิน extreme = ไม่ควรนับเป็นสัญญาณ "ถูก"
_MULTIPLE_LIMITS: dict[str, tuple[float, float]] = {
    "P/FFO": (25.0, 35.0),
    "P/FCF": (35.0, 60.0),
    "P/B": (4.0, 8.0),
    "P/S": (10.0, 20.0),
    "P/E": (35.0, 60.0),
}


def multiple_sanity(basis: str | None, multiple: float | None) -> dict:
    """ตัวคูณ median ที่ใช้ตั้งราคายุติธรรม อยู่ในระดับที่เชื่อได้ไหม.

    คืน {level, note} โดย level = ok | rich | extreme | unknown
    (unknown = จับคู่ basis กับเพดานไม่ได้ — ไม่ตัดสิน ปล่อยผ่านแบบไม่การันตี)."""
    if not basis or not isinstance(multiple, (int, float)) or multiple <= 0:
        return {"level": "unknown", "note": ""}

    # เรียงจากคีย์ยาวไปสั้น กัน "P/E" ไปชนกับ basis ที่จริง ๆ เป็น P/FFO/P/FCF
    for key in sorted(_MULTIPLE_LIMITS, key=len, reverse=True):
        if key in basis:
            rich, extreme = _MULTIPLE_LIMITS[key]
            if multiple >= extreme:
                return {"level": "extreme", "note": (
                    f"ตัวคูณฐาน {key} = {multiple:.0f}x สูงผิดปกติ (เกิน {extreme:.0f}x) — "
                    f"ราคายุติธรรมนี้แปลว่า 'ตลาดเคยให้ราคาแพงกว่านี้' ไม่ใช่ 'หุ้นถูก'")}
            if multiple >= rich:
                return {"level": "rich", "note": (
                    f"ตัวคูณฐาน {key} = {multiple:.0f}x ถือว่าแพงอยู่แล้ว (เกิน {rich:.0f}x) — "
                    f"upside ที่เห็นวัดจากฐานที่ตลาดให้ราคาสูง ไม่ใช่จากราคาถูกโดยเนื้อธุรกิจ")}
            return {"level": "ok", "note": ""}
    return {"level": "unknown", "note": ""}


# ── ชั้น 5: Median-based Fair Value Line + Band ──────────────────────────────
def median_band(ratios: list[float], min_points: int = 8) -> dict | None:
    """สรุปชุดค่า P/X (เช่น P/E, P/FCF ทุกจุดในอดีต) เป็น median + band IQR หลังตัด outlier.
    ใช้แทน "หารด้วยวันเดียว" ที่เสี่ยงเลื่อนทั้งเส้นถ้าวันฐานผิดปกติ.

    คืน {median, p25, p75, n} หรือ None ถ้าจุดน้อยเกิน (< min_points)."""
    clean = [r for r in ratios if isinstance(r, (int, float)) and r > 0 and r == r]  # ตัด None/NaN/≤0
    if len(clean) < min_points:
        return None
    clean = remove_outliers_iqr(clean)
    return {
        "median": _median(clean),
        "p25": percentile(clean, 25),
        "p75": percentile(clean, 75),
        "n": len(clean),
    }


def fair_value_band(prices: list[dict], anchor_series: list[dict],
                    lookback_years: int = 5, min_points: int = 8) -> dict:
    """เส้นมูลค่ายุติธรรมแบบ median-based + band (ชั้น 5 เต็มรูปแบบ).

    เดิม: ราคาฐาน × (X_now / X_base)  ← ขึ้นกับวันเดียว เสี่ยงเลื่อนทั้งเส้น
    ใหม่: median(P/X ตลอดช่วง) × X_now/หุ้น  ← เสถียร สอดคล้องกับกราฟ P/E, P/FCF

    prices:        list ของ {close: float} จับคู่ตำแหน่งกับ anchor_series
    anchor_series: list ของ {per_share: float, date?: str}
    คืน {ok, line, lower, upper, median_ratio, note} หรือ {ok: False, reason}.
    """
    ratios = []
    for i, a in enumerate(anchor_series):
        ps = a.get("per_share")
        if i < len(prices) and isinstance(ps, (int, float)) and ps > 0:
            close = prices[i].get("close")
            if isinstance(close, (int, float)):
                r = close / ps
                if r == r and r > 0:
                    ratios.append(r)
    band = median_band(ratios, min_points=min_points)
    if band is None:
        return {"ok": False, "reason": f"ข้อมูลน้อยเกินไป (<{min_points} จุด) — ประเมิน fair value ไม่ได้"}

    m, p25, p75 = band["median"], band["p25"], band["p75"]

    def project(mult):
        return [(a["per_share"] * mult if isinstance(a.get("per_share"), (int, float)) else None)
                for a in anchor_series]

    return {
        "ok": True,
        "line": project(m),
        "lower": project(p25),
        "upper": project(p75),
        "median_ratio": m,
        "note": f"median {lookback_years} ปี = {m:.1f}x (IQR {p25:.1f}–{p75:.1f}x)",
    }
