"""ประวัติผลตอบแทนรายปีของ S&P 500 แบบ survivorship-aware (~1996–ปัจจุบัน).

ต่างจากสแกนตัวอื่นที่ดู "ปัจจุบัน" — โมดูลนี้ตอบคำถามเชิงประวัติศาสตร์: แต่ละปีหุ้นตัวไหนทำผลตอบแทน
เท่าไร จัดอันดับ 1..N, ใครยืนระยะติด Top X ได้นานที่สุด, และใครที่เคยรุ่งแล้วหลุดออกจากดัชนีไป.

หัวใจคือ "survivorship-aware": ใช้สมาชิกดัชนีแบบ point-in-time (รู้ว่าปีนั้น ๆ มีใครอยู่จริง รวมตัวที่
หลุดออกไปแล้ว) จากชุดข้อมูลเปิด fja05680/sp500 — ไม่ใช่รายชื่อสมาชิกวันนี้ ซึ่งจะทำให้เห็นแต่หุ้นที่รอด
(bias). ผลตอบแทนคิดแบบ total return (adjusted close รวมปันผล/ปรับ split) เทียบกับดัชนี ^SP500TR
(S&P 500 Total Return).

ข้อจำกัดที่ยอมรับตรง ๆ: หุ้นที่ล้มละลาย/ถูกถอดแล้ว Yahoo มักลบข้อมูลทิ้ง → บางปีจะครอบคลุมไม่ครบทุก
สมาชิก (แสดง coverage ให้เห็น). ลึกได้ ~1996 เพราะชุดข้อมูล point-in-time ฟรีมีถึงแค่นั้น.

Build: ยิง Yahoo ~1,200 ตัว (รายเดือน) ครั้งเดียวแล้ว commit ไฟล์ data_sp500_history.json ขึ้น repo —
เว็ป (HF Space) แค่เสิร์ฟไฟล์ ไม่ build สดบนคลาวด์.
"""
from __future__ import annotations

import asyncio
import csv
import datetime as dt
import json
import time
from pathlib import Path

import httpx

_APP_DIR = Path(__file__).resolve().parent
_CONSTITUENTS_CSV = _APP_DIR / "data_sp500hist" / "constituents_history.csv"
_CURRENT_CSV = _APP_DIR / "sp500_constituents.csv"
_DATASET = _APP_DIR / "data_sp500_history.json"
_BENCHMARK = "^SP500TR"  # S&P 500 Total Return (เทียบ total-return-to-total-return)
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AI-Trade-Assistant/1.0)"}
DEFAULT_BAND_PCT = 10.0  # เหนือ/ต่ำกว่าตลาดเมื่อผลตอบแทนต่างจากดัชนีเกิน ±band (percentage points)

# ── สมาชิกดัชนีแบบ point-in-time (จากชุดข้อมูลเปิด) ───────────────────────────────

_membership_cache: dict[int, set[str]] | None = None


def _year_end_membership() -> dict[int, set[str]]:
    """{ปี: set(ticker)} จาก snapshot สมาชิกล่าสุดที่ <= 31 ธ.ค. ของปีนั้น (สมาชิก ณ สิ้นปี)."""
    global _membership_cache
    if _membership_cache is not None:
        return _membership_cache
    rows: list[tuple[dt.date, set[str]]] = []
    with _CONSTITUENTS_CSV.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            d = dt.date.fromisoformat(r["date"])
            tickers = {t.strip().upper() for t in (r["tickers"] or "").split(",") if t.strip()}
            rows.append((d, tickers))
    rows.sort(key=lambda x: x[0])
    out: dict[int, set[str]] = {}
    if rows:
        for y in range(rows[0][0].year, rows[-1][0].year + 1):
            cutoff = dt.date(y, 12, 31)
            snap = None
            for d, s in rows:
                if d <= cutoff:
                    snap = s
                else:
                    break
            if snap:
                out[y] = snap
    _membership_cache = out
    return out


