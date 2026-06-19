"""ดึง + แปลงรายชื่อสมาชิกดัชนีไทย (SET50 / SET100 / SETHD) จาก PDF ทางการของ SET.

SET เผยแพร่ไฟล์ constituents เป็น PDF สาธารณะที่ media.set.or.th (ปรับสมาชิกทุกครึ่งปี).
สคริปต์นี้โหลด PDF → อ่านตาราง → เขียน data/thai/set_indices.json
(รูปแบบ {"set50":[...], "set100":[...], "sethd":[...]}) ให้ build_thai_db.py ใช้ต่อ.

หมายเหตุ: mai เป็น "กระดาน" ไม่มี PDF constituents (และ API ของ SET ถูกบล็อก) →
ถ้าต้องการ mai ให้เตรียมไฟล์รายชื่อเอง แล้วใช้ build_thai_db.py --tickers-file.

อัปเดต URL ด้านล่างเมื่อ SET ออกลิสต์รอบใหม่ (ดูที่ www.set.or.th → Market → Constituents).

ใช้:
    backend\\.venv\\Scripts\\python backend\\scripts\\fetch_thai_indices.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[2]
THAI_DIR = ROOT / "data" / "thai"

# ลิสต์รอบล่าสุด (อัปเดตเมื่อ SET ออกรอบใหม่)
SET50_100_PDF = "https://media.set.or.th/set/Documents/2025/Dec/SET50_100_H1_2026.pdf"
SETHD_PDF = "https://media.set.or.th/set/Documents/2025/Jun/SETHD_H2_2025.pdf"
_UA = {"User-Agent": "Mozilla/5.0"}


def download(url: str, dest: Path) -> Path:
    r = httpx.get(url, headers=_UA, timeout=60, follow_redirects=True)
    r.raise_for_status()
    dest.write_bytes(r.content)
    return dest


def _tables(path: Path) -> list[list[tuple[int, str]]]:
    """อ่านแถว '<no> <SYMBOL> <ชื่อ...>' แล้วตัดเป็นตารางทุกครั้งที่เลขลำดับกลับมาเป็น 1."""
    rows: list[tuple[int, str]] = []
    for pg in PdfReader(str(path)).pages:
        for ln in (pg.extract_text() or "").splitlines():
            m = re.match(r"^(\d+)\s+([A-Z][A-Z0-9&.\-]{0,9})\s+\S", ln.strip())
            if m:
                rows.append((int(m.group(1)), m.group(2)))
    tbls, cur = [], []
    for n, s in rows:
        if n == 1 and cur:
            tbls.append(cur)
            cur = []
        cur.append((n, s))
    if cur:
        tbls.append(cur)
    return tbls


def _syms(t: list[tuple[int, str]]) -> list[str]:
    seen, out = set(), []
    for _, s in t:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _pick(tbls, lo, hi):
    """เลือกตารางที่จำนวนสมาชิกสูงสุดอยู่ในช่วง [lo, hi]."""
    cand = [t for t in tbls if lo <= max(n for n, _ in t) <= hi]
    if not cand:
        raise RuntimeError(f"ไม่พบตารางขนาด {lo}-{hi} ใน PDF (อาจเปลี่ยน layout)")
    return _syms(max(cand, key=lambda t: max(n for n, _ in t)))


def main() -> None:
    THAI_DIR.mkdir(parents=True, exist_ok=True)
    print("ดาวน์โหลด PDF จาก SET ...")
    p1 = download(SET50_100_PDF, THAI_DIR / "SET50_100.pdf")
    p2 = download(SETHD_PDF, THAI_DIR / "SETHD.pdf")

    t1 = _tables(p1)
    set50 = _pick(t1, 45, 55)
    set100 = _pick(t1, 90, 105)
    sethd = _pick(_tables(p2), 25, 45)

    out = {"set50": set50, "set100": set100, "sethd": sethd}
    (THAI_DIR / "set_indices.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"SET50={len(set50)}  SET100={len(set100)}  SETHD={len(sethd)}")
    print(f"เขียน: {THAI_DIR / 'set_indices.json'}")
    print("\nต่อไป: build_thai_db.py --indices set50,set100,sethd")


if __name__ == "__main__":
    main()
