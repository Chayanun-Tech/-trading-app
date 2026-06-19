"""สร้างฐานข้อมูลงบการเงินออฟไลน์ของหุ้น US ทั้งตลาด (จาก SEC EDGAR).

แนวคิด (ตามที่อาจารย์ต้องการ): ดาวน์โหลดงบของหุ้นทุกตัวมาเก็บไว้ก่อนล่วงหน้า
→ เวลาเปิดแอปเรียกดูหุ้นตัวไหน ระบบอ่านจากฐานออฟไลน์ได้ทันที (ไม่ต้องรอเน็ต/ไม่โดน SEC บล็อก).
ตัวที่ยังไม่มีในฐาน ค่อยกดปุ่มอัปเดตในแอปให้ไปดึงสดมาเก็บเพิ่มทีหลัง.

ดึงไฟล์ companyfacts (XBRL งบย้อนหลังลึก) ของแต่ละ CIK เก็บเป็น
    data/financials/facts_<cik>.json
ซึ่งเป็นรูปแบบเดียวกับที่ backend/app/edgar.py อ่าน → เสิร์ฟได้ทันทีไม่ต้องแปลงอะไร.

คุณสมบัติ:
- จำกัดอัตราเรียก ≤ SEC อนุญาต (ดีฟอลต์ ~7 req/วินาที, SEC เพดาน 10).
- resume ได้: ข้ามตัวที่ดาวน์โหลดสด (ใหม่กว่า --refresh-days วัน) ไปแล้ว.
- retry อัตโนมัติเมื่อพลาด, log ตัวที่ล้มเหลวลง failures.json.
- เขียน manifest.json (ticker→cik/ชื่อ/เวลาดาวน์โหลด) ไว้ให้แอป/คนสอนเปิดดูได้.

ตัวอย่างการใช้ (รันในเครื่อง จาก root ของ trading-app):
    # ทดลองก่อน 50 ตัวแรก
    python backend/scripts/build_offline_db.py --limit 50
    # ดาวน์โหลดทั้งตลาด (ใช้เวลาเป็นชั่วโมง + พื้นที่หลาย GB)
    python backend/scripts/build_offline_db.py --all
    # รันต่อจากที่ค้างไว้ (ข้ามตัวที่ดาวน์โหลดภายใน 30 วันล่าสุด)
    python backend/scripts/build_offline_db.py --all --refresh-days 30
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / "data" / "financials"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
# SEC บังคับระบุชื่อแอป + อีเมลจริงใน User-Agent ไม่งั้น 403 (ตรงกับ edgar.py)
HEADERS = {
    "User-Agent": "ChayanunOperating-trading-app chayanun250841@gmail.com",
    "Accept-Encoding": "gzip, deflate",
}
MANIFEST = CACHE_DIR / "manifest.json"
FAILURES = CACHE_DIR / "failures.json"


def facts_path(cik: str) -> Path:
    return CACHE_DIR / f"facts_{str(cik).zfill(10)}.json"


async def load_tickers(client: httpx.AsyncClient) -> list[dict]:
    """โหลดตาราง ticker→CIK จาก SEC (cache 30 วันในไฟล์เดิมที่ edgar ใช้)."""
    cache = CACHE_DIR / "company_tickers.json"
    data = None
    if cache.exists() and time.time() - cache.stat().st_mtime < 30 * 24 * 3600:
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
    if data is None:
        res = await client.get(TICKERS_URL)
        res.raise_for_status()
        data = res.json()
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data), encoding="utf-8")
    rows = []
    for row in data.values():
        rows.append({
            "ticker": str(row["ticker"]).upper(),
            "cik": str(row["cik_str"]).zfill(10),
            "title": row.get("title", ""),
        })
    # เรียงตาม ticker ให้ลำดับการดาวน์โหลดคงที่ (resume ง่าย)
    rows.sort(key=lambda r: r["ticker"])
    return rows


async def fetch_one(client: httpx.AsyncClient, cik: str, retries: int = 3) -> dict | None:
    for attempt in range(retries):
        try:
            res = await client.get(FACTS_URL.format(cik=cik))
            if res.status_code == 404:
                return None  # บางบริษัทไม่มี companyfacts (เพิ่งจดทะเบียน/ไม่ยื่น XBRL)
            res.raise_for_status()
            return res.json()
        except Exception:  # noqa: BLE001
            if attempt == retries - 1:
                raise
            await asyncio.sleep(2 * (attempt + 1))
    return None


async def main() -> None:
    ap = argparse.ArgumentParser(description="สร้างฐานงบการเงินออฟไลน์ของหุ้น US (SEC EDGAR)")
    ap.add_argument("--all", action="store_true", help="ดาวน์โหลดทั้งตลาด")
    ap.add_argument("--limit", type=int, default=0, help="จำกัดจำนวนตัว (ทดลอง)")
    ap.add_argument("--rate", type=float, default=7.0, help="req/วินาที (SEC เพดาน 10)")
    ap.add_argument("--refresh-days", type=int, default=30,
                    help="ถ้ามีไฟล์ใหม่กว่า N วัน จะข้าม (resume)")
    ap.add_argument("--only", type=str, default="",
                    help="เจาะจง ticker คั่นด้วยจุลภาค เช่น AAPL,MSFT")
    ap.add_argument("--tickers-file", type=str, default="",
                    help="ไฟล์รายชื่อ ticker (1 ตัว/บรรทัด) เช่นจาก fetch_index_members.py")
    args = ap.parse_args()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    delay = 1.0 / max(args.rate, 0.5)
    fresh_cutoff = args.refresh_days * 24 * 3600

    async with httpx.AsyncClient(timeout=90, headers=HEADERS) as client:
        rows = await load_tickers(client)
        want = None
        if args.only:
            want = {s.strip().upper() for s in args.only.split(",") if s.strip()}
        elif args.tickers_file:
            txt = Path(args.tickers_file).read_text(encoding="utf-8")
            want = {ln.strip().upper() for ln in txt.splitlines()
                    if ln.strip() and not ln.startswith("#")}
        if want is not None:
            have = {r["ticker"] for r in rows}
            missing = sorted(want - have)
            rows = [r for r in rows if r["ticker"] in want]
            if missing:
                print(f"หมายเหตุ: {len(missing)} ตัวในรายการไม่พบใน SEC (อาจ delist/เปลี่ยนสัญลักษณ์): "
                      + ", ".join(missing[:15]) + ("..." if len(missing) > 15 else ""))
        elif not args.all:
            rows = rows[: args.limit or 50]
        elif args.limit:
            rows = rows[: args.limit]

        total = len(rows)
        manifest = {}
        if MANIFEST.exists():
            try:
                manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                manifest = {}
        failures = {}
        done = skipped = empty = failed = 0
        t0 = time.time()
        print(f"เริ่มสร้างฐานออฟไลน์: {total} ตัว → {CACHE_DIR}")

        for i, r in enumerate(rows, 1):
            cik, tkr = r["cik"], r["ticker"]
            path = facts_path(cik)
            if path.exists() and time.time() - path.stat().st_mtime < fresh_cutoff:
                skipped += 1
                manifest.setdefault(tkr, {"cik": cik, "title": r["title"],
                                         "downloaded_at": int(path.stat().st_mtime)})
                continue
            try:
                data = await fetch_one(client, cik)
            except Exception as exc:  # noqa: BLE001
                failed += 1
                failures[tkr] = str(exc)[:200]
                print(f"  [{i}/{total}] {tkr:<8} ✗ {str(exc)[:60]}")
                await asyncio.sleep(delay)
                continue
            if data is None:
                empty += 1
            else:
                path.write_text(json.dumps(data), encoding="utf-8")
                done += 1
                manifest[tkr] = {"cik": cik, "title": r["title"],
                                 "downloaded_at": int(time.time())}
            if i % 50 == 0 or i == total:
                rate = i / max(time.time() - t0, 0.001)
                print(f"  [{i}/{total}] ใหม่={done} ข้าม={skipped} ว่าง={empty} "
                      f"พลาด={failed}  ({rate:.1f}/s)")
                MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                                    encoding="utf-8")
            await asyncio.sleep(delay)

        MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
        if failures:
            FAILURES.write_text(json.dumps(failures, ensure_ascii=False, indent=1),
                                encoding="utf-8")
        mins = (time.time() - t0) / 60
        print(f"\nเสร็จใน {mins:.1f} นาที — ในฐานตอนนี้รวม "
              f"{sum(1 for _ in CACHE_DIR.glob('facts_*.json'))} ตัว "
              f"(ใหม่ {done}, ข้าม {skipped}, ไม่มีงบ {empty}, พลาด {failed})")
        if failures:
            print(f"ตัวที่พลาดบันทึกไว้ที่ {FAILURES} — รันซ้ำคำสั่งเดิมเพื่อลองใหม่")


if __name__ == "__main__":
    asyncio.run(main())
