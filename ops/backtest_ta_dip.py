"""Backtest กลยุทธ์ "ช้อนตอนย่อ" ด้วย EMA200 + RSI + MACD สำหรับลงทุนระยะยาว

กติกา (ตามที่ผู้ใช้ใช้จริง แปลงเป็นตัวเลขชัด ๆ — ปรับได้ที่ RULES):
  • ราคาใกล้ EMA200  : |ราคาปิด ÷ EMA200 − 1| ≤ 3%
  • RSI(14) ใกล้ 30   : RSI ≤ 35
  • MACD อยู่ขาลง     : เส้น MACD ต่ำกว่าเส้น Signal (histogram ติดลบ)
  • ครบ 3 ข้อในวันเดียว = สัญญาณ 1 ครั้ง (นับครั้งใหม่ได้เมื่อเว้นไปแล้ว ≥ 20 วันทำการ กันซื้อซ้ำวันติด ๆ)
อินดิเคเตอร์ใช้ฟังก์ชันเดียวกับกราฟในแอป (backend/app/indicators.py)

เทียบ 2 แบบ ด้วยเงินเท่ากัน — "ได้เงินเดือนละ 100 แล้วจะลงทุนยังไง":
  A. DCA      : ลงทุนทุกต้นเดือนทันที
  B. รอสัญญาณ : เก็บเงินสดไว้ (ดอกเบี้ย 0%) พอเกิดสัญญาณค่อยลงเงินที่สะสมไว้ทั้งหมด
ถือยาวไม่ขาย · ผลตอบแทนรวมปันผล (adjusted close) · ช่วงลงเงิน ม.ค. 2006 – ปัจจุบัน

วัดเพิ่ม: ผลตอบแทนล่วงหน้า 1/3/5 ปี หลังวันเกิดสัญญาณ เทียบกับ "ซื้อวันไหนก็ได้" ของหุ้นตัวเดียวกัน
และทดสอบแยกทีละเงื่อนไข เพื่อดูว่าเงื่อนไขไหนช่วยจริง

ข้อจำกัด: จักรวาลคือสมาชิก S&P 500 วันนี้ (ตัวที่รอดมาแล้ว) → ทั้ง A และ B ได้ประโยชน์จาก bias นี้เท่ากัน
แต่ตัวเลขผลตอบแทนรวมจะดูดีเกินจริง การเทียบ A กับ B ยังใช้ได้

รัน:  python ops/backtest_ta_dip.py
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

import httpx  # noqa: E402

from app.indicators import ema, macd, rsi  # noqa: E402

CACHE = ROOT / "data" / "backtest" / "daily"
OUT = ROOT / "data" / "backtest" / "ta_dip_summary.json"
START = "2006-01-01"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; research-backtest/1.0)"}

RULES = {"ema_band": 0.03, "rsi_max": 35.0, "cooldown": 20}
VARIANTS = {
    "all3": "ครบ 3 เงื่อนไข (กลยุทธ์ของคุณ)",
    "rsi_only": "RSI ≤ 35 อย่างเดียว",
    "ema_only": "ใกล้ EMA200 อย่างเดียว",
    "rsi_macd": "RSI ≤ 35 + MACD ขาลง (ไม่สน EMA200)",
    "rsi30_all3": "ครบ 3 เงื่อนไข แต่ RSI ≤ 30 เข้มกว่า",
}


async def fetch_daily(sym: str, client: httpx.AsyncClient) -> list | None:
    f = CACHE / f"{sym}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8")) or None
    try:
        r = await client.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}", headers=_HEADERS,
                             params={"interval": "1d", "period1": int(dt.datetime(2004, 1, 1).timestamp()),
                                     "period2": int(dt.datetime.now().timestamp()), "includeAdjustedClose": "true"},
                             timeout=30)
        res = (r.json().get("chart", {}).get("result") or [None])[0] if r.status_code == 200 else None
    except Exception:  # noqa: BLE001
        res = None
    bars = []
    if res and res.get("timestamp"):
        adj = (res["indicators"].get("adjclose") or [{}])[0].get("adjclose") or []
        for t, a in zip(res["timestamp"], adj):
            if a:
                bars.append([dt.datetime.fromtimestamp(t, dt.timezone.utc).date().isoformat(), a])
    f.write_text(json.dumps(bars), encoding="utf-8")
    return bars or None


def signals(closes: list[float]) -> dict[str, list[bool]]:
    e200, r14 = ema(closes, 200), rsi(closes, 14)
    m = macd(closes)
    mline, sline = (m["macd"], m["signal"]) if isinstance(m, dict) else (m[0], m[1])
    n = len(closes)
    near = [e200[i] is not None and abs(closes[i] / e200[i] - 1) <= RULES["ema_band"] for i in range(n)]
    low35 = [r14[i] is not None and r14[i] <= RULES["rsi_max"] for i in range(n)]
    low30 = [r14[i] is not None and r14[i] <= 30 for i in range(n)]
    down = [mline[i] is not None and sline[i] is not None and mline[i] < sline[i] for i in range(n)]
    raw = {
        "all3": [near[i] and low35[i] and down[i] for i in range(n)],
        "rsi_only": low35,
        "ema_only": near,
        "rsi_macd": [low35[i] and down[i] for i in range(n)],
        "rsi30_all3": [near[i] and low30[i] and down[i] for i in range(n)],
    }
    out = {}
    for k, arr in raw.items():  # กันนับซ้ำ: ต้องว่างเว้นสัญญาณ ≥ cooldown วัน ถึงนับครั้งใหม่
        ev, last = [False] * n, -10**9
        for i, v in enumerate(arr):
            if v:
                if i - last > RULES["cooldown"]:
                    ev[i] = True
                last = i
        out[k] = ev
    return out


def simulate(dates: list[str], closes: list[float], sig: list[bool], i0: int) -> dict:
    """เงินเดือนละ 1 หน่วย: DCA ต้นเดือน vs เก็บสดรอสัญญาณ → มูลค่าปลายทาง."""
    dca_sh = sig_sh = cash = 0.0
    month, deposits, buys = None, 0, 0
    for i in range(i0, len(dates)):
        ym = dates[i][:7]
        if ym != month:  # วันทำการแรกของเดือน = เงินเดือนเข้า
            month = ym
            deposits += 1
            dca_sh += 1 / closes[i]
            cash += 1
        if sig[i] and cash > 0:
            sig_sh += cash / closes[i]
            cash = 0.0
            buys += 1
    last = closes[-1]
    return {"deposits": deposits, "dca": dca_sh * last, "sig": sig_sh * last + cash,
            "idle_cash": cash, "buys": buys}


def forward(closes: list[float], idx: list[int], days: int) -> list[float]:
    return [closes[i + days] / closes[i] - 1 for i in idx if i + days < len(closes)]


def analyze(sym: str, bars: list) -> dict | None:
    dates = [b[0] for b in bars]
    closes = [b[1] for b in bars]
    i0 = next((i for i, d in enumerate(dates) if d >= START), None)
    if i0 is None or i0 < 220 or len(dates) - i0 < 750:
        return None  # ต้องมีข้อมูลก่อนปี 2006 พอให้ EMA200 อุ่นเครื่อง และย้อนหลังอย่างน้อย ~3 ปี
    sig = signals(closes)
    years = (len(dates) - i0) / 252
    base_idx = list(range(i0, len(closes)))
    out = {"sym": sym, "years": round(years, 1), "variants": {}}
    for k in VARIANTS:
        idx = [i for i in range(i0, len(closes)) if sig[k][i]]
        sim = simulate(dates, closes, sig[k], i0)
        v = {"signals": len(idx), "per_year": len(idx) / years, "sim": sim,
             "signal_dates": [dates[i] for i in idx][-40:]}
        for h, lab in ((252, "1y"), (756, "3y"), (1260, "5y")):
            fs, fb = forward(closes, idx, h), forward(closes, base_idx, h)
            v[f"fwd_{lab}"] = statistics.median(fs) if fs else None
            v[f"base_{lab}"] = statistics.median(fb) if fb else None
            v[f"n_{lab}"] = len(fs)
        out["variants"][k] = v
    return out


def summarize(results: list[dict]) -> dict:
    summ = {}
    for k, lab in VARIANTS.items():
        rows = [r["variants"][k] for r in results]
        ratios = [v["sim"]["sig"] / v["sim"]["dca"] for v in rows if v["sim"]["dca"] > 0]
        idle = [v["sim"]["idle_cash"] / v["sim"]["deposits"] for v in rows]
        never = sum(v["signals"] == 0 for v in rows)
        s = {"label": lab, "stocks": len(rows),
             "median_signals_per_year": statistics.median(v["per_year"] for v in rows),
             "stocks_never_signaled": never,
             "beat_dca_share": sum(x > 1 for x in ratios) / len(ratios),
             "median_final_vs_dca": statistics.median(ratios),
             "median_idle_cash_share": statistics.median(idle)}
        for h in ("1y", "3y", "5y"):
            ed = [v[f"fwd_{h}"] - v[f"base_{h}"] for v in rows if v[f"fwd_{h}"] is not None and v[f"base_{h}"] is not None]
            s[f"median_edge_{h}"] = statistics.median(ed) if ed else None
            s[f"edge_positive_share_{h}"] = sum(x > 0 for x in ed) / len(ed) if ed else None
        summ[k] = s
    return summ


async def main() -> None:
    iv = json.loads((ROOT / "data" / "intrinsic_ranges.json").read_text(encoding="utf-8"))
    syms = sorted({r["symbol"] for r in iv["results"]} | set(iv["skipped"]))
    index_syms = ["SPY", "QQQ"]
    CACHE.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(8)
    async with httpx.AsyncClient() as client:
        async def one(s):
            async with sem:
                return s, await fetch_daily(s, client)
        data = dict(await asyncio.gather(*(one(s) for s in syms + index_syms)))
    print(f"ดึงราคารายวันได้ {sum(1 for v in data.values() if v)}/{len(data)} ตัว", flush=True)

    results = [r for s in syms if data.get(s) and (r := analyze(s, data[s]))]
    idx_results = {s: analyze(s, data[s]) for s in index_syms if data.get(s)}
    summ = summarize(results)
    payload = {"as_of": dt.date.today().isoformat(), "rules": RULES, "start": START,
               "stocks_tested": len(results), "summary": summ, "index": idx_results,
               "per_stock": {r["sym"]: {k: {"signals": v["signals"], "final_vs_dca": round(v["sim"]["sig"] / v["sim"]["dca"], 3),
                                            "last_signals": v["signal_dates"][-5:]}
                                        for k, v in r["variants"].items()} for r in results}}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"ทดสอบได้ {len(results)} หุ้น")
    for k, s in summ.items():
        print(f"{s['label']:<40} สัญญาณ/ปี {s['median_signals_per_year']:.2f} · ชนะ DCA {s['beat_dca_share'] * 100:.0f}% ของหุ้น · "
              f"มูลค่าเทียบ DCA {s['median_final_vs_dca']:.3f}x · เงินค้าง {s['median_idle_cash_share'] * 100:.0f}% · "
              f"ได้เปรียบ 1 ปี {s['median_edge_1y'] * 100:+.1f}% · 3 ปี {s['median_edge_3y'] * 100:+.1f}% · ไม่เคยเกิดสัญญาณ {s['stocks_never_signaled']}")
    for s, r in idx_results.items():
        if r:
            for k, v in r["variants"].items():
                print(f"  {s} {VARIANTS[k]:<38} สัญญาณ {v['signals']} ครั้ง · มูลค่าเทียบ DCA {v['sim']['sig'] / v['sim']['dca']:.3f}x · "
                      f"ล่วงหน้า 1 ปี {(v['fwd_1y'] or 0) * 100:+.1f}% (ปกติ {(v['base_1y'] or 0) * 100:+.1f}%)")
    print(f"→ {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
