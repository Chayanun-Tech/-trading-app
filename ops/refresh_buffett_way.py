"""อัปเดตข้อมูลทั้งหมดของหน้า "พอร์ตวิถี Buffett" ในคำสั่งเดียว — รันทุกไตรมาสหลังงบออก (หรือบ่อยกว่านั้นได้)

ลำดับ:
  1. ops/export_tradingview_iv.py   มูลค่าที่แท้จริงของ S&P 500 + Nasdaq-100 + หุ้นโฟกัส (ดึงงบ SEC ใหม่ · ~15–40 นาที)
  2. ops/megatrend_screen.py        คุณภาพ 6 ข้อ + Mega Trend
  3. ops/market_climate.py          บรรยากาศตลาด (ราคา/ข่าว/ดอกเบี้ย/มูลค่าตลาด · ~1–3 นาที)
  ข้ามขั้นที่ 1 ได้ด้วย --skip-valuation (อัปเดตแค่บรรยากาศตลาด เช่นรายสัปดาห์)
รัน (จากโฟลเดอร์ trading-app):  python ops/refresh_buffett_way.py
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "data" / ".refresh_running"   # กันรันซ้อน (ปุ่มในหน้าเว็บเช็กไฟล์นี้)


def pid_alive(pid: int) -> bool:
    """เช็กว่าโปรเซสยังอยู่ไหม แบบไม่ส่งสัญญาณ (บน Windows os.kill(pid, 0) จะ "ฆ่า" โปรเซส — ห้ามใช้)."""
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return bool(ok) and code.value == 259            # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def step(name: str, args: list[str]) -> None:
    print(f"\n▶ {name}", flush=True)
    t0 = time.time()
    r = subprocess.run([sys.executable, "-W", "ignore", *args], cwd=ROOT)
    if r.returncode != 0:
        raise SystemExit(f"✗ {name} ล้มเหลว (exit {r.returncode})")
    print(f"✓ {name} ({time.time() - t0:.0f} วินาที)", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-valuation", action="store_true", help="ไม่สแกนมูลค่าใหม่ อัปเดตแค่บรรยากาศตลาด")
    a = ap.parse_args()
    if LOCK.exists():
        try:
            pid = int(LOCK.read_text().split()[0])
        except (ValueError, OSError):
            pid = 0
        if pid and pid_alive(pid):
            raise SystemExit(f"✗ มีการอัปเดตกำลังรันอยู่แล้ว (pid {pid}) — รอให้เสร็จก่อน")
        # ไม่งั้นเป็น lock ค้างจากรอบที่พัง — รันต่อได้
    LOCK.write_text(f"{os.getpid()} {time.strftime('%Y-%m-%d %H:%M:%S')}")
    try:
        _run(a)
    finally:
        LOCK.unlink(missing_ok=True)


def _run(a) -> None:
    if not a.skip_valuation:
        step("มูลค่าที่แท้จริง (สแกนงบ SEC ใหม่)", ["ops/export_tradingview_iv.py"])
        step("คุณภาพ 6 ข้อ + Mega Trend", ["ops/megatrend_screen.py"])
    step("บรรยากาศตลาด", ["ops/market_climate.py"])
    print("\nเสร็จ — เปิดแท็บ 🏛️ พอร์ตวิถี Buffett ในแอปแล้วกด ↻ โหลดใหม่")


if __name__ == "__main__":
    main()
