"""สร้างฐานข้อมูลปัจจัยพื้นฐานออฟไลน์ของหุ้นไทย (SET50/SET100/SETHD/mai) ผ่าน Yahoo.

หุ้นไทยไม่ได้ยื่น SEC แบบ us-gaap → ใช้คนละสายกับหุ้น US:
ดึง snapshot ปัจจัยพื้นฐานจาก Yahoo (.BK) แล้วเก็บลง backend/app/offline_fundamentals.json
(ไฟล์เดียวกับที่ปุ่ม "อัปเดตขึ้นเว็ป" ในแอปใช้ → เปิดแอปอ่านได้ทันที).

อ่านรายชื่อจากไฟล์ JSON ที่ fetch_thai_indices.py สร้าง (data/thai/set_indices.json)
หรือไฟล์ ticker ธรรมดา (1 ตัว/บรรทัด). เติม .BK ให้อัตโนมัติถ้ายังไม่มี.

Yahoo โดน rate-limit ง่าย → หน่วงระหว่างเรียก + retry + resume (ข้ามตัวที่มีใน
offline_fundamentals.json และใหม่กว่า --refresh-days วัน).

ตัวอย่าง (รันในเครื่อง — Yahoo เข้าถึงหุ้นไทยได้เฉพาะในเครื่อง ไม่ใช่บนเว็ป):
    backend\\.venv\\Scripts\\python backend\\scripts\\build_thai_db.py --indices set50,set100,sethd
    backend\\.venv\\Scripts\\python backend\\scripts\\build_thai_db.py --tickers-file mylist.txt
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.fundamentals import (fetch_yahoo_fundamentals, save_offline,  # noqa: E402
                              load_offline, _fetch_sync)

THAI_DIR = ROOT / "data" / "thai"
INDICES_JSON = THAI_DIR / "set_indices.json"


def with_bk(t: str) -> str:
    t = t.strip().upper()
    if not t:
        return ""
    # หุ้นไทยบน Yahoo ใช้ .BK; ถ้าใส่มาแล้ว/เป็นหุ้นต่างประเทศก็ไม่ยุ่ง
    return t if ("." in t) else f"{t}.BK"


def collect_symbols(args) -> list[str]:
    syms: list[str] = []
    if args.tickers_file:
        for ln in Path(args.tickers_file).read_text(encoding="utf-8").splitlines():
            if ln.strip() and not ln.startswith("#"):
                syms.append(ln.strip())
    else:
        data = json.loads(INDICES_JSON.read_text(encoding="utf-8"))
        for idx in [s.strip() for s in args.indices.split(",") if s.strip()]:
            syms.extend(data.get(idx, []))
    # เติม .BK + ลบซ้ำคงลำดับ
    seen, out = set(), []
    for s in syms:
        b = with_bk(s)
        if b and b not in seen:
            seen.add(b)
            out.append(b)
    return out


async def fetch(sym: str):
    """Yahoo httpx ก่อน (ทน path ไทย); ถ้าพลาดลอง yfinance."""
    try:
        return await fetch_yahoo_fundamentals(sym)
    except Exception:
        return await asyncio.to_thread(_fetch_sync, sym)


def _has_quant(snap: dict) -> bool:
    return any(snap.get(k) is not None for k in ("pe", "eps", "market_cap", "roe", "bvps"))


async def main() -> None:
    ap = argparse.ArgumentParser(description="สร้างฐานพื้นฐานหุ้นไทยออฟไลน์ (Yahoo)")
    ap.add_argument("--indices", type=str, default="set50,set100,sethd",
                    help="ดัชนีจาก set_indices.json คั่นจุลภาค (set50,set100,sethd,mai)")
    ap.add_argument("--tickers-file", type=str, default="",
                    help="ไฟล์รายชื่อ ticker (1 ตัว/บรรทัด) แทนการใช้ดัชนี")
    ap.add_argument("--delay", type=float, default=1.5, help="หน่วงระหว่างตัว (วินาที) กัน Yahoo บล็อก")
    ap.add_argument("--refresh-days", type=int, default=7, help="มีใน snapshot ใหม่กว่า N วัน → ข้าม")
    args = ap.parse_args()

    syms = collect_symbols(args)
    total = len(syms)
    print(f"หุ้นไทยที่จะดึง: {total} ตัว (delay {args.delay}s/ตัว)")
    existing = load_offline()
    cutoff = args.refresh_days * 24 * 3600
    done = skip = fail = 0
    failures = []
    t0 = time.time()

    for i, sym in enumerate(syms, 1):
        prev = existing.get(sym)
        if prev and prev.get("fetched_at") and time.time() - prev["fetched_at"] < cutoff and _has_quant(prev):
            skip += 1
            continue
        try:
            snap = await fetch(sym)
            if not _has_quant(snap):
                raise RuntimeError("Yahoo คืนข้อมูลว่าง")
            save_offline(sym, snap)
            done += 1
            print(f"  [{i}/{total}] {sym:<12} ✓ {snap.get('long_name','')[:40]}")
        except Exception as exc:  # noqa: BLE001
            fail += 1
            failures.append(sym)
            print(f"  [{i}/{total}] {sym:<12} ✗ {str(exc)[:50]}")
        await asyncio.sleep(args.delay)

    if failures:
        (THAI_DIR / "thai_failures.json").write_text(
            json.dumps(failures, ensure_ascii=False, indent=1), encoding="utf-8")
    mins = (time.time() - t0) / 60
    print(f"\nเสร็จใน {mins:.1f} นาที — ใหม่ {done}, ข้าม {skip}, พลาด {fail} "
          f"(รวมในฐาน {len(load_offline())} ตัว)")
    if failures:
        print(f"พลาด {fail} ตัว (บันทึก {THAI_DIR/'thai_failures.json'}) — รันซ้ำเพื่อลองใหม่:")
        print("  " + ", ".join(failures[:20]) + ("..." if len(failures) > 20 else ""))


if __name__ == "__main__":
    asyncio.run(main())