def _universe() -> set[str]:
    mem = _year_end_membership()
    return set().union(*mem.values()) if mem else set()


def _current_names() -> dict[str, str]:
    """ticker -> ชื่อบริษัท (เฉพาะสมาชิกปัจจุบัน — ตัวที่หลุดไปแล้วไม่มีชื่อ แสดงเป็น ticker)."""
    out: dict[str, str] = {}
    try:
        with _CURRENT_CSV.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                sym = (r.get("Symbol") or "").strip().upper().replace(".", "-")
                if sym:
                    out[sym] = (r.get("Security") or "").strip()
    except OSError:
        pass
    return out


# ── ดึงราคา (adjusted close รายเดือน → ผลตอบแทนรายปี) ────────────────────────────

async def _fetch_monthly_adjclose(sym: str, client: httpx.AsyncClient) -> dict[int, float]:
    """{ปี: adjusted close ของแท่งเดือนสุดท้ายในปี (≈ สิ้นปี)} — adjclose รวมปันผล/ปรับ split."""
    p1 = int(dt.datetime(1995, 1, 1).timestamp())
    p2 = int(dt.datetime.now().timestamp())
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
    params = {"interval": "1mo", "period1": p1, "period2": p2,
              "events": "div,splits", "includeAdjustedClose": "true"}
    r = await client.get(url, params=params, headers=_HEADERS, timeout=30)
    r.raise_for_status()
    res = (r.json().get("chart", {}).get("result") or [None])[0]
    if not res:
        return {}
    ts = res.get("timestamp") or []
    adj = ((res.get("indicators", {}).get("adjclose") or [{}])[0]).get("adjclose") or []
    closes = ((res.get("indicators", {}).get("quote") or [{}])[0]).get("close") or []
    by_year: dict[int, float] = {}
    for i, t in enumerate(ts):
        year = dt.datetime.utcfromtimestamp(t).year
        v = adj[i] if i < len(adj) and adj[i] is not None else (closes[i] if i < len(closes) else None)
        if v is not None and v > 0:
            by_year[year] = v  # แท่งเดือนหลัง ๆ ทับค่าเดิม → เหลือค่าสิ้นปี
    return by_year


def _annual_returns(by_year: dict[int, float]) -> dict[int, float]:
    """{ปี: ผลตอบแทน %} = ค่าสิ้นปี / ค่าสิ้นปีก่อน − 1 (ต้องมีค่าปีก่อนหน้า)."""
    out: dict[int, float] = {}
    for y in sorted(by_year):
        prev = by_year.get(y - 1)
        if prev and prev > 0:
            out[y] = round((by_year[y] / prev - 1) * 100, 1)
    return out


# ── Build dataset (รันในเครื่อง แล้ว push) ────────────────────────────────────────

async def build_dataset(concurrency: int = 8) -> dict:
    membership = _year_end_membership()
    universe = sorted(_universe())
    names = _current_names()

    # ผลตอบแทนดัชนี (total return) รายปี
    async with httpx.AsyncClient() as client:
        bench_by_year = await _fetch_monthly_adjclose(_BENCHMARK, client)
    index_returns = _annual_returns(bench_by_year)

    sem = asyncio.Semaphore(concurrency)
    returns_by_symbol: dict[str, dict[int, float]] = {}

    async def worker(sym: str):
        async with sem:
            async with httpx.AsyncClient() as client:
                try:
                    by = await _fetch_monthly_adjclose(sym, client)
                except Exception:  # noqa: BLE001 — ตัวเดียวพังไม่ล้มทั้ง build (เดลิสต์/ไม่มีข้อมูล)
                    return
            ar = _annual_returns(by)
            if ar:
                returns_by_symbol[sym] = ar

    await asyncio.gather(*(worker(s) for s in universe))

    # ประกอบผลรายปี: สมาชิกปีนั้น × ผลตอบแทนที่ดึงได้ → เรียงมาก→น้อย
    years_out: dict[str, dict] = {}
    for year, members in membership.items():
        rows = []
        for sym in members:
            ret = returns_by_symbol.get(sym, {}).get(year)
            if ret is not None:
                rows.append([sym, ret])
        rows.sort(key=lambda x: x[1], reverse=True)
        years_out[str(year)] = {
            "index_return": index_returns.get(year),
            "members": len(members),
            "covered": len(rows),
            "rows": rows,
        }

    payload = {
        "as_of": time.time(),
        "source": "fja05680/sp500 (point-in-time constituents) + Yahoo adjusted close",
        "benchmark": _BENCHMARK,
        "universe_size": len(universe),
        "return_basis": "total_return",
        "names": {s: n for s, n in names.items() if n},
        "years": years_out,
    }
    _DATASET.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


