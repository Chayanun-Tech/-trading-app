"""จำลองการลงทุน "วิถี Buffett" แบบกฎชัดเจน แล้ว backtest 2013–2026 (point-in-time — ไม่มองอนาคต)

กฎ (แปลงจากสิ่งที่ Buffett/Munger ทำจริง):
  1. ธุรกิจยอดเยี่ยม      ผ่านคุณภาพครบ 6/6 (สร้างเงินสด · สม่ำเสมอ · ยังโต · ROE ดี · หนี้น้อย · ไม่ออกหุ้นเจือจาง)
                          และโมเดลมูลค่าไม่ขัดกันรุนแรง (เกรด A/B)
  2. ราคาสมเหตุผล + ส่วนเผื่อความปลอดภัย   ซื้อเมื่อ มูลค่าที่แท้จริง ÷ ราคา ≥ 1.0
  3. ตลาดแพงทั้งกระดาน (มิติ "มูลค่า" ของ Market Climate < 20)  → ต้องการส่วนเผื่อมากขึ้น (≥ 1.25) และลงเงินแค่ครึ่งของเงินสด
     ตลาดกลัว (คะแนนรวม ≥ 70)                                → ผ่อนเกณฑ์เป็น ≥ 0.9 และทุ่มเงินสดทั้งหมด ("greedy when others are fearful")
  4. กระจุกตัว            ถือไม่เกิน 10 ตัว
  5. ถือยาว ขายน้อยมาก     ขายเฉพาะเมื่อธุรกิจเสื่อม (คุณภาพเหลือ ≤ 3/6) หรือแพงสุดขีด (ราคา > 2 เท่าของมูลค่า)
  6. เงินสดคือทางเลือก     ถ้าไม่มีของถูก เก็บเงินสด (ได้ดอกเบี้ยพันธบัตร 3 เดือนจริง) รอโอกาส
เงินเข้า: ปีละ 12 หน่วย ณ ต้นปี (ประมาณเงินเดือนละ 1) · ตัดสินใจปีละครั้ง ณ สิ้นธันวาคม
เทียบ: (ก) ซื้อ S&P 500 รวมปันผลด้วยเงินเท่ากัน (ข) ซื้อหุ้นทุกตัวที่ประเมินได้ ถัวเท่ากัน
ข้อมูล: data/backtest/iv_backtest_rows.json (จาก backtest_iv.py) · ราคา data/backtest/prices · บรรยากาศตลาด ops/market_climate.py
รัน: python ops/backtest_buffett_way.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ops"))

import market_climate as mc  # noqa: E402

ROWS = ROOT / "data" / "backtest" / "iv_backtest_rows.json"
PRICES = ROOT / "data" / "backtest" / "prices"
OUT = ROOT / "data" / "backtest" / "buffett_way_summary.json"
MAX_HOLD = 10


def year_return(sym: str, y: int, cache: dict) -> float | None:
    """ผลตอบแทนรวมปันผล ธ.ค. ปีก่อน → ธ.ค. ปีนี้ (ปีล่าสุดถึงเดือนล่าสุดที่มี)."""
    if sym not in cache:
        f = PRICES / f"{sym}.json"
        cache[sym] = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    bars = (cache[sym] or {}).get("bars") or {}
    a = bars.get(f"{y - 1}-12")
    b = bars.get(f"{y}-12") or (bars[max(bars)] if bars and max(bars) > f"{y - 1}-12" else None)
    return (b[1] / a[1] - 1) if a and b else None


def run(rules: dict, rows: list[dict], climate: dict, tbill: dict, bench: dict, verbose: bool = False) -> dict:
    by_year: dict[int, dict[str, dict]] = {}
    for r in rows:
        by_year.setdefault(r["year"], {})[r["sym"]] = r
    years = [y for y in sorted(by_year) if y >= rules.get("start", 0)]
    max_hold = rules.get("max_hold", MAX_HOLD)
    hold: dict[str, float] = {}
    cash = 0.0
    sp = ew = 0.0
    pcache: dict = {}
    log, values, cash_pct, n_hold, sells = [], [], [], [], 0
    for y in years:
        cash += 12
        sp += 12
        ew += 12
        comp, valg = climate.get(y, (50, 50))
        yr = by_year[y]
        # ── ขาย (น้อยที่สุด)
        for s in list(hold):
            r = yr.get(s)
            if r and (r["q_passed"] <= rules["sell_quality"] or r["value_ratio"] < rules["sell_overvalued"]):
                cash += hold.pop(s)
                sells += 1
        # ── เกณฑ์ตามบรรยากาศตลาด
        thr, deploy = rules["mos"], 1.0
        if rules["use_climate"] and valg is not None and valg < 20:
            thr, deploy = rules["mos_expensive"], rules["deploy_expensive"]
        if rules["use_climate"] and comp is not None and comp >= 70:
            thr, deploy = rules["mos_fear"], 1.0
        good = {s: r for s, r in yr.items()
                if r["long_term"] and r["q_passed"] >= rules["min_quality"] and r["grade"] != "C" and r["value_ratio"] >= thr}
        new = sorted((s for s in good if s not in hold), key=lambda s: -good[s]["value_ratio"])[:max(0, max_hold - len(hold))]
        targets = new + [s for s in hold if s in good]          # ซื้อตัวใหม่ + เติมตัวเดิมที่ยังถูก
        invest = cash * deploy if targets else 0.0
        for s in targets:
            hold[s] = hold.get(s, 0.0) + invest / len(targets)
        cash -= invest
        total = sum(hold.values()) + cash
        cash_pct.append(cash / total if total else 0)
        n_hold.append(len(hold))
        if verbose:
            log.append(f"{y}: คะแนนตลาด {comp:.0f} มูลค่าตลาด {valg:.0f} → เกณฑ์ ≥{thr} · ถือ {len(hold)} ตัว เงินสด {cash / total * 100:.0f}% · "
                       + ", ".join(sorted(hold)))
        # ── ผ่านไป 1 ปี
        for s in list(hold):
            r = yr.get(s)
            ret = r["ret"] if r else year_return(s, y, pcache)
            hold[s] *= 1 + (ret if ret is not None else 0.0)
        cash *= 1 + tbill.get(y, 0.0)
        sp *= 1 + bench[y]
        ew *= 1 + sum(r["ret"] for r in yr.values()) / len(yr)
        values.append((y, sum(hold.values()) + cash, sp, ew))
    final, spf, ewf = values[-1][1:]
    return {"final_vs_sp": final / spf, "final_vs_ew": final / ewf, "avg_cash": sum(cash_pct) / len(cash_pct),
            "avg_holdings": sum(n_hold) / len(n_hold), "sells": sells, "log": log,
            "values": [{"year": y, "buffett": v, "sp500": s, "equal_weight": e} for y, v, s, e in values],
            "contributed": 12 * len(years)}


def main() -> None:
    rows = json.loads(ROWS.read_text(encoding="utf-8"))
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
    base = {"mos": 1.0, "mos_expensive": 1.25, "deploy_expensive": 0.5, "mos_fear": 0.9,
            "min_quality": 6, "sell_quality": 3, "sell_overvalued": 0.5, "use_climate": True}
    keep = {**base, "sell_overvalued": -1}   # ขายเฉพาะธุรกิจเสื่อม ไม่ขายเพราะแพง
    variants = {
        "วิถี Buffett (เต็มกฎ)": base,
        "ขายเฉพาะธุรกิจเสื่อม · 10 ตัว": keep,
        "ขายเฉพาะธุรกิจเสื่อม · 15 ตัว": {**keep, "max_hold": 15},
        "ขายเฉพาะธุรกิจเสื่อม · 20 ตัว": {**keep, "max_hold": 20},
        "ขายเฉพาะธุรกิจเสื่อม · 15 ตัว · คุณภาพ ≥5": {**keep, "max_hold": 15, "min_quality": 5},
        "ขายเฉพาะธุรกิจเสื่อม · 15 ตัว · ไม่ใช้บรรยากาศ": {**keep, "max_hold": 15, "use_climate": False},
        "ไม่ใช้บรรยากาศตลาด": {**base, "use_climate": False},
        "เน้นราคาถูกกว่า (ส่วนเผื่อ ≥ 1.3)": {**base, "mos": 1.3, "mos_expensive": 1.5},
        "คุณภาพ ≥ 5/6": {**base, "min_quality": 5},
        "ไม่ขายเลย (ถือตลอด)": {**base, "sell_quality": -1, "sell_overvalued": -1},
    }
    res = {}
    for name, rules in variants.items():
        r = run(rules, rows, climate, tbill, bench, verbose=(name == "วิถี Buffett (เต็มกฎ)"))
        res[name] = r
        print(f"{name:<34} ปลายทาง ÷ S&P {r['final_vs_sp']:.3f}x · ÷ ถัวเท่ากัน {r['final_vs_ew']:.3f}x · "
              f"เงินสดเฉลี่ย {r['avg_cash'] * 100:.0f}% · ถือเฉลี่ย {r['avg_holdings']:.1f} ตัว · ขาย {r['sells']} ครั้ง")
    print("\nความทนทาน: เริ่มลงทุนคนละปี (ปลายทาง ÷ S&P)")
    for name in ("วิถี Buffett (เต็มกฎ)", "ขายเฉพาะธุรกิจเสื่อม · 10 ตัว", "ขายเฉพาะธุรกิจเสื่อม · 15 ตัว",
                 "ขายเฉพาะธุรกิจเสื่อม · 20 ตัว", "ไม่ขายเลย (ถือตลอด)"):
        out = []
        for st in (2013, 2015, 2017, 2019, 2021):
            r = run({**variants[name], "start": st}, rows, climate, tbill, bench)
            out.append(f"{st}: {r['final_vs_sp']:.2f}x")
        print(f"  {name:<34} " + " · ".join(out))
    print("\nบันทึกรายปี (เต็มกฎ):")
    for line in res["วิถี Buffett (เต็มกฎ)"]["log"]:
        print("  " + line)
    v = res["วิถี Buffett (เต็มกฎ)"]["values"]
    print("\nมูลค่าพอร์ต (ลงเงินรวม {}):".format(res["วิถี Buffett (เต็มกฎ)"]["contributed"]))
    for x in v:
        print(f"  {x['year']}: Buffett {x['buffett']:7.1f} · S&P {x['sp500']:7.1f} · ถัวเท่ากัน {x['equal_weight']:7.1f}")
    OUT.write_text(json.dumps({k: {kk: vv for kk, vv in r.items()} for k, r in res.items()}, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
