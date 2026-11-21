"""พอร์ตวิถี Buffett — รวมผลวิจัย (มูลค่าที่แท้จริง + คุณภาพ 6 ข้อ + บรรยากาศตลาด) เป็นคำแนะนำเชิงกฎ

กฎ (ผ่าน backtest point-in-time 2013–2026 ใน ops/backtest_buffett_way.py):
  ซื้อ      ธุรกิจผ่านคุณภาพครบ 6/6 + มูลค่าที่แท้จริง ÷ ราคา ≥ เกณฑ์ส่วนเผื่อ + ผ่านกฎความน่าเชื่อจาก backtest ระยะ 2
            (มูลค่าแบบรายตัว valuation_v2: ไม่ให้สัญญาณซื้อกับหุ้นอิ่มตัว/ยังไม่ทำกำไร/ความน่าเชื่อต่ำ — ถูกแล้วมักเป็นกับดัก)
  เกณฑ์     ปกติ ≥ 1.0 · ตลาดแพงทั้งกระดาน (มิติมูลค่า < 20) ≥ 1.25 และลงเงินใหม่แค่ครึ่ง เก็บที่เหลือเป็นเงินสด
            ตลาดกลัว (คะแนนรวม ≥ 70) ≥ 0.9 และทุ่มเงินสดที่เก็บไว้ทั้งหมด
  ถือ       กระจุก 15–20 ตัว (ทดสอบแล้ว 20 ตัวผลนิ่งที่สุด) · ถือยาว
  ขาย       เฉพาะเมื่อธุรกิจเสื่อม (คุณภาพเหลือ ≤ 3/6) — ไม่ขายเพราะราคาแพง (backtest: กฎขายตอนแพงทำให้ผลแย่ลงมาก)
ไฟล์ข้อมูล (สร้างโดย ops/refresh_buffett_way.py):
  data/intrinsic_ranges.json · data/megatrend_quality.json · data/climate/climate_results.json · data/backtest/buffett_way_summary.json
ผลลัพธ์เป็นการคัดกรองด้วยกฎ ไม่ใช่คำแนะนำการลงทุน — Buffett ยังเลือกเฉพาะธุรกิจที่เข้าใจลึก (circle of competence) อีกชั้น
"""
from __future__ import annotations

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"

RULES = {"max_hold": 20, "min_quality": 6, "mos_normal": 1.0, "mos_expensive": 1.25, "mos_fear": 0.9,
         "deploy_expensive": 0.5, "fear_level": 70, "expensive_level": 20, "sell_quality": 3,
         "suspicious_ratio": 3.0}


def _load(path: Path) -> dict | list | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _mtime(path: Path) -> str | None:
    try:
        return time.strftime("%Y-%m-%d", time.localtime(path.stat().st_mtime))
    except OSError:
        return None


def market_regime(now: dict | None) -> dict:
    comp = (now or {}).get("รวม")
    val = (now or {}).get("มูลค่า")
    if comp is not None and comp >= RULES["fear_level"]:
        return {"key": "fear", "label": "ตลาดกลัว — จังหวะ \"greedy when others are fearful\"",
                "mos": RULES["mos_fear"], "deploy": 1.0,
                "advice": "ผ่อนเกณฑ์ส่วนเผื่อลงเล็กน้อย และลงเงินสดที่เก็บไว้ทั้งหมดในหุ้นที่ผ่านเกณฑ์"}
    if val is not None and val < RULES["expensive_level"]:
        return {"key": "expensive", "label": "ตลาดแพงทั้งกระดาน — ระวังแบบ Buffett",
                "mos": RULES["mos_expensive"], "deploy": RULES["deploy_expensive"],
                "advice": "ต้องการส่วนเผื่อมากขึ้น (ราคาต่ำกว่ามูลค่า ≥ 25%) และลงเงินใหม่แค่ครึ่ง อีกครึ่งเก็บเป็นเงินสด (พันธบัตร/กองทุนตลาดเงิน) รอตลาดกลัว"}
    return {"key": "normal", "label": "ตลาดปกติ", "mos": RULES["mos_normal"], "deploy": 1.0,
            "advice": "ลงเงินใหม่ในหุ้นที่ราคาไม่เกินมูลค่าที่แท้จริง"}


def _row(sym: str, r: dict, q: dict, mq: dict) -> dict:
    vr = r["fair_price"] / r["price"] if r.get("price") else None
    return {
        "symbol": sym, "name": r.get("name") or sym, "price": r.get("price"), "fair": r.get("fair_price"),
        "low": r.get("low"), "high": r.get("high"), "grade": r.get("grade"),
        "value_ratio": round(vr, 3) if vr else None,
        "mos_pct": round((1 - 1 / vr) * 100, 1) if vr and vr > 0 else None,   # ส่วนลดจากมูลค่า
        "implied_growth": r.get("implied_growth_pct"), "hist_growth": r.get("hist_growth_pct"),
        "quality": q.get("passed"), "checks": q.get("checks"), "fcf_margin": q.get("fcf_margin_pct"),
        "roe": q.get("roe_pct"), "rev_cagr": q.get("rev_cagr_pct"), "net_debt_fcf": q.get("net_debt_to_fcf"),
        "trends": mq.get("trends", []), "leader_in": mq.get("leader_in", []),
        "archetype": r.get("archetype"), "archetype_label": r.get("archetype_label"),
        "confidence_level": r.get("confidence_level"), "source": r.get("source"), "method": r.get("method"),
        "buy_allowed": r.get("buy_allowed", r.get("grade") != "C"), "no_buy_reason": r.get("no_buy_reason"),
        "discount_rate": r.get("discount_rate_pct"),
    }


