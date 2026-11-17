"""คำนวณกรอบมูลค่าที่แท้จริง (Intrinsic Value) ของหุ้น S&P 500 + Nasdaq-100 แล้วสร้าง Pine Script
ไปวางใน TradingView — เปิดกราฟหุ้นตัวไหน กล่องกรอบมูลค่าของตัวนั้นจะขึ้นเอง.

ใช้เครื่องประเมินตัวเดียวกับกล่อง "ราคายุติธรรม" ในแอป (value_scanner → revenue_model.sector_fair_value):
  เลือก metric ตาม sector (P/E, P/B, P/FFO, P/FCF, Shiller P/E) × ตัวคูณ median ที่ตลาดให้ ~2 ปีล่าสุด
  → low / fair / high ต่อหุ้น จากงบ SEC ล่าสุดที่ประกาศแล้ว.

รัน (จากโฟลเดอร์ trading-app):
    python ops/export_tradingview_iv.py              # สแกนใหม่ทั้งหมด (~10-20 นาที)
    python ops/export_tradingview_iv.py --pine-only  # ใช้ผลสแกนเดิม สร้าง Pine ใหม่อย่างเดียว

ผลลัพธ์:
    data/intrinsic_ranges.json        ผลคำนวณดิบ (ไว้ให้แอป/Claude อ่าน)
    tradingview/intrinsic_value.pine  ก๊อปทั้งไฟล์ไปวางใน Pine Editor ของ TradingView
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import value_scanner  # noqa: E402
from app.revenue_scanner import load_sp500  # noqa: E402

OUT_JSON = ROOT / "data" / "intrinsic_ranges.json"
OUT_PINE = ROOT / "tradingview" / "intrinsic_value.pine"
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36"}


def _slickcharts(index: str) -> list[dict]:
    """รายชื่อสมาชิกดัชนีปัจจุบันจาก slickcharts (sp500 / nasdaq100)."""
    import pandas as pd

    html = urllib.request.urlopen(
        urllib.request.Request(f"https://www.slickcharts.com/{index}", headers=_UA), timeout=30
    ).read().decode()
    for t in pd.read_html(io.StringIO(html)):
        if "Symbol" in t.columns and len(t) > 50:
            return [{"symbol": str(s).strip().upper().replace(".", "-"), "name": str(n).strip()}
                    for s, n in zip(t["Symbol"], t["Company"])]
    raise RuntimeError(f"ไม่พบตารางรายชื่อ {index}")


def load_universe() -> list[dict]:
    csv_rows = {r["symbol"]: r for r in load_sp500()}
    try:
        sp = _slickcharts("sp500")
    except Exception as e:  # noqa: BLE001
        print(f"! ดึงรายชื่อ S&P 500 ล่าสุดไม่ได้ ({e}) — ใช้ CSV ในเครื่องแทน")
        sp = [{"symbol": s, "name": r["name"]} for s, r in csv_rows.items()]
    ndx = _slickcharts("nasdaq100")

    out: dict[str, dict] = {}
    for idx, rows in (("SP500", sp), ("NDX100", ndx)):
        for r in rows:
            item = out.setdefault(r["symbol"], {
                "symbol": r["symbol"], "name": r["name"],
                "sector": csv_rows.get(r["symbol"], {}).get("sector", ""), "index": [],
            })
            item["index"].append(idx)
    return list(out.values())


async def scan(universe: list[dict], concurrency: int) -> list[dict]:
    sem = asyncio.Semaphore(concurrency)
    done = 0

    async def worker(item: dict):
        nonlocal done
        async with sem:
            r = await value_scanner._symbol_fair_value(item["symbol"], item["name"], item["sector"], refresh=True)
        done += 1
        if done % 25 == 0:
            print(f"  {done}/{len(universe)}", flush=True)
        if r:
            r["index"] = item["index"]
        return r

    results = await asyncio.gather(*(worker(i) for i in universe))
    return [r for r in results if r]


def _num(x: float) -> str:
    return f"{x:.2f}".rstrip("0").rstrip(".")


def grade(r: dict) -> str:
    """เกรดความน่าเชื่อของกรอบ: A = ใช้ได้ · B = ควรตรวจซ้ำ · C = ไม่น่าเชื่อ.
    C: ตัวคูณฐานสูงผิดปกติ (extreme) — ราคายุติธรรมแค่สะท้อนความแพงในอดีต
    B: ตัวคูณสูง (rich) · ใช้ P/FFO แต่ไม่ใช่ Real Estate (จำแนก sector ผิด) ·
       ราคาห่าง fair เกิน ~2 เท่า (มักเกิดจาก split / กำไรพิเศษครั้งเดียว ทำให้ฐานเพี้ยน)"""
    level = value_scanner.annotate_multiple_level(r)["multiple_level"]
    if level == "extreme":
        return "C"
    ratio = r["price"] / r["fair_price"] if r["fair_price"] else 99
    misclassified = "FFO" in r["basis"] and r.get("gics_sector") != "Real Estate"
    if level == "rich" or misclassified or ratio > 2.0 or ratio < 0.5 or r.get("warnings"):
        return "B"
    return "A"


def build_pine(rows: list[dict], as_of: str) -> str:
    # 1 รายการ = "SYM:low:fair:high:basis:grade" — ตัดเป็นก้อนละ 40 ตัว กันสตริงยาวเกินลิมิตของ Pine
    entries = []
    for r in sorted(rows, key=lambda x: x["symbol"]):
        tv_sym = r["symbol"].replace("-", ".")  # TradingView ใช้ BRK.B ไม่ใช่ BRK-B
        basis = r["basis"].split(" ")[0]       # "P/E median (TTM)" → "P/E"
        if basis == "Normalized":
            basis = "Norm-" + r["basis"].split(" ")[1]
        entries.append(f"{tv_sym}:{_num(r['low'])}:{_num(r['fair_price'])}:{_num(r['high'])}:{basis}:{grade(r)}")
    chunks = ["|".join(entries[i:i + 40]) for i in range(0, len(entries), 40)]
    data_lines = "\n".join(f'    array.push(DB, "{c}")' for c in chunks)

    return f"""// Intrinsic Value Box — สร้างอัตโนมัติจาก trading-app (ops/export_tradingview_iv.py)
