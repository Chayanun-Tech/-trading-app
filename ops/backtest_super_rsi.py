"""Backtest สัญญาณของ Super RSI (tradingview/super_rsi.pine) — ตรรกะเดียวกับ Pine ทุกเงื่อนไข

สัญญาณที่ทดสอบ (เกิด ณ แท่งปิด ซื้อราคาปิดวันนั้น):
  ทอง        RSI(14) ลงถึง ≤35 ใน 5 แท่ง + RSI ตัดเส้นสัญญาณ (EMA9 ของ RSI) ขึ้น + MACD hist เพิ่มขึ้น + ราคาห่าง EMA200 ≤3%
  ซื้อ        เหมือนทอง แต่ราคาไม่ใกล้ EMA200
  ย่อในขาขึ้น  ขาขึ้น (ราคา > EMA200 และ EMA200 สูงกว่า 20 แท่งก่อน) + RSI ลงถึง 35–42 + ตัดขึ้น + MACD ฟื้น
  Bull Div    pivot low ของ RSI (ซ้าย 5/ขวา 5) ห่าง pivot ก่อน 5–60 แท่ง · ราคาต่ำลงแต่ RSI สูงขึ้น (นับวันยืนยัน = pivot+5)
  Bear Div    กลับด้าน — สัญญาณ "ระวังขาลง" ยิ่งผลตอบแทนหลังสัญญาณต่ำกว่าปกติ ยิ่งแปลว่าแม่น
วัด: ผลตอบแทนหลังสัญญาณ 20/60/120/250 วันทำการ เทียบกับ "วันไหนก็ได้" ของหุ้นตัวเดียวกัน (ส่วนต่าง = edge)
ข้อมูล: ราคารายวันปรับปันผลจาก data/backtest/daily (สร้างโดย backtest_ta_dip.py) ตั้งแต่ 2004
ข้อจำกัด: ใช้ราคาปิดแทน high/low ใน divergence (แคชมีแต่ราคาปิด) · จักรวาลคือสมาชิก S&P 500 วันนี้

รัน:  python ops/backtest_super_rsi.py
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.indicators import ema, macd, rsi  # noqa: E402

CACHE = ROOT / "data" / "backtest" / "daily"
OUT = ROOT / "data" / "backtest" / "super_rsi_summary.json"
HORIZONS = {"20d": 20, "60d": 60, "120d": 120, "250d": 250}
PL = PR = 5
RANGE = (5, 60)
NAMES = {"golden": "💎 ทอง", "normal": "🔺 ซื้อ", "trend_dip": "● ย่อในขาขึ้น",
         "bull_div": "Bull Divergence", "bear_div": "Bear Divergence (สัญญาณระวัง)",
         "bull_div_near_ema": "Bull Div ใกล้ EMA200"}


def pivots(r: list, low: bool) -> list[int]:
    """index ของ pivot (ต่ำสุด/สูงสุดเทียบซ้าย PL ขวา PR) — ยืนยันได้ที่ index + PR."""
    out = []
    for p in range(PL, len(r) - PR):
        v = r[p]
        if v is None:
            continue
        win = r[p - PL:p] + r[p + 1:p + PR + 1]
        if any(x is None for x in win):
            continue
        if (low and all(v < x for x in win)) or (not low and all(v > x for x in win)):
            out.append(p)
    return out


def signals(c: list[float]) -> dict[str, list[int]]:
    n = len(c)
    r, e = rsi(c, 14), ema(c, 200)
    rs = [None] * n
    vals = [x for x in r if x is not None]
    k0 = next(i for i, x in enumerate(r) if x is not None) if vals else n
    es = ema(vals, 9)
    for j, v in enumerate(es):
        rs[k0 + j] = v
    _, _, h = macd(c)
    out = {k: [] for k in NAMES}
    for i in range(225, n):
        if None in (r[i], r[i - 1], rs[i], rs[i - 1], e[i], e[i - 20], h[i], h[i - 1]):
            continue
        low5 = min(x for x in r[i - 4:i + 1])
        cross = r[i - 1] <= rs[i - 1] and r[i] > rs[i]
        mturn = h[i] > h[i - 1]
        near = abs(c[i] / e[i] - 1) <= 0.03
        if low5 <= 35 and cross and mturn:
            out["golden" if near else "normal"].append(i)
        up = c[i] > e[i] and e[i] > e[i - 20]
        if up and 35 < low5 <= 42 and cross and mturn:
            out["trend_dip"].append(i)
    for low, key in ((True, "bull_div"), (False, "bear_div")):
        ps = pivots(r, low)
        for a, b in zip(ps, ps[1:]):
            gap = b - a
            if not (RANGE[0] <= gap <= RANGE[1]):
                continue
            conf = b + PR
            if conf >= n or conf < 225:
                continue
            if low and r[b] > r[a] and c[b] < c[a]:
                out[key].append(conf)
                if e[conf] and abs(c[conf] / e[conf] - 1) <= 0.03:
                    out["bull_div_near_ema"].append(conf)
            if not low and r[b] < r[a] and c[b] > c[a]:
                out[key].append(conf)
    return out


def main() -> None:
    files = [f for f in sorted(CACHE.glob("*.json")) if f.stem not in ("SPY", "QQQ")]
    agg = {k: {h: {"sig": [], "edge": []} for h in HORIZONS} for k in NAMES}
    counts = {k: 0 for k in NAMES}
    years_total = 0.0
    for f in files:
        bars = json.loads(f.read_text(encoding="utf-8"))
        if len(bars) < 1500:
            continue
        c = [b[1] for b in bars]
        years_total += len(c) / 252
        sig = signals(c)
        for hname, hd in HORIZONS.items():
            base = [c[i + hd] / c[i] - 1 for i in range(225, len(c) - hd)]
            if not base:
                continue
            bmed = statistics.median(base)
            for k, idx in sig.items():
                rets = [c[i + hd] / c[i] - 1 for i in idx if i + hd < len(c)]
                agg[k][hname]["sig"].extend(rets)
                agg[k][hname]["edge"].extend(x - bmed for x in rets)
        for k, idx in sig.items():
            counts[k] += len(idx)

    summary = {}
    print(f"หุ้น {len(files)} ตัว · รวม ~{years_total:,.0f} หุ้น-ปี")
    for k, lab in NAMES.items():
        row = {"label": lab, "signals": counts[k], "per_stock_year": counts[k] / years_total}
        line = f"{lab:<30} {counts[k]:>6} ครั้ง ({row['per_stock_year']:.2f}/หุ้น/ปี)"
        for h in HORIZONS:
            s, e = agg[k][h]["sig"], agg[k][h]["edge"]
            if not s:
                continue
            row[h] = {"median_ret": statistics.median(s), "win_rate": sum(x > 0 for x in s) / len(s),
                      "median_edge": statistics.median(e), "beat_base_share": sum(x > 0 for x in e) / len(e),
                      "worst_10pct": sorted(s)[len(s) // 10]}
            line += f" | {h}: กลาง {row[h]['median_ret'] * 100:+5.1f}% ชนะ {row[h]['win_rate'] * 100:3.0f}% edge {row[h]['median_edge'] * 100:+4.1f}%"
        summary[k] = row
        print(line)
    OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ {OUT}")


if __name__ == "__main__":
    main()
