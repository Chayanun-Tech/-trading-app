"""คัดหุ้น Mega Trend ที่ "ถือยาวได้" จากข้อมูลปัจจุบัน — ต่อยอดผลใน data/intrinsic_ranges.json

1) Mega Trend: จับคู่หุ้นกับเทรนด์ใหญ่ 8 กลุ่ม (กำหนดตายตัวในไฟล์นี้ ไม่ปรับตามความชอบรายตัว) จาก
   • Ecosystem Map ของแอป (backend/app/data_ecosystem — 85 ธีม พร้อมบริษัทจริงและบทบาท leader/challenger)
   • GICS Sub-Industry ของ S&P 500 (sp500_constituents.csv)
   • Industry จาก Yahoo สำหรับหุ้นนอก S&P 500 (Nasdaq-100 / รายชื่อโฟกัส)
2) คุณภาพสำหรับถือยาว — 6 ข้อ คำนวณจากงบ SEC ปัจจุบันเท่านั้น (ไม่พยากรณ์อนาคต):
   Q1 สร้างเงินสดให้เจ้าของจริง   Owner FCF margin มัธยฐาน 5 ปี ≥ 5%   (ธนาคาร/ประกัน: ROE ≥ 10%)
   Q2 สม่ำเสมอ                    Owner FCF เป็นบวกทุกปีใน 5 ปีล่าสุด (ยอมพลาดได้ 1 ปี)
   Q3 ธุรกิจยังโต                  CAGR รายได้ ≥ 5%/ปี
   Q4 ผลตอบแทนต่อทุนดี             ROE มัธยฐาน ≥ 12% (ทุนติดลบจากซื้อหุ้นคืน → ใช้ FCF margin ≥ 15% แทน)
   Q5 งบดุลแข็ง                    หนี้สุทธิ ≤ 3 เท่าของ Owner FCF ปกติ (หรือเงินสดมากกว่าหนี้)
   Q6 ไม่ออกหุ้นเพิ่มจนเจือจาง      จำนวนหุ้น (diluted) โตไม่เกิน 2%/ปี ช่วง 3 ปี
   "ถือยาวได้" = ผ่าน ≥ 5 ใน 6 ข้อ และต้องผ่าน Q1 กับ Q5 เสมอ (ไม่มีเงินสด/หนี้ล้น = อยู่ไม่รอดระยะยาว)

รัน:  python ops/megatrend_screen.py      → data/megatrend_quality.json
"""
from __future__ import annotations

import asyncio
import csv
import glob
import math
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import edgar  # noqa: E402
from app.consensus_value import gather_inputs, owner_fcf_margins, revenue_cagr  # noqa: E402
from app.revenue_model import _COMMON_SPLIT_RATIOS as _SPLITS, _annual_shares  # noqa: E402

APP = ROOT / "backend" / "app"
OUT = ROOT / "data" / "megatrend_quality.json"

