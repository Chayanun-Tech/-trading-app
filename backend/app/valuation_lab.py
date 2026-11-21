"""ห้องคำนวณมูลค่า (Valuation Lab) — ผู้ใช้ตั้งสมมติฐานเอง ระบบทำหน้าที่ "ป้อนข้อมูล" ให้ครบ

  /api/lab/universe   รายชื่อหุ้น S&P 500 + Nasdaq-100 แยกเซกเตอร์ → อุตสาหกรรมย่อย (GICS)
  /api/lab/stock      งบย้อนหลังแยกหมวด (SEC) + ราคา + ค่าสถิติที่ใช้ตั้งสมมติฐาน (คำใบ้แบบอาจารย์)
                      + ผลประเมินของระบบเป็น "ความเห็นที่สอง" (ไม่ใช่คำตอบ)
  /api/lab/macro      ดอกเบี้ย/เงินเฟ้อ/ส่วนต่างเครดิต ย้อนหลังรายเดือน (FRED) — เห็นแนวโน้ม
  /api/lab/journal    บันทึกประวัติการให้มูลค่าตามสมมติฐานของผู้ใช้ (ไฟล์ในเครื่อง)

การคำนวณมูลค่าเกิดในหน้าเว็บ (JS) ทันทีที่ผู้ใช้ขยับตัวเลข — backend ไม่ตัดสินแทนผู้ใช้
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import statistics
import time
import uuid
from datetime import date, datetime
from pathlib import Path

import httpx

from app import edgar
from app.consensus_value import gather_inputs
from app.financials import build_financials
from app.industry_peers import load_constituents

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
LAB = DATA / "lab"
MACRO_FILE = LAB / "macro.json"
JOURNAL_FILE = LAB / "valuation_journal.json"
RANGES_FILE = DATA / "intrinsic_ranges.json"

# หุ้นใน Nasdaq-100/รายการโฟกัสที่ไม่อยู่ในไฟล์ GICS ของ S&P 500 — ใส่หมวดให้เอง
EXTRA_GICS = {
    "ASML": ("Information Technology", "Semiconductor Materials & Equipment"),
    "ARM": ("Information Technology", "Semiconductors"),
    "TSM": ("Information Technology", "Semiconductors"),
    "ALAB": ("Information Technology", "Semiconductors"),
    "SHOP": ("Information Technology", "Internet Services & Infrastructure"),
    "OKTA": ("Information Technology", "Systems Software"),
    "TWLO": ("Information Technology", "Internet Services & Infrastructure"),
    "NBIS": ("Information Technology", "Internet Services & Infrastructure"),
    "P": ("Information Technology", "Technology Hardware, Storage & Peripherals"),
    "PDD": ("Consumer Discretionary", "Broadline Retail"),
    "MELI": ("Consumer Discretionary", "Broadline Retail"),
    "CCEP": ("Consumer Staples", "Soft Drinks & Non-alcoholic Beverages"),
    "TRI": ("Industrials", "Research & Consulting Services"),
    "FER": ("Industrials", "Construction & Engineering"),
    "RKLB": ("Industrials", "Aerospace & Defense"),
    "BE": ("Industrials", "Heavy Electrical Equipment"),
    "ALNY": ("Health Care", "Biotechnology"),
    "ILMN": ("Health Care", "Life Sciences Tools & Services"),
    "HIMS": ("Health Care", "Health Care Services"),
    "SKYD": ("Communication Services", "Movies & Entertainment"),
}
SECTOR_TH = {
    "Information Technology": "เทคโนโลยีสารสนเทศ", "Health Care": "การแพทย์/สุขภาพ", "Financials": "การเงิน",
    "Consumer Discretionary": "สินค้าฟุ่มเฟือย", "Communication Services": "สื่อสาร/อินเทอร์เน็ต",
    "Industrials": "อุตสาหกรรม", "Consumer Staples": "สินค้าจำเป็น", "Energy": "พลังงาน", "Utilities": "สาธารณูปโภค",
    "Real Estate": "อสังหาริมทรัพย์", "Materials": "วัตถุดิบ",
}


def _load_ranges() -> dict[str, dict]:
    try:
        d = json.loads(RANGES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {r["symbol"].upper(): r for r in d.get("results", [])}


# ── รายชื่อหุ้นแยกหมวด ────────────────────────────────────────────────────────────
def universe() -> dict:
    cons = load_constituents()
    ranges = _load_ranges()
    rows: dict[str, dict] = {}
    for sym, c in cons.items():
        rows[sym] = {"symbol": sym, "name": c["name"], "sector": c["sector"], "industry": c["sub_industry"],
                     "index": ["SP500"]}
    for sym, r in ranges.items():
        key = sym.replace("-", ".")
        if key in rows or sym in rows:
            (rows.get(key) or rows[sym])["index"] = r.get("index") or ["SP500"]
            continue
        sec, ind = EXTRA_GICS.get(sym, ("อื่น ๆ", "ไม่ระบุ"))
        rows[sym] = {"symbol": sym, "name": r.get("name") or sym, "sector": sec, "industry": ind,
                     "index": r.get("index") or []}
    tree: dict[str, dict[str, list]] = {}
    for r in rows.values():
        rr = ranges.get(r["symbol"]) or ranges.get(r["symbol"].replace(".", "-")) or {}
        r["model_fair"] = rr.get("fair_price")
        r["price"] = rr.get("price")
        tree.setdefault(r["sector"], {}).setdefault(r["industry"], []).append(r)
    sectors = []
    for sec in sorted(tree):
        inds = [{"industry": ind, "stocks": sorted(v, key=lambda x: x["symbol"])} for ind, v in sorted(tree[sec].items())]
        sectors.append({"sector": sec, "sector_th": SECTOR_TH.get(sec, sec), "count": sum(len(i["stocks"]) for i in inds),
                        "industries": inds})
    return {"count": len(rows), "sectors": sectors}


# ── ข้อมูลรายหุ้น ───────────────────────────────────────────────────────────────
def _series(fin: dict, group: str, key: str) -> list:
    for g in fin["groups"]:
        if g["key"] == group:
            for m in g["metrics"]:
                if m["key"] == key:
                    return m["values"]
    return []


def _cagr(vals: list, years: int) -> float | None:
    xs = [v for v in vals if v is not None]
    if len(xs) < years + 1:
        return None
    a, b = xs[-1 - years], xs[-1]
    if not a or a <= 0 or b <= 0:
        return None
    return (b / a) ** (1 / years) - 1


def _med(vals: list, n: int = 5) -> float | None:
    xs = [v for v in vals[-n:] if v is not None]
    return statistics.median(xs) if xs else None


def build_hints(fin: dict, inp: dict, price: float | None, shares: float | None) -> dict:
    """สถิติที่อาจารย์จะชี้ให้ดูก่อนตั้งสมมติฐาน — ทุกค่าเป็น "ข้อมูลอดีต" ไม่ใช่คำทำนาย."""
    rev = _series(fin, "income", "revenue")
    eps = _series(fin, "income", "eps_diluted")
    fcf = _series(fin, "cashflow", "free_cash_flow")
    sbc_hist = inp.get("sbc") or {}
    fcfm = _series(fin, "ratios", "fcf_margin")
    opm = _series(fin, "ratios", "operating_margin")
    roe = _series(fin, "ratios", "roe")
    sh = _series(fin, "pershare", "shares_diluted")
    ocf_ttm, capex_ttm, sbc_ttm = inp.get("ocf_ttm"), inp.get("capex_ttm"), inp.get("sbc_ttm")
    fcf_ttm = (ocf_ttm - capex_ttm) if ocf_ttm is not None and capex_ttm is not None else None
    owner_ttm = (fcf_ttm - (sbc_ttm or 0)) if fcf_ttm is not None else None
    fcf_avg3 = statistics.mean([v for v in fcf[-3:] if v is not None]) if any(v is not None for v in fcf[-3:]) else None
    sbc_years = sorted(sbc_hist)[-3:]
    owner_avg3 = (fcf_avg3 - statistics.mean([sbc_hist[y] for y in sbc_years])) if fcf_avg3 is not None and sbc_years else fcf_avg3
    eps_ttm = None
    if inp.get("ni_ttm") is not None and shares:
        eps_ttm = inp["ni_ttm"] / shares
    share_chg = _cagr(sh, 5)
    return {
        "revenue_ttm": inp.get("revenue_ttm"),
        "rev_cagr_3": _cagr(rev, 3), "rev_cagr_5": _cagr(rev, 5), "rev_cagr_10": _cagr(rev, 10),
        "eps_cagr_5": _cagr(eps, 5), "fcf_cagr_5": _cagr(fcf, 5),
        "fcf_margin_med5": _med(fcfm), "op_margin_med5": _med(opm), "roe_med5": _med(roe),
        "fcf_ttm": fcf_ttm, "sbc_ttm": sbc_ttm, "owner_fcf_ttm": owner_ttm,
        "fcf_avg3": fcf_avg3, "owner_fcf_avg3": owner_avg3,
        "eps_ttm": eps_ttm, "eps_last_fy": next((v for v in reversed(eps) if v is not None), None),
        "cash": inp.get("cash"), "debt": inp.get("debt"), "net_cash": (inp.get("cash") or 0) - (inp.get("debt") or 0),
        "shares": shares, "share_change_5y": share_chg,
        "pe_ttm": (price / eps_ttm) if price and eps_ttm and eps_ttm > 0 else None,
        "fcf_yield": (fcf_ttm / (price * shares)) if price and shares and fcf_ttm is not None else None,
        "data_end": inp.get("data_end"),
    }


def adjust_splits(fin: dict) -> list[str]:
    """งบ SEC เก็บ EPS/จำนวนหุ้นของปีเก่าตามจำนวนหุ้นตอนนั้น (ไม่ปรับ split) — เช่น NVDA split 4:1 (2021)
    และ 10:1 (2024) ทำให้ EPS เหมือนลดลง. ตรวจจากจำนวนหุ้นที่กระโดด ≥1.8 เท่าในปีเดียว (ใกล้จำนวนเต็ม)
    แล้วปรับปีก่อนหน้าทั้งหมดให้เทียบกันได้. คืนรายการ split ที่พบ (โชว์ให้ผู้ใช้รู้)."""
    ps = next((g for g in fin["groups"] if g["key"] == "pershare"), None)
    inc = next((g for g in fin["groups"] if g["key"] == "income"), None)
    if not ps:
        return []
    rows = {m["key"]: m for m in ps["metrics"]}
    sh = rows.get("shares_diluted", {}).get("values") or []
    found = []
    for i in range(len(sh) - 1, 0, -1):
        a, b = sh[i - 1], sh[i]
        if not a or not b:
            continue
        ratio = b / a
        k = round(ratio)
        if ratio >= 1.8 and abs(ratio - k) / k < 0.12:
            found.append(f"{fin['periods'][i]} split {k}:1")
            for j in range(i):
                if sh[j]:
                    sh[j] *= k
                for key in ("eps_diluted", "revenue_per_share", "fcf_per_share", "book_value_per_share"):
                    v = rows.get(key, {}).get("values")
                    if v and v[j] is not None:
                        v[j] /= k
                if inc:
                    for m in inc["metrics"]:
                        if m["key"] == "eps_diluted" and m["values"][j] is not None and m["values"] is not rows.get("eps_diluted", {}).get("values"):
                            m["values"][j] /= k
    return list(reversed(found))


async def stock(symbol: str, provider=None) -> dict:
    sym = symbol.upper().strip()
    facts = await edgar.get_company_facts(sym)
    fin = build_financials(facts, "annual", max_periods=11)
    fin["splits"] = adjust_splits(fin)
    try:
        inp = gather_inputs(facts)
    except Exception:  # noqa: BLE001
        inp = {}
    price, quote = None, None
    if provider is not None:
        try:
            q = await provider.get_quote(sym)
            price = float(q.price) if q and q.price else None
            quote = {"price": price, "change_pct": getattr(q, "change_percent", None)}
        except Exception:  # noqa: BLE001
            pass
    model = _load_ranges().get(sym) or _load_ranges().get(sym.replace(".", "-"))
    if price is None and model:
        price = model.get("price")
    # จำนวนหุ้น: ล่าสุดจากงบ (ถัวเฉลี่ย diluted) — ถ้ามีผลระบบ ใช้ market cap ที่สอดคล้องกับราคา
    sh_series = [v for v in _series(fin, "pershare", "shares_diluted") if v]
    shares = sh_series[-1] if sh_series else None
    cons = load_constituents().get(sym) or load_constituents().get(sym.replace("-", "."))
    sec, ind = (cons["sector"], cons["sub_industry"]) if cons else EXTRA_GICS.get(sym, ("อื่น ๆ", "ไม่ระบุ"))
    return {
        "symbol": sym, "name": (cons or {}).get("name") or (model or {}).get("name") or facts.get("entityName"),
        "sector": sec, "sector_th": SECTOR_TH.get(sec, sec), "industry": ind,
        "price": price, "quote": quote, "currency": inp.get("unit", "USD"),
        "financials": fin, "hints": build_hints(fin, inp, price, shares),
        "model": None if not model else {
            k: model.get(k) for k in ("low", "fair_price", "high", "archetype_label", "method", "confidence_level",
                                      "discount_rate_pct", "assumptions", "implied_growth_pct", "hist_growth_pct",
                                      "buy_allowed", "no_buy_reason")},
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


# ── ปัจจัยมหภาค (FRED) ──────────────────────────────────────────────────────────
MACRO_SERIES = [
    # id, ชื่อ, คำอธิบายแบบอาจารย์, แปลงค่า
    ("DGS10", "ผลตอบแทนพันธบัตรรัฐบาลสหรัฐ 10 ปี", "จุดตั้งต้นของอัตราคิดลด: หุ้นควรให้ผลตอบแทนมากกว่านี้ 4–6% (ส่วนชดเชยความเสี่ยง)", None),
    ("DGS2", "พันธบัตร 2 ปี", "สะท้อนการคาดการณ์ดอกเบี้ยนโยบายช่วงสั้น", None),
    ("FEDFUNDS", "ดอกเบี้ยนโยบาย Fed", "ต้นทุนเงินของทั้งระบบ — ขึ้นเร็วมักกดมูลค่าหุ้นโตเร็ว", None),
    ("DFII10", "ดอกเบี้ยแท้จริง 10 ปี (หักเงินเฟ้อ)", "ยิ่งสูง หุ้นที่มูลค่าอยู่ไกลในอนาคต (หุ้นโต) ยิ่งถูกกด", None),
    ("T10Y2Y", "ส่วนต่าง 10 ปี − 2 ปี", "ติดลบ (yield curve กลับหัว) มักนำหน้าเศรษฐกิจถดถอย", None),
    ("CPIAUCSL", "เงินเฟ้อ CPI (% เทียบปีก่อน)", "เงินเฟ้อสูง → ดอกเบี้ยสูงนาน → อัตราคิดลดสูง", "yoy"),
    ("BAA10Y", "ส่วนต่างหุ้นกู้เกรด BAA − พันธบัตร 10 ปี", "ตลาดกลัวความเสี่ยงแค่ไหน — กว้างขึ้นเร็ว = ความกลัวสูง", None),
    ("UNRATE", "อัตราว่างงานสหรัฐ", "เศรษฐกิจจริง — ว่างงานเริ่มขึ้นจากจุดต่ำมักเป็นสัญญาณขาลงของเศรษฐกิจ", None),
]
MACRO_TTL = 12 * 3600


async def _fred_csv(client: httpx.AsyncClient, sid: str) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    for a, b in (("2000-01-01", "2012-12-31"), ("2013-01-01", "2035-12-31")):
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd={a}&coed={b}"
        for attempt in range(3):
            try:
                r = await client.get(url, headers={"User-Agent": "curl/8.0"}, timeout=60)
                r.raise_for_status()
                break
            except Exception:  # noqa: BLE001
                if attempt == 2:
                    raise
                await asyncio.sleep(2)
        for row in list(csv.reader(io.StringIO(r.text)))[1:]:
            if len(row) >= 2 and row[1] not in ("", "."):
                try:
                    out.append((row[0], float(row[1])))
                except ValueError:
                    pass
    return out


def _monthly(points: list[tuple[str, float]], transform: str | None) -> list[list]:
    by_m: dict[str, list[float]] = {}
    for d, v in points:
        by_m.setdefault(d[:7], []).append(v)
    months = sorted(by_m)
    vals = {m: statistics.mean(by_m[m]) for m in months}
    if transform == "yoy":
        out = []
        for m in months:
            y, mo = int(m[:4]), m[5:]
            prev = vals.get(f"{y - 1}-{mo}")
            if prev:
                out.append([m, round((vals[m] / prev - 1) * 100, 2)])
        return out
    return [[m, round(vals[m], 3)] for m in months]


async def macro(refresh: bool = False) -> dict:
    if not refresh and MACRO_FILE.exists() and time.time() - MACRO_FILE.stat().st_mtime < MACRO_TTL:
        return json.loads(MACRO_FILE.read_text(encoding="utf-8"))
    old = json.loads(MACRO_FILE.read_text(encoding="utf-8")) if MACRO_FILE.exists() else {"series": []}
    old_by = {s["id"]: s for s in old.get("series", [])}
    series, errors = [], []
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*[_fred_csv(client, sid) for sid, *_ in MACRO_SERIES], return_exceptions=True)
    for (sid, name, note, tf), res in zip(MACRO_SERIES, results):
        if isinstance(res, Exception) or not res:
            errors.append(sid)
            if sid in old_by:
                series.append(old_by[sid])
            continue
        pts = _monthly(res, tf)
        series.append({"id": sid, "name": name, "note": note, "unit": "%", "points": pts,
                       "latest": pts[-1] if pts else None, "daily_latest": [res[-1][0], res[-1][1]] if tf is None else None})
    out = {"updated": datetime.now().strftime("%Y-%m-%d %H:%M"), "source": "FRED (Federal Reserve Bank of St. Louis)",
           "series": series, "errors": errors}
    if series:
        LAB.mkdir(parents=True, exist_ok=True)
        MACRO_FILE.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


# ── บันทึกประวัติการให้มูลค่า ─────────────────────────────────────────────────────
def _read_journal() -> list[dict]:
    try:
        return json.loads(JOURNAL_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def journal_list(symbol: str | None = None) -> list[dict]:
    rows = _read_journal()
    if symbol:
        rows = [r for r in rows if r.get("symbol") == symbol.upper()]
    return sorted(rows, key=lambda r: r.get("saved_at", ""), reverse=True)


def journal_save(entry: dict) -> dict:
    if not entry.get("symbol"):
        raise ValueError("ต้องมี symbol")
    e = dict(entry)
    e["symbol"] = str(e["symbol"]).upper()
    e.setdefault("id", uuid.uuid4().hex[:12])
    e.setdefault("saved_at", datetime.now().strftime("%Y-%m-%d %H:%M"))
    rows = [r for r in _read_journal() if r.get("id") != e["id"]]
    rows.append(e)
    LAB.mkdir(parents=True, exist_ok=True)
    JOURNAL_FILE.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    return e


def journal_delete(entry_id: str) -> bool:
    rows = _read_journal()
    keep = [r for r in rows if r.get("id") != entry_id]
    if len(keep) == len(rows):
        return False
    JOURNAL_FILE.write_text(json.dumps(keep, ensure_ascii=False, indent=1), encoding="utf-8")
    return True


def today() -> str:
    return date.today().isoformat()
