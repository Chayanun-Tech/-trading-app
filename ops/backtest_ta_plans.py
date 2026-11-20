"""Backtest แบบ "ขั้นบันได": RSI+MACD = จังหวะลงเงินปกติ · ครบ 3 เงื่อนไข (แตะ EMA200) = โอกาสทอง ลงหนักกว่า

ใช้ราคารายวันที่แคชไว้จาก ops/backtest_ta_dip.py (รันตัวนั้นก่อน) · เงินเข้าเดือนละ 1 หน่วยตั้งแต่ ม.ค. 2006 · ถือยาว
แผนที่ทดสอบ (เทียบกับ DCA 100% ทุกต้นเดือนเสมอ):
  P1 ขั้นบันได         เก็บเงินสด → RSI+MACD ลง 50% ของเงินสดที่มี · โอกาสทอง ลง 100%
  P2 ขั้นบันได (หนักขึ้น) เก็บเงินสด → RSI+MACD ลง 70% · โอกาสทอง ลง 100%
  P3 DCA 80% + กองสำรอง 20% → กองสำรองลงเมื่อเกิด RSI+MACD หรือโอกาสทอง
  P4 DCA 70% + กองสำรอง 30% → กองสำรองลงเฉพาะโอกาสทอง
  P5 DCA 70% + กองสำรอง 30% → ลงสำรองทั้งหมดเมื่อเกิดสัญญาณใดก็ได้
  P6 DCA 70% + กองสำรอง 30% → RSI+MACD ลงครึ่งของสำรอง · โอกาสทองลงทั้งหมด
ทดสอบ 2 กรณีดอกเบี้ยเงินสดระหว่างรอ: 0% และ 4%/ปี (พักในกองทุนตลาดเงิน)

รัน:  python ops/backtest_ta_plans.py
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ops"))
sys.path.insert(0, str(ROOT / "backend"))

from backtest_ta_dip import CACHE, START, signals  # noqa: E402

OUT = ROOT / "data" / "backtest" / "ta_plans_summary.json"
PLANS = {
    "P1": "ขั้นบันได: RSI+MACD ลง 50% ของเงินสด · โอกาสทองลงหมด",
    "P2": "ขั้นบันได: RSI+MACD ลง 70% · โอกาสทองลงหมด",
    "P3": "DCA 80% + สำรอง 20% ลงเมื่อเกิดสัญญาณใดก็ได้",
    "P4": "DCA 70% + สำรอง 30% ลงเฉพาะโอกาสทอง",
    "P5": "DCA 70% + สำรอง 30% ลงหมดเมื่อเกิดสัญญาณใดก็ได้",
    "P6": "DCA 70% + สำรอง 30% · RSI+MACD ลงครึ่ง · โอกาสทองลงหมด",
}
_DCA_SHARE = {"P3": 0.8, "P4": 0.7, "P5": 0.7, "P6": 0.7}
_NORMAL_FRAC = {"P1": 0.5, "P2": 0.7, "P3": 1.0, "P4": 0.0, "P5": 1.0, "P6": 0.5}


def run(dates, closes, normal, golden, i0, plan, cash_rate):
    daily = (1 + cash_rate) ** (1 / 252) - 1
    sh = cash = 0.0
    dca_sh = 0.0
    month = None
    for i in range(i0, len(dates)):
        cash *= 1 + daily
        if dates[i][:7] != month:
            month = dates[i][:7]
            dca_sh += 1 / closes[i]
            if plan in ("P1", "P2"):
                cash += 1
            else:
                sh += _DCA_SHARE[plan] / closes[i]
                cash += 1 - _DCA_SHARE[plan]
        frac = 0.0
        if golden[i]:
            frac = 1.0
        elif normal[i]:
            frac = _NORMAL_FRAC[plan]
        if frac and cash > 0:
            sh += cash * frac / closes[i]
            cash -= cash * frac
    return (sh * closes[-1] + cash) / (dca_sh * closes[-1])


def main() -> None:
    files = sorted(CACHE.glob("*.json"))
    res = {f"{p}@{r}": [] for p in PLANS for r in (0.0, 0.04)}
    idx = {}
    for f in files:
        bars = json.loads(f.read_text(encoding="utf-8"))
        if not bars:
            continue
        dates, closes = [b[0] for b in bars], [b[1] for b in bars]
        i0 = next((i for i, d in enumerate(dates) if d >= START), None)
        if i0 is None or i0 < 220 or len(dates) - i0 < 750:
            continue
        sig = signals(closes)
        for p in PLANS:
            for r in (0.0, 0.04):
                v = run(dates, closes, sig["rsi_macd"], sig["all3"], i0, p, r)
                if f.stem in ("SPY", "QQQ"):
                    idx[f"{f.stem} {p}@{r}"] = v
                else:
                    res[f"{p}@{r}"].append(v)
    summ = {}
    for k, xs in res.items():
        p, r = k.split("@")
        summ[k] = {"plan": PLANS[p], "cash_rate": float(r), "stocks": len(xs),
                   "median_vs_dca": statistics.median(xs), "beat_dca_share": sum(x > 1 for x in xs) / len(xs),
                   "p10": sorted(xs)[len(xs) // 10], "p90": sorted(xs)[len(xs) * 9 // 10]}
        print(f"{PLANS[p]:<48} ดอกเบี้ยเงินสด {float(r) * 100:.0f}% · มูลค่าเทียบ DCA {summ[k]['median_vs_dca']:.3f}x · "
              f"ชนะ DCA {summ[k]['beat_dca_share'] * 100:.0f}% ของหุ้น · ช่วง 10–90% {summ[k]['p10']:.2f}–{summ[k]['p90']:.2f}x")
    for k, v in idx.items():
        print(f"  {k}: {v:.3f}x")
    OUT.write_text(json.dumps({"summary": summ, "index": idx}, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