// ข้อมูล ณ {as_of} · {len(entries)} หุ้น (S&P 500 + Nasdaq-100)
// วิธี: metric ตาม sector × ตัวคูณ median ~2 ปี จากงบ SEC ล่าสุด — เป็นผลจากโมเดล ไม่ใช่คำแนะนำการลงทุน
//@version=6
indicator("Intrinsic Value Box", overlay = true, max_boxes_count = 5, max_lines_count = 5, max_labels_count = 5)

lookback   = input.int(150, "กล่องเริ่มย้อนหลัง (แท่ง)", minval = 1, maxval = 4000)
extendBars = input.int(40, "ยื่นไปทางขวา (แท่ง)", minval = 0, maxval = 400)
showFair   = input.bool(true, "แสดงเส้นราคายุติธรรม")
showLabel  = input.bool(true, "แสดงป้ายตัวเลข")
boxColor   = input.color(color.new(color.yellow, 85), "สีพื้นกล่อง")
edgeColor  = input.color(color.yellow, "สีขอบกล่อง")

var array<string> DB = array.new<string>()
if barstate.isfirst
{data_lines}

var float ivLow = na
var float ivFair = na
var float ivHigh = na
var string ivBasis = ""
var string ivGrade = ""
var bool looked = false

if barstate.islast and not looked
    looked := true
    string key = syminfo.ticker + ":"
    for chunk in DB
        if str.contains("|" + chunk, "|" + key)
            for e in str.split(chunk, "|")
                if str.startswith(e, key)
                    array<string> p = str.split(e, ":")
                    ivLow   := str.tonumber(array.get(p, 1))
                    ivFair  := str.tonumber(array.get(p, 2))
                    ivHigh  := str.tonumber(array.get(p, 3))
                    ivBasis := array.get(p, 4)
                    ivGrade := array.get(p, 5)
                    break
            break

