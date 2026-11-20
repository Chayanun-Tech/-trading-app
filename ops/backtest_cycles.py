"""Backtest วิธีนับรอบ/แบ่งช่วงตลาด หลายแบบ เพื่อหาวิธีที่ได้ผลที่สุดสำหรับนักลงทุนระยะยาว

แต่ละวิธีให้ "สถานะ" รายวันแบบไม่มองอนาคต: 1 = อยู่ในขาขึ้นใหญ่ (ถือหุ้น) · 0 = ขาลงใหญ่ (ถือเงินสด)
วัดผล 2 มุม:
  A) ใช้เป็นตัวกำหนดว่าจะถือหรือไม่ (เปลี่ยนสถานะ → ทำตามที่ราคาปิดวันถัดไป) เทียบกับซื้อแล้วถือ
     CAGR · Max Drawdown · Calmar (CAGR ÷ |MaxDD|) · % เวลาในตลาด · จำนวนการสลับต่อปี
  B) คุณภาพสัญญาณ: หลัง "ยืนยันก้นใหญ่" ราคาไปต่อดีกว่าปกติไหม (60/250 วัน) · หลัง "ยืนยันยอดใหญ่" แย่กว่าปกติไหม
วิธีที่ทดสอบ:
  zz15 / zz20 / zz25      ZigZag % คงที่ (v1 · กฎตลาดหมี 20%)
  v2_a{6,8,10,12}         Cycle v2: เกณฑ์ปรับตามความผันผวน + ต้องโครงสร้างเปลี่ยน (CHoCH) — ตัวเลข = ตัวคูณรอบใหญ่
  v2noC_10                v2 ไม่ต้อง CHoCH (เกณฑ์ผันผวนอย่างเดียว)
  ema200                  ราคาเหนือ/ใต้ EMA200
  stage                   Weinstein Stage: เข้าเมื่อเหนือ EMA200 และ EMA200 ชันขึ้น · ออกเมื่อหลุด EMA200
  v2_10+ema               v2 รอบใหญ่ขาขึ้น และราคาเหนือ EMA200
ข้อมูล: ราคาปิดรายวันปรับปันผล (data/backtest/daily) ช่วงวัดผล 2006–2026 · เงินสดได้ 0% · ไม่หักค่าธรรมเนียม/ภาษี
ข้อจำกัด: มีแต่ราคาปิด (ATR ประมาณจากการเปลี่ยนแปลงรายวัน × 1.25) · จักรวาลคือสมาชิก S&P 500 วันนี้

รัน: python ops/backtest_cycles.py
"""
from __future__ import annotations

import json
import math
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "backtest" / "daily"
OUT = ROOT / "data" / "backtest" / "cycles_summary.json"
START = "2006-01-01"


def vol_series(c):
    out, tr, s = [], [], 0.0
    for i in range(len(c)):
        t = abs(c[i] - c[i - 1]) * 1.25 if i else 0.0
        tr.append(t)
        s += t
        if i >= 14:
            s -= tr[i - 14]
        out.append((s / min(i + 1, 14)) / c[i] * 100)
    v, acc = [], 0.0
    for i, x in enumerate(out):
        acc += x
        if i >= 250:
            acc -= out[i - 250]
        v.append(acc / min(i + 1, 250))
    return v


def ema(c, n):
    k, out, prev = 2 / (n + 1), [], None
    for x in c:
        prev = x if prev is None else x * k + prev * (1 - k)
        out.append(prev)
    return out


def zigzag_state(c, pct):
    """ZigZag % คงที่: สถานะ = ทิศของขาที่ยืนยันแล้ว · คืน (state, bottoms, tops)"""
    st, bots, tops = [0] * len(c), [], []
    d, hi, lo = 0, c[0], c[0]
    for i, x in enumerate(c):
        if d >= 0 and x > hi: hi = x
        if d <= 0 and x < lo: lo = x
        if d <= 0 and x >= lo * (1 + pct / 100):
            d, hi = 1, x
            bots.append(i)
        elif d >= 0 and x <= hi * (1 - pct / 100):
            d, lo = -1, x
            tops.append(i)
        st[i] = 1 if d == 1 else 0
    return st, bots, tops


