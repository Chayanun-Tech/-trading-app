"""ระยะ 2: Backtest การประเมินมูลค่าแบบรายตัว (valuation_v2) เทียบ v1 แยกตามประเภทธุรกิจ — point-in-time

ทุกปี Y (2013–2026) ยืน ณ 31 ธ.ค. Y−1: สมาชิก S&P 500 ณ วันนั้น · ใช้เฉพาะงบที่ยื่นแล้ว · ราคาจริง ณ วันนั้น
  v2 รันแบบ "ไม่ใช้ไฟล์กำหนดรายหุ้น" (archetype_overrides) เพราะไฟล์นั้นเขียนด้วยความรู้ปัจจุบัน = มองเห็นอนาคต
  แล้วรันอีกรอบ "ใช้ไฟล์" เพื่อดูว่าต่างกันแค่ไหน (เป็นข้อมูลประกอบ ไม่ใช่ผลหลัก)
วัดผล:
  1. IC (สหสัมพันธ์อันดับ) ระหว่าง มูลค่า÷ราคา กับ ผลตอบแทน 1 ปีข้างหน้า — คิดรายปีแล้วเฉลี่ย · v1 vs v2 บนหุ้นชุดเดียวกัน
  2. แยกตามประเภทธุรกิจ: IC · ถูก 40% vs แพง 40% · จำนวนตัวอย่าง
  3. ระดับความน่าเชื่อ (สูง/กลาง/ต่ำ) มีความหมายไหม (IC ของแต่ละระดับ)
  4. พอร์ตวิถี Buffett (กฎเดิม) ใช้มูลค่า v2 แทน v1
ข้อมูล: แคชจาก backtest_iv.py (data/backtest/prices + แคชงบ SEC) — ต้องรัน backtest_iv.py ก่อน
รัน: python ops/backtest_v2.py
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "ops"))

from app import edgar, valuation_v2  # noqa: E402
from app.consensus_value import gather_inputs  # noqa: E402
from app.revenue_model import _annual_shares  # noqa: E402
from app.sp500_history import _year_end_membership  # noqa: E402
from backtest_iv import FIRST_YEAR, LAST_YEAR, PRICES, facts_as_of, price_as_of, profile_from_sic, shares_as_of  # noqa: E402
from megatrend_screen import quality  # noqa: E402

OUT_ROWS = ROOT / "data" / "backtest" / "v2_rows.json"
OUT_SUM = ROOT / "data" / "backtest" / "v2_summary.json"
V1_ROWS = ROOT / "data" / "backtest" / "iv_backtest_rows.json"


def rank_ic(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 15:
        return None

    def ranks(v):
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        for k, i in enumerate(order):
            r[i] = k
        return r
    rx, ry = ranks(xs), ranks(ys)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx) ** 0.5
    vy = sum((b - my) ** 2 for b in ry) ** 0.5
    return cov / (vx * vy) if vx and vy else None


def avg_ic(rows: list[dict], key: str) -> dict:
    by = {}
    for r in rows:
        by.setdefault(r["year"], []).append(r)
    ics = []
    for y, rs in sorted(by.items()):
        ic = rank_ic([r[key] for r in rs], [r["ret"] for r in rs])
        if ic is not None:
            ics.append(ic)
    if not ics:
        return {"ic": None, "years": 0, "positive_years": 0}
    return {"ic": statistics.mean(ics), "years": len(ics), "positive_years": sum(i > 0 for i in ics)}


def spread(rows: list[dict], key: str) -> float | None:
    """ผลตอบแทนเฉลี่ยรายปีของ ถูกสุด 40% − แพงสุด 40% (ถัวรายปี)."""
    by = {}
    for r in rows:
        by.setdefault(r["year"], []).append(r)
    diffs = []
    for rs in by.values():
        if len(rs) < 10:
            continue
        rs = sorted(rs, key=lambda r: r[key])
        k = max(2, int(len(rs) * 0.4))
        diffs.append(statistics.mean(r["ret"] for r in rs[-k:]) - statistics.mean(r["ret"] for r in rs[:k]))
    return statistics.mean(diffs) if diffs else None


async def build_rows() -> list[dict]:
    mem = _year_end_membership()
    v1 = {(r["year"], r["sym"]): r for r in json.loads(V1_ROWS.read_text(encoding="utf-8"))}
    ov = valuation_v2.load_overrides()
    rows = []
    for y in range(FIRST_YEAR, LAST_YEAR + 1):
        cutoff = f"{y - 1}-12-31"
        asof = dt.date(y - 1, 12, 31)
        n_ok = 0
        for s in sorted(mem.get(y - 1, set())):
            f = PRICES / f"{s}.json"
            px = json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
            if not px:
                continue
            try:
                facts = await edgar.get_company_facts(s)
                sic = (await edgar.get_submissions(s)).get("sic")
            except Exception:  # noqa: BLE001
                continue
            p0 = price_as_of(px, f"{y - 1}-12")
            end_ym = f"{y}-12" if f"{y}-12" in px["bars"] else max(px["bars"])
            if not p0 or end_ym <= f"{y - 1}-12":
                continue
            pit = facts_as_of(facts, cutoff)
            try:
                inp = gather_inputs(pit)
            except Exception:  # noqa: BLE001
                continue
            if inp["unit"] != "USD" or not inp["data_end"] or (asof - dt.date.fromisoformat(inp["data_end"])).days > 450:
                continue
            sh = shares_as_of(pit, cutoff, px["splits"])
            if not sh:
                continue
            mcap = p0[0] * sh
            profile = profile_from_sic(sic)
            try:
                r2 = valuation_v2.evaluate(pit, sic, profile, p0[0], mcap, None, symbol=s, overrides={}, as_of=asof)
                r2o = valuation_v2.evaluate(pit, sic, profile, p0[0], mcap, None, symbol=s, overrides=ov, as_of=asof) if s in ov else r2
            except Exception:  # noqa: BLE001
                continue
            if not r2:
                continue
            o = v1.get((y, s))
            if o:
                lt, qp = o["long_term"], o["q_passed"]
            else:
                q = quality(inp, _annual_shares(pit), profile)
                lt, qp = q["long_term"], q["passed"]
            rows.append({
                "year": y, "sym": s, "ret": px["bars"][end_ym][1] / p0[1] - 1,
                "vr2": r2["fair_price"] / p0[0], "vr2_ov": (r2o or r2)["fair_price"] / p0[0],
                "arche": r2["archetype"], "conf": r2["confidence"], "conf_level": r2["confidence_level"],
                "vr1": o["value_ratio"] if o else None, "grade1": o["grade"] if o else None,
                "long_term": lt, "q_passed": qp,
            })
            n_ok += 1
        print(f"  {y}: ประเมิน v2 ได้ {n_ok}", flush=True)
    OUT_ROWS.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return rows


def analyze(rows: list[dict]) -> dict:
    out = {}
    both = [r for r in rows if r["vr1"] is not None]
    out["overall"] = {"n": len(rows), "n_both": len(both),
                      "v1": {**avg_ic(both, "vr1"), "spread": spread(both, "vr1")},
                      "v2": {**avg_ic(both, "vr2"), "spread": spread(both, "vr2")},
                      "v2_all": {**avg_ic(rows, "vr2"), "spread": spread(rows, "vr2")},
                      "v2_with_overrides": {**avg_ic(rows, "vr2_ov"), "spread": spread(rows, "vr2_ov")}}
    out["by_archetype"] = {}
    for a in valuation_v2.LABEL:
        rs = [r for r in rows if r["arche"] == a]
        if len(rs) < 30:
            out["by_archetype"][a] = {"n": len(rs)}
            continue
        b = [r for r in rs if r["vr1"] is not None]
        out["by_archetype"][a] = {"n": len(rs), "v2": {**avg_ic(rs, "vr2"), "spread": spread(rs, "vr2")},
                                  "v1_same_stocks": {**avg_ic(b, "vr1"), "spread": spread(b, "vr1")} if len(b) >= 30 else None}
    out["by_confidence"] = {lv: {"n": len([r for r in rows if r["conf_level"] == lv]),
                                 **avg_ic([r for r in rows if r["conf_level"] == lv], "vr2")}
                            for lv in ("สูง", "กลาง", "ต่ำ")}
    return out


def buffett_way(rows: list[dict]) -> dict:
    """พอร์ตวิถี Buffett (กฎเดิม) ใช้มูลค่า v2 · เกรด = ระดับความน่าเชื่อ (สูง→A กลาง→B ต่ำ→C)."""
    import backtest_buffett_way as bw
    import market_climate as mc
    conv = [{"year": r["year"], "sym": r["sym"], "value_ratio": r["vr2"], "long_term": r["long_term"],
             "q_passed": r["q_passed"], "grade": {"สูง": "A", "กลาง": "B", "ต่ำ": "C"}[r["conf_level"]], "ret": r["ret"]}
            for r in rows]
    v1conv = json.loads(V1_ROWS.read_text(encoding="utf-8"))
    df = mc.build()
    sc = mc.score(df)
    climate, tbill, bench = {}, {}, {}
    for y in range(2013, 2027):
        at = sc[sc.index < f"{y}-01-01"].iloc[-1]
        climate[y] = (float(at["รวม"]), float(at["มูลค่า"]))
        yr = df[(df.index >= f"{y}-01-01") & (df.index < f"{y + 1}-01-01")]
        tbill[y] = float(yr["tbill"].mean()) / 100 * (len(yr) / 252) if len(yr) else 0.0
        p0 = df["tr"][df.index < f"{y}-01-01"].iloc[-1]
        bench[y] = float(yr["tr"].iloc[-1] / p0 - 1)
    rules = {"mos": 1.0, "mos_expensive": 1.25, "deploy_expensive": 0.5, "mos_fear": 0.9, "min_quality": 6,
             "sell_quality": 3, "sell_overvalued": -1, "use_climate": True, "max_hold": 20}
    res = {}
    for name, rs in (("v1", v1conv), ("v2", conv)):
        res[name] = {st: round(bw.run({**rules, "start": st}, rs, climate, tbill, bench)["final_vs_sp"], 3)
                     for st in (2013, 2015, 2017, 2019, 2021)}
    return res


def main() -> None:
    rows = json.loads(OUT_ROWS.read_text(encoding="utf-8")) if "--no-build" in sys.argv and OUT_ROWS.exists() \
        else asyncio.run(build_rows())
    summ = analyze(rows)
    summ["buffett_way_vs_sp500"] = buffett_way(rows)
    OUT_SUM.write_text(json.dumps(summ, ensure_ascii=False, indent=1), encoding="utf-8")
    f = lambda x: "  –  " if x is None else f"{x:+.3f}"  # noqa: E731
    o = summ["overall"]
    print(f"\nรวม (หุ้นชุดเดียวกัน n={o['n_both']}):  v1 IC {f(o['v1']['ic'])} (บวก {o['v1']['positive_years']}/{o['v1']['years']} ปี) "
          f"ถูก−แพง {f(o['v1']['spread'])}  |  v2 IC {f(o['v2']['ic'])} (บวก {o['v2']['positive_years']}/{o['v2']['years']}) ถูก−แพง {f(o['v2']['spread'])}")
    print(f"v2 ทุกตัว (n={o['n']}): IC {f(o['v2_all']['ic'])} · ถ้าใช้ไฟล์กำหนดรายหุ้น IC {f(o['v2_with_overrides']['ic'])}")
    print("\nแยกตามประเภท:")
    for a, d in summ["by_archetype"].items():
        if "v2" not in d:
            print(f"  {valuation_v2.LABEL[a]:<26} n={d['n']:<5} (น้อยเกินวัดผล)")
            continue
        v1s = d["v1_same_stocks"]
        print(f"  {valuation_v2.LABEL[a]:<26} n={d['n']:<5} v2 IC {f(d['v2']['ic'])} (บวก {d['v2']['positive_years']}/{d['v2']['years']}) "
              f"ถูก−แพง {f(d['v2']['spread'])}  |  v1 IC {f(v1s['ic']) if v1s else '  –  '} ถูก−แพง {f(v1s['spread']) if v1s else '  –  '}")
    print("\nระดับความน่าเชื่อ (v2):")
    for lv, d in summ["by_confidence"].items():
        print(f"  {lv:<5} n={d['n']:<5} IC {f(d['ic'])} (บวก {d['positive_years']}/{d['years']} ปี)")
    print("\nพอร์ตวิถี Buffett (20 ตัว ขายเฉพาะธุรกิจเสื่อม) ปลายทาง ÷ S&P 500 เริ่มต่างปี:")
    for k, v in summ["buffett_way_vs_sp500"].items():
        print(f"  {k}: " + " · ".join(f"{y}: {x:.2f}x" for y, x in v.items()))


if __name__ == "__main__":
    main()