var box  ivBox  = na
var line ivLine = na
var label ivLbl = na

if barstate.islast and not na(ivLow)
    int left  = math.max(bar_index - lookback, 0)
    int right = bar_index + extendBars
    box.delete(ivBox)
    line.delete(ivLine)
    label.delete(ivLbl)
    // เกรด C (ไม่น่าเชื่อ) วาดเป็นกล่องสีเทา จะได้ไม่เผลอใช้เหมือนกรอบปกติ
    color eCol = ivGrade == "C" ? color.gray : edgeColor
    color bCol = ivGrade == "C" ? color.new(color.gray, 88) : boxColor
    string gTxt = ivGrade == "A" ? "" : ivGrade == "B" ? "  ⚠ ควรตรวจซ้ำ" : "  ✕ ไม่น่าเชื่อ"
    ivBox := box.new(left, ivHigh, right, ivLow, border_color = eCol, bgcolor = bCol,
         text = "Intrinsic Value" + gTxt, text_color = color.white, text_size = size.normal)
    if showFair
        ivLine := line.new(left, ivFair, right, ivFair, color = eCol, style = line.style_dashed)
    if showLabel
        float up = (ivFair - close) / close * 100
        color c = close < ivLow ? color.green : close > ivHigh ? color.red : color.orange
        string zone = close < ivLow ? "ต่ำกว่ากรอบ" : close > ivHigh ? "สูงกว่ากรอบ" : "อยู่ในกรอบ"
        ivLbl := label.new(right, ivHigh,
             "IV " + str.tostring(ivLow, "#.##") + " – " + str.tostring(ivHigh, "#.##") +
             "\\nFair " + str.tostring(ivFair, "#.##") + " (" + ivBasis + ")" +
             "\\nห่างจาก Fair " + str.tostring(up, "+#.#;-#.#") + "% · " + zone +
             "\\nเกรดความน่าเชื่อ " + ivGrade +
             "\\nข้อมูล {as_of}",
             style = label.style_label_lower_left, color = c, textcolor = color.white, size = size.small)

if barstate.islast and na(ivLow)
    var label miss = label.new(bar_index, high, "ไม่มีข้อมูล IV ของ " + syminfo.ticker,
         style = label.style_label_down, color = color.gray, textcolor = color.white, size = size.small)
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pine-only", action="store_true", help="ใช้ผลใน data/intrinsic_ranges.json ที่มีอยู่")
    ap.add_argument("--concurrency", type=int, default=8)
    args = ap.parse_args()

    if args.pine_only:
        payload = json.loads(OUT_JSON.read_text(encoding="utf-8"))
    else:
        universe = load_universe()
        print(f"สแกน {len(universe)} หุ้น ...", flush=True)
        t0 = time.time()
        rows = asyncio.run(scan(universe, args.concurrency))
        payload = {"as_of": datetime.now().strftime("%Y-%m-%d"), "universe_count": len(universe),
                   "success_count": len(rows), "skipped": sorted({u["symbol"] for u in universe} -
                                                                {r["symbol"] for r in rows}),
                   "results": rows}
        OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
        OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"เสร็จ {len(rows)}/{len(universe)} ตัว ใน {time.time() - t0:.0f} วินาที → {OUT_JSON}")

    OUT_PINE.parent.mkdir(parents=True, exist_ok=True)
    OUT_PINE.write_text(build_pine(payload["results"], payload["as_of"]), encoding="utf-8")
    print(f"Pine Script → {OUT_PINE}")


if __name__ == "__main__":
    main()
