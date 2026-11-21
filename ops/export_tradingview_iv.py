"""คำนวณกรอบมูลค่าที่แท้จริง (Intrinsic Value) ของหุ้น S&P 500 + Nasdaq-100 แล้วสร้าง Pine Script
ไปวางใน TradingView — เปิดกราฟหุ้นตัวไหน กล่องกรอบมูลค่าของตัวนั้นจะขึ้นเอง.

ประเมินแบบรายตัว (backend/app/valuation_v2.py): จำแนกประเภทธุรกิจ 9 แบบ แล้วใช้วิธี/อัตราคิดลด/การโตเฉพาะบริษัท
รวมกับ v1 (consensus_value) ตามผล backtest ระยะ 2: ใช้ v2 กับหุ้นโตเร็ว/วัฏจักร/Compounder · ใช้ v1 กับการเงิน/พลิกฟื้น
· ไม่ให้สัญญาณซื้อกับหุ้นอิ่มตัว (กับดักหุ้นถูก) / ยังไม่ทำกำไร / ความน่าเชื่อต่ำ (valuation_v2.combine_v1_v2)
หุ้นใน data/focus_list.json ถูกรวมเข้าไปเสมอ (เช่น TSM ที่ไม่อยู่ในดัชนี).

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

from app.consensus_value import consensus_fair_value  # noqa: E402
from app.valuation_v2 import combine_v1_v2, value_stock  # noqa: E402
from app.revenue_scanner import load_sp500  # noqa: E402

OUT_JSON = ROOT / "data" / "intrinsic_ranges.json"
OUT_PINE = ROOT / "tradingview" / "intrinsic_value.pine"
FOCUS = ROOT / "data" / "focus_list.json"
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
    try:
        focus = json.loads(FOCUS.read_text(encoding="utf-8"))["symbols"]
    except (OSError, ValueError, KeyError):
        focus = []
    for sym in focus:
        sym = sym.upper().replace(".", "-")
        out.setdefault(sym, {"symbol": sym, "name": sym, "sector": "", "index": []})["index"].append("FOCUS")
    return list(out.values())


async def scan(universe: list[dict], concurrency: int) -> list[dict]:
    sem = asyncio.Semaphore(concurrency)
    done = 0

    async def worker(item: dict):
        nonlocal done
        async with sem:
            r1 = r2 = None
            try:
                r1 = await consensus_fair_value(item["symbol"], refresh=True)   # v1 + ดึงงบ SEC ใหม่
            except Exception as e:  # noqa: BLE001 — ตัวเดียวพังไม่ล้มทั้งการสแกน
                print(f"  ! v1 {item['symbol']}: {e!r}", flush=True)
            try:
                r2 = await value_stock(item["symbol"])                            # v2 (ใช้งบที่เพิ่งดึง)
            except Exception as e:  # noqa: BLE001
                print(f"  ! v2 {item['symbol']}: {e!r}", flush=True)
            r = combine_v1_v2(r1, r2)
            if r:
                r["symbol"] = item["symbol"]
        done += 1
        if done % 25 == 0:
            print(f"  {done}/{len(universe)}", flush=True)
        if r:
            r["index"] = item["index"]
            r["name"] = item["name"]
        return r

    results = await asyncio.gather(*(worker(i) for i in universe))
    return [r for r in results if r]


def _num(x: float) -> str:
    return f"{x:.2f}".rstrip("0").rstrip(".")


def cycle_section() -> str:
    """ต่อท้ายด้วย Cycle Counter (tradingview/cycle_counter.pine) ให้เป็น indicator เดียวกัน —
    แผน Basic ของ TradingView ใส่ได้แค่ 2 อินดิเคเตอร์ต่อกราฟ การรวมจึงประหยัดช่อง.
    ตัดส่วนหัว (คอมเมนต์ //@version และบรรทัด indicator) ทิ้ง เหลือแต่ตัวโค้ด."""
    src = (ROOT / "tradingview" / "cycle_counter.pine").read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(src) if ln.startswith("indicator(")) + 1
    head = "\n\n// ════════ Cycle Counter (นับรอบ) — รวมมาจาก tradingview/cycle_counter.pine ════════\n"
    return head + "\n".join(src[start:]) + "\n"