def parse_symbols(raw: str | list | None) -> list[dict]:
    """แปลงรายชื่อหุ้นที่ส่งมาจากเบราว์เซอร์ ("AAPL,MSFT" หรือ list) → [{"symbol": ...}] — ไม่เก็บลงเซิร์ฟเวอร์
    (เว็บเปิดสาธารณะ: ถ้าเก็บไฟล์บนเซิร์ฟเวอร์ ผู้เข้าชมทุกคนจะเห็น/แก้พอร์ตเดียวกัน)."""
    items = raw.split(",") if isinstance(raw, str) else (raw or [])
    out, seen = [], set()
    for it in items:
        sym = str(it).strip().upper().replace(".", "-")
        if sym and sym not in seen and len(sym) <= 12 and len(out) < 50:
            seen.add(sym)
            out.append({"symbol": sym, "note": ""})
    return out


def build_report(holdings: list[dict] | None = None) -> dict:
    iv = _load(DATA / "intrinsic_ranges.json") or {}
    mq = (_load(DATA / "megatrend_quality.json") or {}).get("stocks", {})
    climate = _load(DATA / "climate" / "climate_results.json") or {}
    bt = _load(DATA / "backtest" / "buffett_way_summary.json") or {}
    now = climate.get("now") or {}
    regime = market_regime(now)
    thr = regime["mos"]

    rows = {r["symbol"]: r for r in iv.get("results", [])}
    buy, wait, blocked = [], [], []
    for sym, r in rows.items():
        m = mq.get(sym) or {}
        q = m.get("quality") or {}
        if not (q.get("long_term") and (q.get("passed") or 0) >= RULES["min_quality"]):
            continue
        row = _row(sym, r, q, m)
        if row["value_ratio"] is None:
            continue
        if not row["buy_allowed"]:
            if row["value_ratio"] >= thr:
                blocked.append(row)      # ดูถูก แต่ backtest บอกว่ากลุ่มนี้ "ถูก" มักเป็นกับดัก
            continue
        if row["value_ratio"] >= thr:
            row["flags"] = (["ส่วนลดสูงผิดปกติ — ตรวจซ้ำ (ความเสี่ยงที่โมเดลมองไม่เห็น / กำไรตามวัฏจักร)"]
                            if row["value_ratio"] >= RULES["suspicious_ratio"] else [])
            buy.append(row)
        else:
            row["buy_below"] = round(r["fair_price"] / thr, 2)   # ราคาที่จะเข้าเกณฑ์
            row["gap_pct"] = round((row["buy_below"] / r["price"] - 1) * 100, 1)
            wait.append(row)
    buy.sort(key=lambda x: -x["value_ratio"])
    wait.sort(key=lambda x: -x["value_ratio"])
    blocked.sort(key=lambda x: -x["value_ratio"])

    requested = holdings or []
    holdings = []
    for h in requested:
        sym = h["symbol"]
        r, m = rows.get(sym), mq.get(sym) or {}
        q = m.get("quality") or {}
        if not r:
            status, tone = "ไม่มีข้อมูลในสแกน (อาจไม่อยู่ใน S&P 500/Nasdaq-100 หรือยังไม่มีกำไร)", "muted"
            item = {"symbol": sym}
        else:
            item = _row(sym, r, q, m)
            qp = q.get("passed")
            if qp is not None and qp <= RULES["sell_quality"]:
                status, tone = "ธุรกิจเสื่อม — ทบทวนเหตุผลที่ถือ (กฎวิถี Buffett: ขายได้)", "bad"
            elif any(b["symbol"] == sym for b in buy):
                status, tone = "ยังถูก — เติมเงินได้", "good"
            elif not item.get("buy_allowed"):
                status, tone = "ถือต่อ แต่ไม่ควรเติมเพราะดูถูก — " + (item.get("no_buy_reason") or ""), "ok"
            elif (item["value_ratio"] or 0) < 0.5:
                status, tone = "แพงมาก แต่ถือต่อ (วิถี Buffett ไม่ขายเพราะราคา)", "warn"
            else:
                status, tone = "ถือต่อ", "ok"
        holdings.append({**item, "note": h.get("note", ""), "status": status, "tone": tone})

    held = {h["symbol"] for h in holdings}
    slots = max(0, RULES["max_hold"] - len(held))
    new_ideas = [b for b in buy if b["symbol"] not in held][:slots]

    keep_names = ("ขายเฉพาะธุรกิจเสื่อม · 20 ตัว", "ขายเฉพาะธุรกิจเสื่อม · 15 ตัว", "วิถี Buffett (เต็มกฎ)", "ไม่ขายเลย (ถือตลอด)")
    backtest = {k: {"vs_sp": round(bt[k]["final_vs_sp"], 3), "avg_cash": bt[k]["avg_cash"],
                    "values": bt[k].get("values", [])}
                for k in keep_names if k in bt}

    return {
        "rules": RULES, "regime": regime,
        "climate": {"as_of": climate.get("as_of"), "now": {k: now.get(k) for k in ("รวม", "มูลค่า", "ความกลัว/ข่าว", "ราคา/ความกว้าง", "ภาวะการเงิน")}},
        "buy": buy, "new_ideas": new_ideas, "wait": wait[:40], "blocked": blocked[:30], "holdings": holdings,
        "counts": {"buy": len(buy), "wait": len(wait), "blocked": len(blocked), "held": len(held), "slots": slots},
        "backtest": backtest,
        "data_as_of": {"valuation": iv.get("as_of"), "quality": _mtime(DATA / "megatrend_quality.json"),
                       "climate": climate.get("as_of"), "backtest": _mtime(DATA / "backtest" / "buffett_way_summary.json")},
        "disclaimer": "คัดกรองด้วยกฎจากผลวิจัย ไม่ใช่คำแนะนำการลงทุน · Buffett เลือกเฉพาะธุรกิจที่เข้าใจลึกจากรายชื่อนี้อีกชั้น",
    }