def v2_state(c, vol, mm, MM, choch=True):
    """ตรรกะเดียวกับ Cycle v2 ใน Pine (ใช้ราคาปิดแทน high/low)."""
    n = len(c)
    st, bots, tops = [0] * n, [], []
    mDir = 0; mHi = mLo = c[0]; mHiB = mLoB = 0
    lmh = lml = None; lmhB = lmlB = None
    MDir = 0; MHi = MLo = c[0]; MHiB = MLoB = 0; hsl = lsh = c[0]; hslB = lshB = 0
    topB = cycB = None
    for i, x in enumerate(c):
        mp, Mp = mm * vol[i], MM * vol[i]
        if mDir == 0:
            if x > mHi: mHi, mHiB = x, i
            if x < mLo: mLo, mLoB = x, i
            if x <= mHi * (1 - mp / 100) and mHiB < i:
                mDir = -1; lmh, lmhB = mHi, mHiB; mLo, mLoB = x, i
            elif x >= mLo * (1 + mp / 100) and mLoB < i:
                mDir = 1; lml, lmlB = mLo, mLoB; mHi, mHiB = x, i
        elif mDir == 1:
            if x > mHi: mHi, mHiB = x, i
            if x <= mHi * (1 - mp / 100):
                lmh, lmhB = mHi, mHiB; mDir = -1; mLo, mLoB = x, i
        else:
            if x < mLo: mLo, mLoB = x, i
            if x >= mLo * (1 + mp / 100):
                lml, lmlB = mLo, mLoB; mDir = 1; mHi, mHiB = x, i
        if MDir <= 0:
            if x < MLo: MLo, MLoB = x, i; hsl, hslB = x, i
            elif x > hsl: hsl, hslB = x, i
        if MDir >= 0:
            if x > MHi: MHi, MHiB = x, i; lsh, lshB = x, i
            elif x < lsh: lsh, lshB = x, i
        bL = lmh if (choch and lmhB is not None and (topB is None or lmhB > topB)) else None
        tL = lml if (choch and lmlB is not None and (cycB is None or lmlB > cycB)) else None
        if MDir <= 0 and MLoB < i and x >= MLo * (1 + Mp / 100) and (bL is None or x > bL):
            cycB = MLoB; MDir = 1; MHi, MHiB = hsl, hslB; lsh, lshB = x, i
            bots.append(i)
        elif MDir >= 0 and MHiB < i and x <= MHi * (1 - Mp / 100) and (tL is None or x < tL):
            topB = MHiB; MDir = -1; MLo, MLoB = lsh, lshB; hsl, hslB = x, i
            tops.append(i)
        st[i] = 1 if MDir == 1 else 0
    return st, bots, tops


def ema_state(c, e):
    st = [1 if c[i] > e[i] else 0 for i in range(len(c))]
    bots = [i for i in range(1, len(c)) if st[i] and not st[i - 1]]
    tops = [i for i in range(1, len(c)) if not st[i] and st[i - 1]]
    return st, bots, tops


def stage_state(c, e):
    st, cur = [0] * len(c), 0
    for i in range(len(c)):
        if i >= 20 and c[i] > e[i] and e[i] > e[i - 20]:
            cur = 1
        elif c[i] < e[i]:
            cur = 0
        st[i] = cur
    bots = [i for i in range(1, len(c)) if st[i] and not st[i - 1]]
    tops = [i for i in range(1, len(c)) if not st[i] and st[i - 1]]
    return st, bots, tops


def perf(c, st, i0):
    """ทำตามสถานะของวันก่อนหน้า (สัญญาณยืนยันที่ราคาปิด → มีผลวันถัดไป)."""
    eq = bh = 1.0
    peak_e = peak_b = 1.0
    dd_e = dd_b = 0.0
    switches, inmkt = 0, 0
    for i in range(i0 + 1, len(c)):
        r = c[i] / c[i - 1] - 1
        if st[i - 1]:
            eq *= 1 + r
            inmkt += 1
        bh *= 1 + r
        peak_e, peak_b = max(peak_e, eq), max(peak_b, bh)
        dd_e, dd_b = min(dd_e, eq / peak_e - 1), min(dd_b, bh / peak_b - 1)
        if st[i] != st[i - 1]:
            switches += 1
    yrs = (len(c) - 1 - i0) / 252
    return {"cagr": eq ** (1 / yrs) - 1, "bh_cagr": bh ** (1 / yrs) - 1, "mdd": dd_e, "bh_mdd": dd_b,
            "in_mkt": inmkt / (len(c) - 1 - i0), "switch_yr": switches / yrs}


def fwd_edge(c, idx, i0, h, base):
    xs = [c[i + h] / c[i] - 1 - base for i in idx if i >= i0 and i + h < len(c)]
    return xs