# ── นิยาม Mega Trend (แหล่งเดียวของความจริง) ─────────────────────────────────────
TRENDS = {
    "ai": "AI & ชิป/ดาต้าเซ็นเตอร์",
    "software": "คลาวด์ ซอฟต์แวร์ & ไซเบอร์",
    "digital": "แพลตฟอร์มดิจิทัล & ชำระเงิน",
    "electrify": "ไฟฟ้า & พลังงานเปลี่ยนผ่าน",
    "automation": "ระบบอัตโนมัติ & หุ่นยนต์",
    "health": "สุขภาพสังคมสูงวัย & นวัตกรรมการแพทย์",
    "space": "อวกาศ & ความมั่นคง",
    "scarcity": "ทรัพยากรขาดแคลน (น้ำ/แร่วิกฤต)",
}
THEME_TO_TREND = {
    "ai-ml-infrastructure": "ai", "semiconductor-design-fabless": "ai", "semiconductor-foundry": "ai",
    "semiconductor-equipment": "ai", "data-center-networking": "ai", "data-center-reits": "ai",
    "cloud-infrastructure": "software", "enterprise-software-saas": "software", "cybersecurity": "software",
    "payments-fintech-software": "digital", "consumer-payments-fintech": "digital",
    "ecommerce-online-retail": "digital", "digital-advertising": "digital", "social-media-platforms": "digital",
    "streaming-media": "digital", "video-gaming-esports": "digital",
    "electric-vehicles": "electrify", "battery-ev-materials": "electrify", "energy-storage-battery-tech": "electrify",
    "ev-charging-infrastructure": "electrify", "solar-energy": "electrify", "wind-energy": "electrify",
    "renewable-utility-power": "electrify", "electrical-equipment-machinery": "electrify",
    "industrial-automation-robotics": "automation",
    "glp1-obesity-drug-ecosystem": "health", "genomics-precision-medicine": "health",
    "medical-devices": "health", "digital-health": "health", "diagnostics-life-science-tools": "health",
    "space-economy": "space", "aerospace-defense": "space",
    "water-scarcity-desalination": "scarcity", "rare-earth-critical-minerals": "scarcity",
}
GICS_TO_TREND = {
    "Semiconductors": "ai", "Semiconductor Materials & Equipment": "ai", "Data Center REITs": "ai",
    "Internet Services & Infrastructure": "ai", "Communications Equipment": "ai", "Electronic Components": "ai",
    "Technology Hardware, Storage & Peripherals": "ai",
    "Systems Software": "software", "Application Software": "software",
    "Interactive Media & Services": "digital", "Transaction & Payment Processing Services": "digital",
    "Broadline Retail": "digital", "Interactive Home Entertainment": "digital",
    "Electrical Components & Equipment": "electrify", "Heavy Electrical Equipment": "electrify",
    "Independent Power Producers & Energy Traders": "electrify", "Copper": "electrify",
    "Electronic Equipment & Instruments": "automation",
    "Health Care Equipment": "health", "Life Sciences Tools & Services": "health", "Biotechnology": "health",
    "Aerospace & Defense": "space",
}
# Yahoo industry (หุ้นนอก S&P 500) — จับด้วยคำสำคัญ
YAHOO_KEYWORDS = [
    ("semiconductor", "ai"), ("computer hardware", "ai"), ("communication equipment", "ai"),
    ("software", "software"), ("information technology services", "software"),
    ("internet content", "digital"), ("internet retail", "digital"), ("credit services", "digital"),
    ("electronic gaming", "digital"), ("entertainment", "digital"),
    ("auto manufacturers", "electrify"), ("electrical equipment", "electrify"), ("solar", "electrify"),
    ("renewable", "electrify"), ("copper", "electrify"),
    ("medical devices", "health"), ("medical instruments", "health"), ("diagnostics", "health"),
    ("biotechnology", "health"), ("health information", "health"),
    ("aerospace", "space"),
]


def ecosystem_map() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for f in glob.glob(str(APP / "data_ecosystem" / "*.json")):
        d = json.loads(Path(f).read_text(encoding="utf-8"))
        trend = THEME_TO_TREND.get(d.get("slug"))
        for comp in d.get("components", []):
            for co in comp.get("companies", []):
                t = (co.get("ticker") or "").upper().replace(".", "-")
                if not t or co.get("role") == "failed_case":
                    continue
                e = out.setdefault(t, {"trends": set(), "themes": set(), "leader_in": set()})
                e["themes"].add(d.get("name_th"))
                if trend:
                    e["trends"].add(trend)
                    if co.get("role") == "leader":
                        e["leader_in"].add(TRENDS[trend])
    return out


def gics_map() -> dict[str, str]:
    with (APP / "sp500_constituents.csv").open(encoding="utf-8") as f:
        return {r["Symbol"].strip().upper().replace(".", "-"): r["GICS Sub-Industry"] for r in csv.DictReader(f)}