# ── ปุ่ม "ดึงข้อมูลล่าสุด" ในหน้าเว็บ — รันสคริปต์อัปเดตเป็นงานเบื้องหลัง (เฉพาะแอปที่รันในเครื่อง) ─────────────
import os  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402

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


REFRESH_LOG = DATA / "refresh_log.txt"
PINE_FILE = ROOT / "tradingview" / "intrinsic_value.pine"
_job: dict = {"proc": None, "mode": None, "started": None}
MODES = {"quick": {"args": ["--skip-valuation"], "label": "อัปเดตบรรยากาศตลาด", "eta_min": 3},
         "full": {"args": [], "label": "สแกนมูลค่า + คุณภาพ + บรรยากาศตลาดใหม่ทั้งหมด", "eta_min": 45}}


def can_refresh() -> bool:
    """สแกนได้เฉพาะแอปที่รันในเครื่อง (มี .git + สคริปต์ ops/) — บนเว็บ (HF Space) ไม่มีสคริปต์และไฟล์จะหายเมื่อรีสตาร์ท."""
    return (ROOT / ".git").exists() and (ROOT / "ops" / "refresh_buffett_way.py").exists()


def refresh_status() -> dict:
    p = _job["proc"]
    running = p is not None and p.poll() is None
    code = None if p is None or running else p.returncode
    try:
        lines = REFRESH_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    steps = [ln for ln in lines if ln.startswith(("▶", "✓", "✗", "เสร็จ", "สแกน", "Pine"))]
    return {"available": can_refresh(), "running": running, "mode": _job["mode"],
            "mode_label": MODES.get(_job["mode"] or "", {}).get("label"), "started": _job["started"],
            "exit_code": code, "ok": code == 0 if code is not None else None,
            "steps": steps[-8:], "tail": lines[-3:],
            "pine_updated": _mtime(PINE_FILE), "eta_min": MODES.get(_job["mode"] or "", {}).get("eta_min")}


def start_refresh(mode: str) -> dict:
    if mode not in MODES:
        raise ValueError("mode ต้องเป็น quick หรือ full")
    if not can_refresh():
        raise PermissionError("ปุ่มนี้ใช้ได้เฉพาะแอปที่รันในเครื่อง — บนเว็บให้รันในเครื่องแล้วเอาขึ้นเว็บ")
    if _job["proc"] is not None and _job["proc"].poll() is None:
        return refresh_status()                    # กำลังรันอยู่แล้ว ไม่เริ่มซ้ำ
    lock = DATA / ".refresh_running"
    if lock.exists():
        try:
            pid = int(lock.read_text().split()[0])
        except (ValueError, OSError):
            pid = 0
        if pid and pid_alive(pid):
            raise PermissionError("มีการอัปเดตที่เริ่มจากที่อื่น (เช่น command line) กำลังรันอยู่ — รอให้เสร็จก่อน")
    REFRESH_LOG.parent.mkdir(parents=True, exist_ok=True)
    log = REFRESH_LOG.open("w", encoding="utf-8")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    _job["proc"] = subprocess.Popen(
        [sys.executable, "-X", "utf8", "-W", "ignore", "ops/refresh_buffett_way.py", *MODES[mode]["args"]],
        cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, env=env)
    _job["mode"] = mode
    _job["started"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return refresh_status()


def pine_text() -> str | None:
    try:
        return PINE_FILE.read_text(encoding="utf-8")
    except OSError:
        return None