def main(cache=CACHE, index_stems=("SPY", "QQQ"), out=OUT):
    methods = ["zz15", "zz20", "zz25", "v2_a6", "v2_a8", "v2_a10", "v2_a12", "v2noC_10", "ema200", "stage", "v2_10+ema"]
    agg = {m: {"rows": [], "bot60": [], "bot250": [], "top60": [], "top250": []} for m in methods}
    idx_res = {}
    for f in sorted(cache.glob("*.json")):
        bars = json.loads(f.read_text(encoding="utf-8"))
        if len(bars) < 1500:
            continue
        d = [b[0] for b in bars]
        c = [b[1] for b in bars]
        i0 = next((i for i, x in enumerate(d) if x >= START), None)
        if i0 is None or i0 < 260 or len(c) - i0 < 750:
            continue
        vol, e200 = vol_series(c), ema(c, 200)
        states = {
            "zz15": zigzag_state(c, 15), "zz20": zigzag_state(c, 20), "zz25": zigzag_state(c, 25),
            "v2_a6": v2_state(c, vol, 4, 6), "v2_a8": v2_state(c, vol, 4, 8),
            "v2_a10": v2_state(c, vol, 4, 10), "v2_a12": v2_state(c, vol, 4, 12),
            "v2noC_10": v2_state(c, vol, 4, 10, choch=False),
            "ema200": ema_state(c, e200), "stage": stage_state(c, e200),
        }
        s10 = states["v2_a10"][0]
        comb = [1 if s10[i] and c[i] > e200[i] else 0 for i in range(len(c))]
        states["v2_10+ema"] = (comb, [i for i in range(1, len(c)) if comb[i] and not comb[i - 1]],
                               [i for i in range(1, len(c)) if not comb[i] and comb[i - 1]])
        base60 = statistics.median([c[i + 60] / c[i] - 1 for i in range(i0, len(c) - 60)])
        base250 = statistics.median([c[i + 250] / c[i] - 1 for i in range(i0, len(c) - 250)])
        for m, (st, bots, tops) in states.items():
            p = perf(c, st, i0)
            if f.stem in index_stems:
                idx_res[f"{f.stem}:{m}"] = p
                continue
            agg[m]["rows"].append(p)
            agg[m]["bot60"] += fwd_edge(c, bots, i0, 60, base60)
            agg[m]["bot250"] += fwd_edge(c, bots, i0, 250, base250)
            agg[m]["top60"] += fwd_edge(c, tops, i0, 60, base60)
            agg[m]["top250"] += fwd_edge(c, tops, i0, 250, base250)

    med = statistics.median
    summary = {}
    print(f"{'วิธี':<11} {'CAGR':>6} {'B&H':>6} {'ชนะB&H':>7} {'MaxDD':>7} {'B&H DD':>7} {'Calmar':>7} {'B&H Cal':>7} {'ในตลาด':>7} {'สลับ/ปี':>7} | ก้น→60d 250d | ยอด→60d 250d")
    for m in methods:
        rows = agg[m]["rows"]
        cal = [r["cagr"] / abs(r["mdd"]) if r["mdd"] < 0 else 0 for r in rows]
        bcal = [r["bh_cagr"] / abs(r["bh_mdd"]) if r["bh_mdd"] < 0 else 0 for r in rows]
        s = {"stocks": len(rows), "cagr": med(r["cagr"] for r in rows), "bh_cagr": med(r["bh_cagr"] for r in rows),
             "beat_bh": sum(r["cagr"] > r["bh_cagr"] for r in rows) / len(rows),
             "mdd": med(r["mdd"] for r in rows), "bh_mdd": med(r["bh_mdd"] for r in rows),
             "calmar": med(cal), "bh_calmar": med(bcal),
             "better_calmar": sum(a > b for a, b in zip(cal, bcal)) / len(rows),
             "in_mkt": med(r["in_mkt"] for r in rows), "switch_yr": med(r["switch_yr"] for r in rows),
             "bot60": med(agg[m]["bot60"]) if agg[m]["bot60"] else None,
             "bot250": med(agg[m]["bot250"]) if agg[m]["bot250"] else None,
             "top60": med(agg[m]["top60"]) if agg[m]["top60"] else None,
             "top250": med(agg[m]["top250"]) if agg[m]["top250"] else None}
        summary[m] = s
        pc = lambda x: f"{x * 100:+5.1f}%" if x is not None else "   –  "
        print(f"{m:<11} {s['cagr'] * 100:5.1f}% {s['bh_cagr'] * 100:5.1f}% {s['beat_bh'] * 100:6.0f}% {s['mdd'] * 100:6.0f}% {s['bh_mdd'] * 100:6.0f}% "
              f"{s['calmar']:7.2f} {s['bh_calmar']:7.2f} {s['in_mkt'] * 100:6.0f}% {s['switch_yr']:7.1f} | {pc(s['bot60'])} {pc(s['bot250'])} | {pc(s['top60'])} {pc(s['top250'])}")
    print("\nดัชนี (CAGR / MaxDD เทียบ ซื้อแล้วถือ):")
    for k, p in idx_res.items():
        print(f"  {k:<16} {p['cagr'] * 100:5.1f}% / {p['mdd'] * 100:5.0f}%   (B&H {p['bh_cagr'] * 100:5.1f}% / {p['bh_mdd'] * 100:5.0f}%) ในตลาด {p['in_mkt'] * 100:.0f}% สลับ {p['switch_yr']:.1f}/ปี")
    out.write_text(json.dumps({"summary": summary, "index": idx_res}, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
