"""Backtest โมเดลมูลค่าที่แท้จริง (consensus_value) ย้อนหลัง ~2013–2026 แบบ point-in-time.

ทุกปี Y: ยืนอยู่ ณ 31 ธ.ค. ของปี Y−1 แล้ว
  1. จักรวาล = สมาชิก S&P 500 ณ สิ้นปี Y−1 (ไม่ใช่รายชื่อวันนี้ → ลด survivorship bias)
  2. ใช้งบ SEC เฉพาะตัวที่ "ยื่นแล้ว" (filed ≤ วันตัด) → ไม่มองเห็นอนาคต (look-ahead bias)
  3. มูลค่าตลาด = ราคาปิดจริง ณ ธ.ค. Y−1 (ถอดการปรับ split ของ Yahoo ออก) × จำนวนหุ้นจากหน้าปกงบล่าสุด
  4. รันโมเดลชุดเดิม (DCF Owner FCF + Earnings Power + Justified P/B สำหรับการเงิน) ด้วยสมมติฐานเดิม
     ที่ล็อกไว้ (คิดลด 8.5%, โตสูงสุด 25%) — ไม่ปรับตามผล
  5. วัดผลตอบแทนรวมปันผลของปี Y (adjclose ธ.ค. Y−1 → ธ.ค. Y; ปีล่าสุดถึงเดือนล่าสุดที่มี)

ต่างจากของจริงที่ใช้วันนี้: ไม่มีโมเดล "ตัวคูณในอดีต" (ต้องใช้ราคารายสัปดาห์ย้อนหลังของทุกตัว) และข้าม REIT
ข้อจำกัด: หุ้นที่ล้มละลาย/ถูกซื้อ/เปลี่ยน ticker มักไม่มีข้อมูลใน Yahoo หรือ SEC ticker map → ตกหล่น (รายงาน coverage)

รัน:  python ops/backtest_iv.py            (ดึงข้อมูลครั้งแรก ~30–60 นาที แคชไว้ใน data/backtest/)
      python ops/backtest_iv.py --no-fetch  (คำนวณใหม่จากแคช)
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "ops"))

import httpx  # noqa: E402

from app import edgar  # noqa: E402
from app.consensus_value import (DISCOUNT_US, GROWTH_CAP, combine, gather_inputs, model_dcf,  # noqa: E402
                                 model_earnings_power, model_justified_pb, revenue_cagr,
                                 reverse_dcf_growth)
from app.financials import _SHARES, _entries  # noqa: E402
from app.revenue_model import _annual_shares  # noqa: E402
from app.sp500_history import _year_end_membership  # noqa: E402
from megatrend_screen import quality  # noqa: E402

CACHE = ROOT / "data" / "backtest"
PRICES = CACHE / "prices"
OUT_ROWS = CACHE / "iv_backtest_rows.json"
OUT_SUMMARY = CACHE / "iv_backtest_summary.json"
FIRST_YEAR, LAST_YEAR = 2013, 2026
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; research-backtest/1.0)"}


# ── ดึง/แคชข้อมูล ────────────────────────────────────────────────────────────────

async def fetch_prices(sym: str, client: httpx.AsyncClient) -> dict | None:
    f = PRICES / f"{sym}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8")) or None
    ysym = sym.replace(".", "-")
    try:
        r = await client.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ysym}", headers=_HEADERS,
                             params={"interval": "1mo", "period1": int(dt.datetime(2008, 1, 1).timestamp()),
                                     "period2": int(dt.datetime.now().timestamp()),
                                     "events": "div,splits", "includeAdjustedClose": "true"}, timeout=30)
        res = (r.json().get("chart", {}).get("result") or [None])[0] if r.status_code == 200 else None
    except Exception:  # noqa: BLE001
        res = None
    data = {}
    if res and res.get("timestamp"):
        q = (res["indicators"].get("quote") or [{}])[0]
        adj = (res["indicators"].get("adjclose") or [{}])[0].get("adjclose") or []
        bars = {}
        for i, t in enumerate(res["timestamp"]):
            d = dt.datetime.utcfromtimestamp(t)
            c = (q.get("close") or [None] * len(res["timestamp"]))[i]
            a = adj[i] if i < len(adj) else None
            if c and a:
                bars[f"{d.year}-{d.month:02d}"] = [c, a]
        splits = [[dt.datetime.utcfromtimestamp(s["date"]).date().isoformat(), s["numerator"] / s["denominator"]]
                  for s in (res.get("events", {}).get("splits") or {}).values() if s.get("denominator")]
        data = {"bars": bars, "splits": splits}
    f.write_text(json.dumps(data), encoding="utf-8")
    return data or None


async def fetch_all(symbols: list[str]) -> None:
    PRICES.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(8)
    done = 0
    async with httpx.AsyncClient() as client:
        async def one(s):
            nonlocal done
            async with sem:
                await fetch_prices(s, client)
                try:
                    await edgar.get_company_facts(s)
                    await edgar.get_submissions(s)
                except Exception:  # noqa: BLE001 — ticker เก่าที่ไม่อยู่ใน SEC map แล้ว
                    pass
            done += 1
            if done % 50 == 0:
                print(f"  ดึงข้อมูล {done}/{len(symbols)}", flush=True)
        await asyncio.gather(*(one(s) for s in symbols))


# ── point-in-time ──────────────────────────────────────────────────────────────

def facts_as_of(facts: dict, cutoff: str) -> dict:
    """สำเนา facts ที่เหลือเฉพาะตัวเลขที่ยื่นแล้ว ณ วันตัด (filed ≤ cutoff)."""
    out = {"facts": {}}
    for taxo, concepts in facts.get("facts", {}).items():
        nc = {}
        for c, node in concepts.items():
            units = {u: [e for e in arr if e.get("filed", "9999") <= cutoff] for u, arr in node.get("units", {}).items()}
            units = {u: a for u, a in units.items() if a}
            if units:
                nc[c] = {"units": units}
        out["facts"][taxo] = nc
    return out


def shares_as_of(facts_pit: dict, cutoff: str, splits: list) -> float | None:
    """จำนวนหุ้นจากหน้าปกงบล่าสุด (dei) — รวมทุก class ในงบฉบับเดียวกัน แล้วปรับ split ที่เกิดหลังวันนับหุ้น."""
    arr = (facts_pit["facts"].get("dei", {}).get("EntityCommonStockSharesOutstanding", {})
           .get("units", {}).get("shares", []))
    if arr:
        last = max(arr, key=lambda e: (e["filed"], e["end"]))
        same = {e["val"] for e in arr if e.get("accn") == last.get("accn") and e["end"] == last["end"]}
        sh = float(sum(same))
    else:
        # บางบริษัท (เช่น Alphabet) ไม่ติดแท็กหน้าปก → ใช้จำนวนหุ้นถัวเฉลี่ย (diluted) งวดล่าสุดแทน
        ent = [e for e in _entries(facts_pit, _SHARES, "shares") if e.get("val")]
        if not ent:
            return None
        last = max(ent, key=lambda e: (e["end"], e.get("filed", "")))
        sh = float(last["val"])
    for d, ratio in splits:
        if last["end"] < d <= cutoff:
            sh *= ratio
    return sh


def price_as_of(px: dict, ym: str) -> tuple[float, float] | None:
    """(ราคาปิดจริงไม่ปรับ split, adjclose) ของเดือน ym."""
    b = px["bars"].get(ym)
    if not b:
        return None
    close_adj, adj = b
    date_ = f"{ym}-28"
    factor = 1.0
    for d, ratio in px["splits"]:
        if d > date_:
            factor *= ratio  # Yahoo หารราคาย้อนหลังด้วย split ที่เกิดทีหลัง → คูณกลับเป็นราคาจริง
    return close_adj * factor, adj


def profile_from_sic(sic) -> str:
    s = str(sic or "")
    if s.startswith("60") or s == "6712":
        return "bank"
    if s.startswith(("63", "64")):
        return "insurance"
    if s == "6798":
        return "reit"
    return "general"


# ── รันโมเดล ณ วันตัด ────────────────────────────────────────────────────────────

def evaluate(facts: dict, sic, px: dict, year: int) -> dict | None:
    cutoff = f"{year - 1}-12-31"
    profile = profile_from_sic(sic)
    if profile == "reit":
        return None
    pit = facts_as_of(facts, cutoff)
    inp = gather_inputs(pit)
    if inp["unit"] != "USD" or not inp["data_end"]:
        return None
    if (dt.date.fromisoformat(cutoff) - dt.date.fromisoformat(inp["data_end"])).days > 450:
        return None  # งบเก่าเกิน — บริษัทน่าจะหยุดยื่นแล้ว
    p0 = price_as_of(px, f"{year - 1}-12")
    end_ym = f"{year}-12" if f"{year}-12" in px["bars"] else max(px["bars"])
    if not p0 or end_ym <= f"{year - 1}-12":
        return None
    p1 = px["bars"][end_ym][1]
    sh = shares_as_of(pit, cutoff, px["splits"])
    if not sh:
        return None
    mcap = p0[0] * sh
    cagr = revenue_cagr(inp["revenue"])
    g = max(GROWTH_CAP[0], min(GROWTH_CAP[1], cagr if cagr is not None else 0.03))
    r = DISCOUNT_US
    if profile in ("bank", "insurance"):
        models = [model_justified_pb(inp, r), model_earnings_power(inp, min(g, 0.10))]
    else:
        models = [model_dcf(inp, g, r), model_earnings_power(inp, g)]
    models = [m for m in models if m]
    res = combine(models, mcap, p0[0])
    if not res:
        return None
    dcf = next((m for m in models if m["model"].startswith("DCF")), None)
    implied = reverse_dcf_growth(dcf["base_fcf"], mcap, r, inp["cash"] - inp["debt"]) if dcf else None
    q = quality(inp, _annual_shares(pit), profile)
    return {
        "year": year, "price": round(p0[0], 2), "mcap": mcap,
        "value_ratio": round(res["fair_price"] / p0[0], 3),  # Fair ÷ ราคา (>1 = ต่ำกว่ามูลค่า)
        "below_low": p0[0] < res["low"], "grade": res["grade"], "models": res["models_used"],
        "implied_g": implied, "hist_g": cagr,
        "gap": (cagr - implied) if (cagr is not None and implied is not None) else None,
        "long_term": q["long_term"], "q_passed": q["passed"],
        "ret": p1 / p0[1] - 1, "ret_end": end_ym,
    }


# ── สรุปผล ─────────────────────────────────────────────────────────────────────

def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def quintiles(rows: list[dict], key: str) -> list[list[dict]]:
    rs = sorted([r for r in rows if r.get(key) is not None], key=lambda r: r[key])
    n = len(rs)
    return [rs[i * n // 5:(i + 1) * n // 5] for i in range(5)] if n >= 25 else []


def summarize(rows: list[dict], bench: dict[int, float], coverage: dict[int, dict]) -> dict:
    years = sorted({r["year"] for r in rows})
    strategies = {
        "universe": ("ทุกตัวที่ประเมินได้ (ถัวเท่ากัน)", lambda r, yr: True),
        "q_cheap": ("ถูกสุด 20% (Fair÷ราคา สูงสุด)", None),
        "q_exp": ("แพงสุด 20%", None),
        "gap_top": ("ส่วนต่างความคาดหวังสูงสุด 20%", None),
        "gap_bottom": ("ส่วนต่างความคาดหวังต่ำสุด 20%", None),
        "long_term": ("ผ่านคุณภาพ 'ถือยาวได้'", lambda r, yr: r["long_term"]),
        "not_long_term": ("ไม่ผ่านคุณภาพ", lambda r, yr: not r["long_term"]),
        "shortlist": ("ถือยาวได้ + เกรด A/B + ราคา ≤ Fair", lambda r, yr: r["long_term"] and r["grade"] != "C" and r["value_ratio"] >= 1),
        "cheap_A": ("ถูกสุด 20% เฉพาะเกรด A", None),
        "cheap_C": ("ถูกสุด 20% เฉพาะเกรด C", None),
    }
    per_year: dict[str, dict[int, dict]] = {k: {} for k in strategies}
    for y in years:
        yr = [r for r in rows if r["year"] == y]
        vq, gq = quintiles(yr, "value_ratio"), quintiles(yr, "gap")
        picks = {
            "q_cheap": vq[4] if vq else [], "q_exp": vq[0] if vq else [],
            "gap_top": gq[4] if gq else [], "gap_bottom": gq[0] if gq else [],
            "cheap_A": [r for r in (vq[4] if vq else []) if r["grade"] == "A"],
            "cheap_C": [r for r in (vq[4] if vq else []) if r["grade"] == "C"],
        }
        for k, (_, fn) in strategies.items():
            sel = picks[k] if fn is None else [r for r in yr if fn(r, y)]
            m = _mean([r["ret"] for r in sel])
            per_year[k][y] = {"ret": m, "n": len(sel), "tickers": sorted(r["sym"] for r in sel)[:60]}

    def stats(k):
        ys = [y for y in years if per_year[k][y]["ret"] is not None and per_year[k][y]["n"] >= 3]
        if not ys:
            return None
        cum = 1.0
        for y in ys:
            cum *= 1 + per_year[k][y]["ret"]
        vs_b = [per_year[k][y]["ret"] - bench[y] for y in ys if y in bench]
        vs_u = [per_year[k][y]["ret"] - per_year["universe"][y]["ret"] for y in ys]
        full = [y for y in ys if y < LAST_YEAR]
        cagr = None
        if full:
            c = 1.0
            for y in full:
                c *= 1 + per_year[k][y]["ret"]
            cagr = c ** (1 / len(full)) - 1
        return {
            "years": len(ys), "cagr_full_years": cagr, "cumulative": cum - 1,
            "avg_excess_vs_sp500": _mean(vs_b), "beat_sp500_years": sum(x > 0 for x in vs_b),
            "avg_excess_vs_universe": _mean(vs_u), "beat_universe_years": sum(x > 0 for x in vs_u),
            "worst_year_vs_sp500": min(vs_b) if vs_b else None, "avg_n": _mean([per_year[k][y]["n"] for y in ys]),
        }

    return {
        "years": years, "benchmark": {str(y): bench.get(y) for y in years},
        "coverage": {str(y): coverage[y] for y in years},
        "strategies": {k: {"label": lab, "stats": stats(k),
                           "per_year": {str(y): v for y, v in per_year[k].items()}}
                       for k, (lab, _) in strategies.items()},
        "assumptions": {"discount": DISCOUNT_US, "growth_cap": GROWTH_CAP[1], "rebalance": "ปีละครั้ง ณ สิ้นธ.ค.",
                        "weighting": "ถัวน้ำหนักเท่ากัน", "costs": "ไม่หักค่าธรรมเนียม/ภาษี"},
    }


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true")
    args = ap.parse_args()
    mem = _year_end_membership()
    universe = sorted(set().union(*(mem.get(y - 1, set()) for y in range(FIRST_YEAR, LAST_YEAR + 1))))
    print(f"จักรวาล {len(universe)} ticker (สมาชิก S&P 500 ณ สิ้นปี {FIRST_YEAR - 1}–{LAST_YEAR - 1})", flush=True)
    if not args.no_fetch:
        await fetch_all(universe + ["^SP500TR"])

    async with httpx.AsyncClient() as client:
        bpx = await fetch_prices("^SP500TR", client)
    bench = {}
    for y in range(FIRST_YEAR, LAST_YEAR + 1):
        a, b = bpx["bars"].get(f"{y - 1}-12"), bpx["bars"].get(f"{y}-12") or bpx["bars"][max(bpx["bars"])]
        if a and b:
            bench[y] = b[1] / a[1] - 1

    rows, coverage = [], {}
    for y in range(FIRST_YEAR, LAST_YEAR + 1):
        members = sorted(mem.get(y - 1, set()))
        ok = 0
        for s in members:
            px_file = PRICES / f"{s}.json"
            px = json.loads(px_file.read_text(encoding="utf-8")) if px_file.exists() else None
            if not px:
                continue
            try:
                facts = await edgar.get_company_facts(s)
                sic = (await edgar.get_submissions(s)).get("sic")
                r = evaluate(facts, sic, px, y)
            except Exception:  # noqa: BLE001
                r = None
            if r:
                r["sym"] = s
                rows.append(r)
                ok += 1
        coverage[y] = {"members": len(members), "valued": ok}
        print(f"  {y}: ประเมินได้ {ok}/{len(members)}", flush=True)

    CACHE.mkdir(parents=True, exist_ok=True)
    OUT_ROWS.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    summ = summarize(rows, bench, coverage)
    OUT_SUMMARY.write_text(json.dumps(summ, ensure_ascii=False, indent=1), encoding="utf-8")
    for k, v in summ["strategies"].items():
        s = v["stats"]
        if s:
            print(f"{v['label']:<40} CAGR {s['cagr_full_years'] * 100:6.1f}%  เกิน S&P {s['avg_excess_vs_sp500'] * 100:+5.1f}%/ปี "
                  f"(ชนะ {s['beat_sp500_years']}/{s['years']} ปี)  เกินค่าเฉลี่ยจักรวาล {s['avg_excess_vs_universe'] * 100:+5.1f}%  n≈{s['avg_n']:.0f}")
    print(f"→ {OUT_SUMMARY}")


if __name__ == "__main__":
    asyncio.run(main())
