"""รัน backtest วิธีนับรอบชุดเดียวกับ ops/backtest_cycles.py กับหุ้น SET100 (รายชื่อ H1/2026 จาก data/thai/set_indices.json)
เทียบดัชนี: ^SET.BK (ดัชนีราคา ไม่รวมปันผล) และ TDEX.BK (ETF SET50 — ราคาปรับปันผลแล้ว)
ข้อมูล: Yahoo รายวันปรับปันผล → แคชที่ data/backtest/daily_th
รัน: python ops/backtest_cycles_set100.py
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ops"))

import httpx  # noqa: E402

import backtest_cycles as bc  # noqa: E402

CACHE = ROOT / "data" / "backtest" / "daily_th"
OUT = ROOT / "data" / "backtest" / "cycles_set100_summary.json"
INDEX = ["^SET.BK", "TDEX.BK"]
_H = {"User-Agent": "Mozilla/5.0 (compatible; research-backtest/1.0)"}


async def fetch(sym, client):
    f = CACHE / f"th_{sym}.json"  # นำหน้า th_ กันชื่อสงวนของ Windows (เช่น COM7)
    if f.exists():
        return
    bars = []
    try:
        r = await client.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}", headers=_H, timeout=30,
                             params={"interval": "1d", "period1": int(dt.datetime(2000, 1, 1).timestamp()),
                                     "period2": int(dt.datetime.now().timestamp()), "includeAdjustedClose": "true"})
        res = (r.json().get("chart", {}).get("result") or [None])[0] if r.status_code == 200 else None
        if res and res.get("timestamp"):
            adj = (res["indicators"].get("adjclose") or [{}])[0].get("adjclose") \
                or (res["indicators"].get("quote") or [{}])[0].get("close") or []
            for t, a in zip(res["timestamp"], adj):
                if a:
                    bars.append([dt.datetime.fromtimestamp(t, dt.timezone.utc).date().isoformat(), a])
    except Exception:  # noqa: BLE001
        pass
    f.write_text(json.dumps(bars), encoding="utf-8")


async def main():
    syms = json.loads((ROOT / "data" / "thai" / "set_indices.json").read_text(encoding="utf-8"))["set100"]
    CACHE.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(6)
    async with httpx.AsyncClient() as c:
        async def one(s):
            async with sem:
                await fetch(s, c)
        await asyncio.gather(*(one(f"{s}.BK") for s in syms), *(one(s) for s in INDEX))
    got = sum(1 for f in CACHE.glob("*.json") if len(json.loads(f.read_text(encoding="utf-8"))) >= 1500)
    print(f"มีข้อมูลยาวพอ (≥ ~6 ปี) {got} ตัว (รวมดัชนี)")
    bc.main(cache=CACHE, index_stems=tuple(f"th_{s}" for s in INDEX), out=OUT)


if __name__ == "__main__":
    asyncio.run(main())
