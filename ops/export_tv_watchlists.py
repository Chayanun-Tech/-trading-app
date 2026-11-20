"""สร้างไฟล์ Watchlist สำหรับ "นำเข้า" (Import list) ใน TradingView จากผลประเมินมูลค่า + Mega Trend

ไฟล์ .txt หนึ่งไฟล์ = หนึ่ง watchlist · บรรทัด "###ชื่อ" = หัวข้อแบ่งกลุ่ม (section) ใน watchlist
วิธีนำเข้า: TradingView → แผง Watchlist → เมนูชื่อ list → "นำเข้ารายการ..." (Import list) → เลือกไฟล์

รัน:  python ops/export_tv_watchlists.py   (ต้องรัน export_tradingview_iv.py และ megatrend_screen.py ก่อน)
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tradingview" / "watchlists"


def tv(sym: str) -> str:
    return sym.replace("-", ".")  # TradingView ใช้ BRK.B


def write(name: str, sections: list[tuple[str, list[str]]]) -> int:
    lines, n = [], 0
    for title, syms in sections:
        if syms:
            lines.append(f"###{title}," + ",".join(tv(s) for s in syms))
            n += len(syms)
    (OUT / name).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return n


def main() -> None:
    iv = {r["symbol"]: r for r in json.loads((ROOT / "data" / "intrinsic_ranges.json").read_text(encoding="utf-8"))["results"]}
    mq = json.loads((ROOT / "data" / "megatrend_quality.json").read_text(encoding="utf-8"))
    trends, st = mq["trends"], mq["stocks"]
    OUT.mkdir(parents=True, exist_ok=True)

    def gap(s):  # อดีตโต − ราคาต้องโต (บวก = ตลาดคาดหวังต่ำกว่าที่เคยทำได้)
        r = iv[s]
        if r.get("implied_growth_pct") is None or r.get("hist_growth_pct") is None:
            return -999
        return r["hist_growth_pct"] - r["implied_growth_pct"]

    def lt(s):
        return bool((st.get(s, {}).get("quality") or {}).get("long_term"))

    # 1) Shortlist: Mega Trend + ถือยาวได้ + เกรด A/B — แบ่ง section ตามเทรนด์หลัก (เทรนด์แรกของหุ้น)
    short = [s for s in iv if st.get(s, {}).get("trends") and lt(s) and iv[s]["grade"] != "C"]
    sec = [(trends[k], sorted([s for s in short if st[s]["trends"][0] == k], key=gap, reverse=True)) for k in trends]
    n1 = write("01_megatrend_longterm.txt", sec)

    # 2) ทุกตัว แบ่งตามเกรด → ตำแหน่งราคาเทียบกรอบ
    def zone(s):
        r = iv[s]
        return "ต่ำกว่ากรอบ" if r["price"] < r["low"] else "สูงกว่ากรอบ" if r["price"] > r["high"] else "ในกรอบ"
    sec2 = []
    for g in "ABC":
        for z in ("ต่ำกว่ากรอบ", "ในกรอบ", "สูงกว่ากรอบ"):
            syms = sorted([s for s in iv if iv[s]["grade"] == g and zone(s) == z], key=gap, reverse=True)
            sec2.append((f"เกรด {g} · {z}", syms))
    n2 = write("02_all_by_grade.txt", sec2)

    # 3) แนว Buffett: คุณภาพ 6/6 · FCF margin ≥ 15% · ROE ≥ 15% · หนี้สุทธิ ≤ 2 เท่า FCF — แบ่งตามส่วนเผื่อราคา
    def buffett(s):
        q = (st.get(s, {}).get("quality") or {})
        nd = q.get("net_debt_to_fcf")
        return (iv[s]["grade"] != "C" and q.get("passed") == 6 and (q.get("fcf_margin_pct") or 0) >= 15
                and (q.get("roe_pct") or 0) >= 15 and (nd is None or nd <= 2))
    bf = sorted([s for s in iv if buffett(s)], key=lambda s: iv[s]["price"] / iv[s]["fair_price"])
    ratio = lambda s: iv[s]["price"] / iv[s]["fair_price"]  # noqa: E731
    n3 = write("03_buffett_style.txt", [
        ("มีส่วนเผื่อ ≥15% (ราคา ≤ 85% ของ Fair)", [s for s in bf if ratio(s) <= 0.85]),
        ("ใกล้มูลค่า (85–105%)", [s for s in bf if 0.85 < ratio(s) <= 1.05]),
        ("ธุรกิจดีแต่ราคาเต็มแล้ว (>105%)", [s for s in bf if ratio(s) > 1.05]),
    ])
    print(f"01_megatrend_longterm {n1} ตัว · 02_all_by_grade {n2} ตัว · 03_buffett_style {n3} ตัว → {OUT}")


if __name__ == "__main__":
    main()