def can_build_live() -> bool:
    """build สดได้เฉพาะตอนรันในเครื่อง (มี .git) — บน HF เสิร์ฟไฟล์อย่างเดียว."""
    return (_APP_DIR.parents[1] / ".git").exists()


# ── Serving (อ่าน dataset ที่ build ไว้) ──────────────────────────────────────────

_dataset_cache: tuple[float, dict] | None = None


def _load() -> dict | None:
    global _dataset_cache
    if not _DATASET.exists():
        return None
    mtime = _DATASET.stat().st_mtime
    if _dataset_cache and _dataset_cache[0] == mtime:
        return _dataset_cache[1]
    try:
        data = json.loads(_DATASET.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    _dataset_cache = (mtime, data)
    return data


def _tier(ret: float, index_ret: float | None, band: float) -> str:
    if index_ret is None:
        return "unknown"
    diff = ret - index_ret
    if diff > band:
        return "above"
    if diff < -band:
        return "below"
    return "market"


def _base_meta(data: dict) -> dict:
    return {
        "as_of": data["as_of"],
        "as_of_label": dt.datetime.fromtimestamp(data["as_of"]).strftime("%d %b %Y %H:%M"),
        "benchmark": data.get("benchmark"),
        "return_basis": data.get("return_basis"),
        "source": data.get("source"),
        "can_build_live": can_build_live(),
    }


def meta() -> dict:
    """รายการปีที่มี + coverage ต่อปี (ไว้ให้ frontend สร้าง dropdown/สรุป)."""
    data = _load()
    if not data:
        return {"available": False}
    years = data["years"]
    summary = []
    for y in sorted(years, reverse=True):
        yd = years[y]
        summary.append({
            "year": int(y),
            "index_return": yd.get("index_return"),
            "members": yd.get("members"),
            "covered": yd.get("covered"),
            "coverage_pct": round(yd["covered"] / yd["members"] * 100, 1) if yd.get("members") else None,
        })
    return {
        "available": True,
        **_base_meta(data),
        "year_min": min(int(y) for y in years),
        "year_max": max(int(y) for y in years),
        "default_band_pct": DEFAULT_BAND_PCT,
        "years": summary,
    }


def year_ranking(year: int, band: float = DEFAULT_BAND_PCT) -> dict:
    """อันดับหุ้นปีเดียว (1..N) + tier ต่อแถว + จุดกราฟผลตอบแทนเรียงมาก→น้อย."""
    data = _load()
    if not data:
        return {"available": False}
    yd = data["years"].get(str(year))
    if not yd:
        return {"available": True, **_base_meta(data), "year": year, "found": False}
    names = data.get("names", {})
    index_ret = yd.get("index_return")
    rows = []
    counts = {"above": 0, "market": 0, "below": 0, "unknown": 0}
    for i, (sym, ret) in enumerate(yd["rows"]):
        tier = _tier(ret, index_ret, band)
        counts[tier] = counts.get(tier, 0) + 1
        rows.append({"rank": i + 1, "symbol": sym, "name": names.get(sym), "ret": ret, "tier": tier})
    return {
        "available": True,
        **_base_meta(data),
        "year": year,
        "found": True,
        "index_return": index_ret,
        "members": yd.get("members"),
        "covered": yd.get("covered"),
        "coverage_pct": round(yd["covered"] / yd["members"] * 100, 1) if yd.get("members") else None,
        "band_pct": band,
        "tier_counts": counts,
        "rows": rows,
        "curve": [r["ret"] for r in rows],  # ผลตอบแทนเรียงมาก→น้อย (กราฟ exponential-like)
    }


def longevity(top: int = 50, years_back: int = 20, band: float = DEFAULT_BAND_PCT) -> dict:
    """หุ้นที่ติด Top X อันดับแรก 'กี่ปี' ในช่วง Y ปีล่าสุด + สตรีคยาวสุด + จำนวนปีที่อยู่ในดัชนี."""
    data = _load()
    if not data:
        return {"available": False}
    all_years = sorted(int(y) for y in data["years"])
    window = all_years[-years_back:] if years_back and years_back < len(all_years) else all_years
    membership = _year_end_membership()
    names = data.get("names", {})

    # นับต่อหุ้น: ปีที่ติด Top X, ปีที่อยู่ในดัชนี, และเก็บ set ปีที่ติด Top X ไว้คิดสตรีค
    top_years: dict[str, list[int]] = {}
    idx_years: dict[str, int] = {}
    best_rank: dict[str, int] = {}
    for y in window:
        yd = data["years"].get(str(y))
        if not yd:
            continue
        for sym in membership.get(y, ()):  # นับ "อยู่ในดัชนี" จากสมาชิกจริง (รวมตัวที่ข้อมูลราคาไม่ครบ)
            idx_years[sym] = idx_years.get(sym, 0) + 1
        for i, (sym, _ret) in enumerate(yd["rows"][:top]):
            top_years.setdefault(sym, []).append(y)
            best_rank[sym] = min(best_rank.get(sym, 10**9), i + 1)

    def longest_streak(ys: list[int]) -> int:
        ys = sorted(set(ys))
        best = run = 0
        prev = None
        for y in ys:
            run = run + 1 if prev is not None and y == prev + 1 else 1
            best = max(best, run)
            prev = y
        return best

    ranking = []
    for sym, ys in top_years.items():
        ranking.append({
            "symbol": sym,
            "name": names.get(sym),
            "top_years": len(ys),
            "streak": longest_streak(ys),
            "years_in_index": idx_years.get(sym, len(ys)),
            "best_rank": best_rank.get(sym),
            "still_member": sym in membership.get(all_years[-1], set()),
        })
    ranking.sort(key=lambda r: (r["top_years"], r["streak"]), reverse=True)
    return {
        "available": True,
        **_base_meta(data),
        "top": top,
        "years_back": len(window),
        "window": [window[0], window[-1]] if window else None,
        "band_pct": band,
        "ranking": ranking,
    }


def stock_timeline(symbol: str, band: float = DEFAULT_BAND_PCT) -> dict:
    """อันดับ/ผลตอบแทน/tier ของหุ้นตัวเดียวในทุกปี (เห็นเส้นทางรุ่ง→ร่วง)."""
    data = _load()
    if not data:
        return {"available": False}
    sym = (symbol or "").strip().upper().replace(".", "-")
    membership = _year_end_membership()
    names = data.get("names", {})
    timeline = []
    for y in sorted(int(x) for x in data["years"]):
        yd = data["years"][str(y)]
        in_index = sym in membership.get(y, set())
        rank = ret = tier = None
        for i, (s, r) in enumerate(yd["rows"]):
            if s == sym:
                rank, ret = i + 1, r
                tier = _tier(r, yd.get("index_return"), band)
                break
        if in_index or rank is not None:
            timeline.append({
                "year": y, "in_index": in_index, "rank": rank, "ret": ret, "tier": tier,
                "of": yd.get("covered"), "index_return": yd.get("index_return"),
            })
    return {
        "available": True,
        **_base_meta(data),
        "symbol": sym,
        "name": names.get(sym),
        "found": bool(timeline),
        "band_pct": band,
        "timeline": timeline,
    }