def build_pine(rows: list[dict], as_of: str) -> str:
    # 1 รายการ = "SYM:low:fair:high:ความน่าเชื่อ(A=สูง B=กลาง C=ต่ำ):ประเภทธุรกิจ:โตจริง%:โตที่ราคาคาดหวัง%:ให้สัญญาณซื้อ(1/0)"
    # ("-" = ไม่มีข้อมูล; ไม่ใช้ค่าว่างเพราะ str.split อาจตัดช่องท้ายทิ้ง)
    # ตัดเป็นก้อนละ 40 ตัว กันสตริงยาวเกินลิมิตของ Pine
    entries = []
    for r in sorted(rows, key=lambda x: x["symbol"]):
        tv_sym = r["symbol"].replace("-", ".")  # TradingView ใช้ BRK.B ไม่ใช่ BRK-B
        hg = "-" if r.get("hist_growth_pct") is None else _num(r["hist_growth_pct"])
        ig = "-" if r.get("implied_growth_pct") is None else _num(r["implied_growth_pct"])
        entries.append(f"{tv_sym}:{_num(r['low'])}:{_num(r['fair_price'])}:{_num(r['high'])}:"
                       f"{r['grade']}:{r.get('archetype') or 'none'}:{hg}:{ig}:{1 if r.get('buy_allowed') else 0}")
    chunks = ["|".join(entries[i:i + 40]) for i in range(0, len(entries), 40)]
    data_lines = "\n".join(f'    array.push(DB, "{c}")' for c in chunks)

    return f"""// Intrinsic Value Box — สร้างอัตโนมัติจาก trading-app (ops/export_tradingview_iv.py)
// ข้อมูล ณ {as_of} · {len(entries)} หุ้น (S&P 500 + Nasdaq-100)
// วิธี: ประเมินรายตัวตามประเภทธุรกิจ (valuation v2) + v1 สำหรับการเงิน/พลิกฟื้น · อัตราคิดลด/การโตเฉพาะบริษัท จากงบ SEC ล่าสุด
// ความน่าเชื่อ สูง/กลาง/ต่ำ ผ่าน backtest point-in-time 2013–2026 — เป็นผลจากโมเดล ไม่ใช่คำแนะนำการลงทุน
//@version=6
indicator("Intrinsic Value + Cycle", shorttitle = "IV + Cycle", overlay = true, max_boxes_count = 5, max_lines_count = 500, max_labels_count = 500, max_polylines_count = 100)

lookback   = input.int(150, "กล่องเริ่มย้อนหลัง (แท่ง)", minval = 1, maxval = 4000, group = "Intrinsic Value")
extendBars = input.int(40, "ยื่นไปทางขวา (แท่ง)", minval = 0, maxval = 400, group = "Intrinsic Value")
showFair   = input.bool(true, "แสดงเส้นราคายุติธรรม", group = "Intrinsic Value")
showLabel  = input.bool(true, "แสดงป้ายตัวเลข", group = "Intrinsic Value")
boxColor   = input.color(color.new(color.yellow, 85), "สีพื้นกล่อง", group = "Intrinsic Value")
edgeColor  = input.color(color.yellow, "สีขอบกล่อง", group = "Intrinsic Value")

var array<string> DB = array.new<string>()
if barstate.isfirst
{data_lines}

var float ivLow = na
var float ivFair = na
var float ivHigh = na
var string ivGrade = ""
var string ivArche = ""
var string ivBuy = "1"
var string ivHistG = ""
var string ivImplG = ""
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
                    ivGrade  := array.get(p, 4)
                    ivArche  := array.get(p, 5)
                    ivHistG  := array.get(p, 6)
                    ivImplG  := array.get(p, 7)
                    ivBuy    := array.size(p) > 8 ? array.get(p, 8) : "1"
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
    string gTxt = ivGrade == "C" ? "  ✕ ความน่าเชื่อต่ำ" : ivBuy == "0" ? "  ⚠ ไม่ใช้เป็นสัญญาณซื้อ" : ivGrade == "B" ? "  · ความน่าเชื่อกลาง" : ""
    ivBox := box.new(left, ivHigh, right, ivLow, border_color = eCol, bgcolor = bCol,
         text = "Intrinsic Value" + gTxt, text_color = color.white, text_size = size.normal)
    if showFair
        ivLine := line.new(left, ivFair, right, ivFair, color = eCol, style = line.style_dashed)
    if showLabel
        float up = (ivFair - close) / close * 100
        // เกรด C: ป้ายเป็นสีเทาเสมอ — ไม่ให้สีเขียว/แดงชวนเข้าใจผิดว่าเป็นสัญญาณ
        color c = ivGrade == "C" or ivBuy == "0" ? color.gray : close < ivLow ? color.green : close > ivHigh ? color.red : color.orange
        string zone = close < ivLow ? "ต่ำกว่ากรอบ" : close > ivHigh ? "สูงกว่ากรอบ" : "อยู่ในกรอบ"
        string archeTh = switch ivArche
            "fast" => "โตเร็ว"
            "compounder" => "Compounder มั่นคง"
            "mature" => "อิ่มตัว/โตช้า"
            "cyclical" => "วัฏจักร"
            "commodity" => "ผูกราคาสินค้าโภคภัณฑ์"
            "financial" => "การเงิน"
            "reit" => "REIT"
            "turnaround" => "กำลังพลิกฟื้น"
            "unprofitable" => "ยังไม่ทำกำไร"
            => "ไม่ระบุประเภท"
        string confTh = ivGrade == "A" ? "สูง" : ivGrade == "B" ? "กลาง" : "ต่ำ"
        string gLine = "ประเภท: " + archeTh + " · ความน่าเชื่อ " + confTh +
             (ivBuy == "0" ? "
ไม่ให้สัญญาณซื้อ (ถูกแล้วมักเป็นกับดัก / ความน่าเชื่อต่ำ)" : "")
        string gText = ivImplG == "-" ? "" :
             "\\nราคาวันนี้ต้องโต " + ivImplG + "%/ปี (5 ปี)" + (ivHistG == "-" ? "" : " · อดีตโต " + ivHistG + "%")
        ivLbl := label.new(right, ivHigh,
             "IV " + str.tostring(ivLow, "#.##") + " – " + str.tostring(ivHigh, "#.##") +
             " · Fair " + str.tostring(ivFair, "#.##") +
             "\\nห่างจาก Fair " + str.tostring(up, "+#.#;-#.#") + "% · " + zone + gText +
             "\\n" + gLine +
             "\\nข้อมูล {as_of}",
             style = label.style_label_lower_left, color = c, textcolor = color.white, size = size.small)

if barstate.islast and na(ivLow)
    var label miss = label.new(bar_index, high, "ไม่มีกรอบ IV ของ " + syminfo.ticker + "\\n(ไม่อยู่ในรายชื่อ / ยังไม่มีกำไรหรือกระแสเงินสด / งบ SEC ไม่พอ)",
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
    OUT_PINE.write_text(build_pine(payload["results"], payload["as_of"]) + cycle_section(), encoding="utf-8")
    print(f"Pine Script → {OUT_PINE}")


if __name__ == "__main__":
    main()