def quality(inp: dict, shares: dict, profile: str) -> dict:
    margins = owner_fcf_margins(inp)
    m = statistics.median(margins) if margins else None
    cagr = revenue_cagr(inp["revenue"])
    roes = [inp["net_income"][y] / e for y, e in (inp.get("equity_hist") or {}).items()
            if e and e > 0 and y in inp["net_income"]][-5:]
    roe = statistics.median(roes) if roes else None
    fin = profile in ("bank", "insurance")
    eq_neg = (inp.get("equity") or 0) <= 0
    norm_fcf = (inp.get("revenue_ttm") or 0) * (m or 0)
    net_debt = inp["debt"] - inp["cash"]
    yrs = sorted(y for y in shares if shares[y]["val"] and shares[y]["val"] > 0)
    dil = None
    if len(yrs) >= 4:
        # ปรับ split ออกก่อน: ปีไหนจำนวนหุ้นกระโดดเป็นอัตรา split มาตรฐาน (2,3,4,10...) เทียบปีก่อน
        # ถือว่าเป็น split ไม่ใช่การออกหุ้นเพิ่ม (เจอจริง: NVDA/AVGO/NFLX split 10:1 → โต +113%/ปี ผิด)
        adj = {yrs[0]: shares[yrs[0]]["val"]}
        factor = 1.0
        for a, b in zip(yrs, yrs[1:]):
            ratio = shares[b]["val"] / shares[a]["val"]
            # split ในปีเดียวกับการออกหุ้นซื้อกิจการ (AVGO: 10:1 + VMware ~13%) อัตราจะเพี้ยนจากเลขกลม
            # จึงยอมคลาดได้ −10%…+25% แล้วเก็บส่วนเกินไว้เป็นการเจือจางจริง
            k = min(_SPLITS, key=lambda k: abs(math.log(ratio / k)))  # split ที่ใกล้ที่สุด (กัน 8 ชนะ 10)
            k = k if 0.9 <= ratio / k <= 1.25 else None
            if k:
                factor *= k
            adj[b] = shares[b]["val"] / factor
        dil = (adj[yrs[-1]] / adj[yrs[-4]]) ** (1 / 3) - 1

    q = {
        "Q1": (roe is not None and roe >= 0.10) if fin else (m is not None and m >= 0.05),
        "Q2": True if fin else (len(margins) >= 3 and sum(x <= 0 for x in margins[-6:]) <= 1),
        "Q3": cagr is not None and cagr >= 0.05,
        "Q4": (m is not None and m >= 0.15) if (eq_neg and not fin) else (roe is not None and roe >= 0.12),
        "Q5": True if fin else (net_debt <= 0 or (norm_fcf > 0 and net_debt <= 3 * norm_fcf)),
        "Q6": dil is None or dil <= 0.02,  # ไม่มีข้อมูลจำนวนหุ้น (เช่นหุ้นหลาย class) = ไม่มีหลักฐานว่าเจือจาง
    }
    passed = sum(q.values())
    return {
        "checks": q, "passed": passed, "long_term": passed >= 5 and q["Q1"] and q["Q5"],
        "fcf_margin_pct": round(m * 100, 1) if m is not None else None,
        "roe_pct": round(roe * 100, 1) if roe is not None else None,
        "rev_cagr_pct": round(cagr * 100, 1) if cagr is not None else None,
        "net_debt_to_fcf": round(net_debt / norm_fcf, 1) if norm_fcf > 0 else None,
        "share_growth_pct": round(dil * 100, 1) if dil is not None else None,
    }


async def main() -> None:
    from app.fundamentals import fetch_yahoo_fundamentals

    rows = json.loads((ROOT / "data" / "intrinsic_ranges.json").read_text(encoding="utf-8"))["results"]
    eco, gics = ecosystem_map(), gics_map()
    sem = asyncio.Semaphore(8)
    out: dict[str, dict] = {}

    async def one(r: dict) -> None:
        sym = r["symbol"]
        trends = set(eco.get(sym, {}).get("trends", set()))
        industry = gics.get(sym)
        async with sem:
            if industry in GICS_TO_TREND:
                trends.add(GICS_TO_TREND[industry])
            elif industry is None:
                try:
                    industry = (await fetch_yahoo_fundamentals(sym)).get("industry") or ""
                    low = industry.lower()
                    trends.update(t for kw, t in YAHOO_KEYWORDS if kw in low)
                except Exception:  # noqa: BLE001
                    industry = ""
            try:
                facts = await edgar.get_company_facts(sym)
                qual = quality(gather_inputs(facts), _annual_shares(facts), r.get("profile", "general"))
            except Exception as e:  # noqa: BLE001
                print(f"  ! {sym}: {e!r}")
                qual = None
        out[sym] = {
            "trends": sorted(trends), "industry": industry,
            "leader_in": sorted(eco.get(sym, {}).get("leader_in", set())),
            "quality": qual,
        }

    await asyncio.gather(*(one(r) for r in rows))
    OUT.write_text(json.dumps({"trends": TRENDS, "stocks": out}, ensure_ascii=False, indent=1), encoding="utf-8")
    mt = [s for s, v in out.items() if v["trends"]]
    lt = [s for s in mt if (out[s]["quality"] or {}).get("long_term")]
    print(f"Mega Trend {len(mt)} ตัว · ถือยาวได้ {len(lt)} ตัว → {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
