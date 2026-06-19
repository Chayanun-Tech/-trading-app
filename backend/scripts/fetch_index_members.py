"""ดึงรายชื่อสมาชิก 3 ดัชนีหลัก: S&P 500, Nasdaq-100, Russell 2000.

เขียนผลออกเป็น:
    data/financials/index_members.json  — แยกตามดัชนี + union (พร้อมจำนวน)
    data/financials/index_tickers.txt   — รายชื่อ union (1 ตัว/บรรทัด) สำหรับป้อน build_offline_db.py

แหล่งข้อมูล (ฟรี):
- S&P 500   : datahub (constituents.csv) — อัปเดตสม่ำเสมอ
- Nasdaq-100: รายชื่อ snapshot จาก Wikipedia (ฝังไว้; แก้ได้ที่ NASDAQ100 ด้านล่าง)
- Russell 2000: ikoniaris/Russell2000 (github) — โดยประมาณ (ดัชนีนี้ไม่มีลิสต์ฟรีที่เป็นทางการ)

จากนั้น:
    backend\\.venv\\Scripts\\python backend\\scripts\\fetch_index_members.py
    backend\\.venv\\Scripts\\python backend\\scripts\\build_offline_db.py --all --tickers-file data\\financials\\index_tickers.txt
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "data" / "financials"
HEADERS = {"User-Agent": "ChayanunOperating-trading-app chayanun250841@gmail.com"}

SP500_CSV = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
RUSSELL_CSV = "https://raw.githubusercontent.com/ikoniaris/Russell2000/master/russell_2000_components.csv"

# Nasdaq-100 — snapshot จาก Wikipedia (อัปเดตได้เองเมื่อดัชนีปรับสมาชิก)
NASDAQ100 = ("ADBE AMD ABNB ALNY GOOGL GOOG AMZN AEP AMGN ADI AAPL AMAT APP ARM ASML ADSK ADP "
             "AXON BKR BKNG AVGO CDNS CHTR CTAS CSCO CCEP CTSH CMCSA CEG CPRT COST CRWD CSX DDOG "
             "DXCM FANG DASH EA EXC FAST FER FTNT GEHC GILD HON IDXX INSM INTC INTU ISRG KDP KLAC "
             "KHC LRCX LIN LITE MAR MRVL MELI META MCHP MU MSFT MSTR MDLZ MPWR MNST NFLX NVDA NXPI "
             "ORLY ODFL PCAR PLTR PANW PAYX PYPL PDD PEP QCOM REGN ROP ROST SNDK STX SHOP SBUX SNPS "
             "TMUS TTWO TSLA TXN TRI VRSK VRTX WMT WBD WDC WDAY XEL ZS").split()


def _norm(t: str) -> str:
    # SEC ใช้สัญลักษณ์แบบ BRK-B (ไม่ใช่ BRK.B) — แปลงจุดเป็นขีดให้ตรง
    return t.strip().upper().replace(".", "-")


def fetch_csv_column(url: str, col_names: list[str]) -> list[str]:
    res = httpx.get(url, headers=HEADERS, timeout=60, follow_redirects=True)
    res.raise_for_status()
    reader = csv.DictReader(io.StringIO(res.text))
    field = next((c for c in reader.fieldnames or [] if c.strip() in col_names), None)
    if not field:
        raise RuntimeError(f"ไม่พบคอลัมน์ {col_names} ใน {url} (เจอ {reader.fieldnames})")
    out = []
    for row in reader:
        t = _norm(row.get(field, ""))
        if t:
            out.append(t)
    return out


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print("ดึง S&P 500 ...")
    sp500 = sorted(set(fetch_csv_column(SP500_CSV, ["Symbol", "Ticker"])))
    print(f"  → {len(sp500)} ตัว")
    print("ดึง Russell 2000 ...")
    russell = sorted(set(fetch_csv_column(RUSSELL_CSV, ["Ticker", "Symbol"])))
    print(f"  → {len(russell)} ตัว")
    nasdaq100 = sorted(set(_norm(t) for t in NASDAQ100))
    print(f"Nasdaq-100 (snapshot) → {len(nasdaq100)} ตัว")

    union = sorted(set(sp500) | set(nasdaq100) | set(russell))
    members = {
        "sp500": sp500,
        "nasdaq100": nasdaq100,
        "russell2000": russell,
        "counts": {"sp500": len(sp500), "nasdaq100": len(nasdaq100),
                   "russell2000": len(russell), "union": len(union)},
    }
    (OUT_DIR / "index_members.json").write_text(
        json.dumps(members, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT_DIR / "index_tickers.txt").write_text("\n".join(union) + "\n", encoding="utf-8")
    print(f"\nรวม union = {len(union)} ตัว (ไม่ซ้ำ)")
    print(f"เขียน: {OUT_DIR / 'index_members.json'}")
    print(f"เขียน: {OUT_DIR / 'index_tickers.txt'}")
    print("\nต่อไป: build_offline_db.py --all --tickers-file data/financials/index_tickers.txt")


if __name__ == "__main__":
    main()
