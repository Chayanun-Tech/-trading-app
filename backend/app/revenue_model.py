"""Revenue Growth Model — กราฟสไตล์ TrendSpider: ขั้นบันได YoY revenue growth รายไตรมาส
+ P/E ย้อนหลังรายสัปดาห์ ใต้กราฟราคา

ใช้ SEC EDGAR (รายได้/EPS รายไตรมาสจาก companyfacts XBRL) + ราคาย้อนหลังจาก provider ปัจจุบัน
(ปกติคือ Yahoo — resample เป็นรายสัปดาห์เอง). รองรับเฉพาะหุ้นสหรัฐที่ยื่น SEC.

หมายเหตุสำคัญ: หลายบริษัท (เช่น Apple) ยื่น 10-Q แค่ 3 ไตรมาส/ปี — ไตรมาสที่ 4 ไม่ได้ยื่นแยก
(รวมอยู่ใน 10-K ประจำปีเท่านั้น) ถ้านับ YoY แบบ "ถอยไป 4 รายการในลิสต์" จะเพี้ยนทันทีเพราะข้อมูล
มีแค่ 3 จุด/ปี ไม่ใช่ 4 — โมดูลนี้จึงสังเคราะห์ไตรมาสที่ขาด (FY 10-K − 3 ไตรมาสที่มี) และจับคู่ YoY
ด้วย fp/fy จริง (ไม่ใช่นับตำแหน่งในลิสต์) เพื่อกันคลาดเคลื่อน.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app import edgar
from app.financials import _entries, _days, _INCOME, _CASHFLOW, _EPS, _SHARES
from app.fundamentals import is_equity_symbol

_REVENUE_CONCEPTS = next(c[2] for c in _INCOME if c[0] == "revenue")
_NET_INCOME_CONCEPTS = next(c[2] for c in _INCOME if c[0] == "net_income")
_OCF_CONCEPTS = next(c[2] for c in _CASHFLOW if c[0] == "operating_cash_flow")
_CAPEX_CONCEPTS = next(c[2] for c in _CASHFLOW if c[0] == "capex")
_ALL_FP = {"Q1", "Q2", "Q3", "Q4"}

# จำนวนแท่งราคาโดยประมาณต่อ 1 ไตรมาส แยกตามความละเอียด — ใช้ตัดช่วงราคา/P/E ให้พอดีกับไตรมาสที่แสดง
_BARS_PER_QUARTER = {"daily": 63, "weekly": 13, "monthly": 3}


def _quarterly_with_fp(facts: dict, concepts: list[str], unit: str, prefer_latest_value: bool = False) -> dict[str, dict]:
    """เหมือน financials._quarterly_duration แต่เก็บ fp/fy (ไตรมาสบัญชีจริงที่บริษัทยื่น) ไว้ด้วย
    บริษัทที่ปีงบไม่ตรงปฏิทิน (เช่น Microsoft: Q3 FY = ม.ค.-มี.ค.) ต้องใช้ fp/fy ไม่ใช่เดือนปฏิทิน
    ไม่งั้น label ไตรมาสจะไม่ตรงกับที่บริษัท/นักวิเคราะห์อื่นเรียก.

    prefer_latest_value=True: ใช้ค่า 'ยื่นล่าสุด' แทนค่าที่ยื่นครั้งแรก (fp/fy ยังคงยึดตามครั้งแรกเสมอ
    เพื่อ label ไม่เพี้ยน) — จำเป็นสำหรับ EPS เพราะมาตรฐานบัญชีบังคับให้ 'ปรับปรุงย้อนหลัง' EPS ทุกไตรมาส
    ที่เคยรายงานเมื่อบริษัท split หุ้น (เช่น GOOGL split 20:1 ก.ค. 2022 → EPS ไตรมาสเก่าที่โผล่มาเป็นตัวเลข
    เทียบเคียงในงบปีถัดไปจะถูกหารด้วย 20 ให้แล้ว) ถ้าใช้ค่าที่ยื่นครั้งแรกจะเป็นเลขก่อน split ผิดเพี้ยนไปหลายเท่า
    เมื่อเทียบกับราคาย้อนหลังที่ Yahoo ปรับ split ให้แล้วเสมอ."""
    out: dict[str, dict] = {}
    for e in _entries(facts, concepts, unit):
        if "start" not in e or not str(e.get("form", "")).startswith("10-Q"):
            continue
        d = _days(e["start"], e["end"])
        if d is None or d < 80 or d > 100:
            continue
        if e["end"] not in out:
            out[e["end"]] = {"val": e["val"], "fp": e.get("fp"), "fy": e.get("fy")}
        elif prefer_latest_value:
            out[e["end"]]["val"] = e["val"]
    return out


def _annual_with_end(facts: dict, concepts: list[str], unit: str, prefer_latest_value: bool = False) -> dict[int, dict]:
    """งบทั้งปี (10-K, fp=FY) พร้อมวันที่ปิดงบ — ใช้สังเคราะห์ไตรมาสที่ไม่ได้ยื่นแยก (มักเป็น Q4).
    prefer_latest_value: เหมือน _quarterly_with_fp — ใช้ EPS ที่ปรับปรุงหลัง split แล้ว."""
    out: dict[int, dict] = {}
    for e in _entries(facts, concepts, unit):
        if "start" not in e or not str(e.get("form", "")).startswith("10-K") or e.get("fp") != "FY":
            continue
        d = _days(e["start"], e["end"])
        if d is None or d < 300 or d > 400:
            continue
        fy = e.get("fy")
        if fy is None:
            continue
        if fy not in out or e["end"] > out[fy]["end"]:
            out[fy] = {"end": e["end"], "val": e["val"]}
        elif prefer_latest_value and e["end"] == out[fy]["end"]:
            out[fy]["val"] = e["val"]
    return out


def _synthesize_missing_quarter(quarterly: dict[str, dict], annual: dict[int, dict]) -> dict[str, dict]:
    """เติมไตรมาสที่ SEC ไม่แยกยื่นเป็น 10-Q (ปกติคือ Q4) = งบทั้งปี (10-K) − ผลรวม 3 ไตรมาสที่มี.
    ทำเฉพาะปีที่ขาดพอดี 1 ไตรมาส (กันเดาผิดตอนข้อมูลไม่ครบ/ไม่ตรงกัน)."""
    by_fy: dict[int, dict[str, float]] = {}
    for m in quarterly.values():
        fp, fy = m.get("fp"), m.get("fy")
        if fp in _ALL_FP and fy is not None:
            by_fy.setdefault(fy, {})[fp] = m["val"]

    out = dict(quarterly)
    for fy, ann in annual.items():
        have = by_fy.get(fy, {})
        missing = _ALL_FP - have.keys()
        if len(missing) == 1 and len(have) == 3 and ann["end"] not in out:
            miss_fp = next(iter(missing))
            out[ann["end"]] = {"val": ann["val"] - sum(have.values()), "fp": miss_fp, "fy": fy}
    return out


def _fp_key(meta: dict, end: str) -> tuple[str, int] | None:
    """คีย์ระบุไตรมาส-ปีบัญชีจริง ('Q3', 2025) ใช้จับคู่ YoY แทนการนับตำแหน่งในลิสต์
    (นับตำแหน่งจะเพี้ยนถ้าไตรมาสไหนขาดหาย — เช่นบริษัทที่มีแค่ 3 ไตรมาส/ปี)."""
    fp, fy = meta.get("fp"), meta.get("fy")
    if fp and fy is not None:
        try:
            return (str(fp), int(fy))
        except (TypeError, ValueError):
            pass
    try:
        y, m, _d = map(int, end.split("-"))
        return (f"Q{(m - 1) // 3 + 1}", y)
    except Exception:  # noqa: BLE001
        return None


def _yoy_lookup(meta: dict[str, dict]) -> dict[tuple[str, int], float]:
    """สร้างตาราง (fp, fy) -> ค่า สำหรับจับคู่ YoY ของเมตริกใดก็ได้ (รายได้/กำไร)."""
    lookup: dict[tuple[str, int], float] = {}
    for d, m in meta.items():
        k = _fp_key(m, d)
        if k:
            lookup[k] = m["val"]
    return lookup


def _quarter_label(end: str, meta: dict | None) -> str:
    """ใช้ fp/fy จาก SEC ('Q3', 2025) -> \"Q3'25\" ถ้ามี ไม่งั้น fallback เป็นไตรมาสปฏิทิน."""
    fp, fy = (meta or {}).get("fp"), (meta or {}).get("fy")
    if fp and fy is not None:
        try:
            return f"{fp}'{int(fy) % 100:02d}"
        except (TypeError, ValueError):
            pass
    try:
        y, m, _d = map(int, end.split("-"))
        q = (m - 1) // 3 + 1
        return f"Q{q}'{y % 100:02d}"
    except Exception:  # noqa: BLE001
        return end


def _group_candles(candles: list, key_fn) -> list[dict]:
    groups: dict = {}
    for c in candles:
        groups.setdefault(key_fn(c), []).append(c)
    out = []
    for key in sorted(groups.keys()):
        grp = groups[key]
        out.append({
            "time": grp[0].time,
            "open": grp[0].open,
            "high": max(x.high for x in grp),
            "low": min(x.low for x in grp),
            "close": grp[-1].close,
        })
    return out


def _fetch_yahoo_quarterly_sync(symbol: str) -> dict[str, dict]:
    """ดึงงบไตรมาสล่าสุดจาก Yahoo (yfinance) แบบ sync — เร็วกว่า SEC มาก เพราะ Yahoo มักอัปเดตราย
    ไตรมาสภายใน 1-2 วันหลังบริษัทประกาศผล (ต่างจาก SEC ที่ต้องรอยื่นเอกสาร 10-Q ทางการ ~1-3 สัปดาห์)
    แต่เป็นตัวเลข 'เบื้องต้น' ที่บริษัทประกาศเอง ยังไม่ผ่านการยื่นอย่างเป็นทางการ — ใช้เสริมเฉพาะไตรมาส
    ล่าสุดที่ SEC ยังไม่มี ไม่ใช้แทนข้อมูล SEC ที่เหลือทั้งหมด. ไม่ cache — ต้องเป็นข้อมูลสดที่สุดเสมอ."""
    import yfinance as yf
    t = yf.Ticker(symbol)
    df = t.quarterly_income_stmt
    if df is None or df.empty:
        df = t.quarterly_financials
    if df is None or df.empty:
        return {}

    def _row(names):
        for n in names:
            if n in df.index:
                return df.loc[n]
        return None

    rev_row = _row(["Total Revenue", "TotalRevenue", "Operating Revenue"])
    ni_row = _row(["Net Income", "Net Income Common Stockholders", "NetIncome"])
    if rev_row is None:
        return {}

    out: dict[str, dict] = {}
    for col in df.columns:
        try:
            end = col.date().isoformat() if hasattr(col, "date") else str(col)[:10]
        except Exception:  # noqa: BLE001
            continue
        rev_val = rev_row.get(col)
        if rev_val is None or rev_val != rev_val:  # NaN
            continue
        ni_val = ni_row.get(col) if ni_row is not None else None
        out[end] = {
            "revenue": float(rev_val),
            "net_income": float(ni_val) if ni_val is not None and ni_val == ni_val else None,
        }
    return out


async def _preliminary_quarter(symbol: str, quarters_full: list[dict]) -> dict | None:
    """หาไตรมาสล่าสุดจาก Yahoo ที่ 'ใหม่กว่า' ไตรมาสล่าสุดที่มีใน SEC แล้ว — ถ้ามี คืนเป็นไตรมาส
    'เบื้องต้น' (preliminary) เพิ่มเข้ากราฟ ต้องคำนวณ YoY ได้ด้วย (เทียบกับไตรมาสเดียวกันปีก่อนจาก SEC
    ก่อน แม่นกว่า ถ้าไม่มีค่อย fallback ไปหาในข้อมูล Yahoo เอง) ไม่งั้นโชว์ไปก็เทียบไม่ได้ ไม่มีประโยชน์."""
    try:
        yahoo_q = await asyncio.to_thread(_fetch_yahoo_quarterly_sync, symbol)
    except Exception:  # noqa: BLE001 — yfinance ล่ม/rate-limit ก็ไม่ทำให้ endpoint หลักพังไปด้วย
        return None
    if not yahoo_q:
        return None

    latest_sec = quarters_full[-1]["period"] if quarters_full else ""
    newest_end = max(yahoo_q.keys())
    if newest_end <= latest_sec:
        return None  # SEC มีข้อมูลทันแล้ว ไม่ต้องเสริม

    y = yahoo_q[newest_end]
    target_dt = datetime.strptime(newest_end, "%Y-%m-%d")
    try:
        prior_year_target = target_dt.replace(year=target_dt.year - 1)
    except ValueError:  # 29 ก.พ.
        prior_year_target = target_dt.replace(year=target_dt.year - 1, day=28)

    prev_rev = prev_ni = None
    best_diff = None
    for qf in quarters_full:
        try:
            qd = datetime.strptime(qf["period"], "%Y-%m-%d")
        except ValueError:
            continue
        diff = abs((qd - prior_year_target).days)
        if diff <= 20 and (best_diff is None or diff < best_diff):
            best_diff, prev_rev, prev_ni = diff, qf["revenue"], qf.get("net_income")
    if prev_rev is None:
        for end, vals in yahoo_q.items():
            if end == newest_end:
                continue
            try:
                ed = datetime.strptime(end, "%Y-%m-%d")
            except ValueError:
                continue
            if abs((ed - prior_year_target).days) <= 20:
                prev_rev, prev_ni = vals["revenue"], vals.get("net_income")
                break
    if not prev_rev:
        return None

    yoy = (y["revenue"] - prev_rev) / abs(prev_rev)
    profit_yoy = None
    if y.get("net_income") is not None and prev_ni:
        profit_yoy = (y["net_income"] - prev_ni) / abs(prev_ni)

    return {
        "period": newest_end,
        "label": _quarter_label(newest_end, None) + "*",
        "revenue": y["revenue"], "yoy_pct": yoy,
        "net_income": y.get("net_income"), "profit_yoy_pct": profit_yoy,
        "preliminary": True,
    }


def _resample(candles: list, granularity: str) -> list[dict]:
    """รวมแท่งรายวันเป็นความละเอียดที่ต้องการ: daily (แยกแท่งเดิม) / weekly (สัปดาห์ปฏิทิน ISO) / monthly (เดือนปฏิทิน)."""
    if granularity == "daily":
        return [{"time": c.time, "open": c.open, "high": c.high, "low": c.low, "close": c.close} for c in candles]
    if granularity == "monthly":
        return _group_candles(candles, lambda c: (datetime.fromtimestamp(c.time, tz=timezone.utc).year,
                                                    datetime.fromtimestamp(c.time, tz=timezone.utc).month))
    return _group_candles(candles, lambda c: datetime.fromtimestamp(c.time, tz=timezone.utc).isocalendar()[:2])


async def get_revenue_model(symbol: str, refresh: bool = False, max_quarters: int = 40,
                            granularity: str = "weekly") -> dict:
    symbol = (symbol or "").strip().upper()
    if not symbol:
        raise ValueError("กรุณาระบุสัญลักษณ์หุ้น")
    if not is_equity_symbol(symbol):
        raise ValueError("ใช้ได้กับหุ้นรายตัวเท่านั้น (ไม่รองรับคริปโต/forex/ดัชนี)")
    if granularity not in _BARS_PER_QUARTER:
        raise ValueError("granularity ต้องเป็น daily, weekly หรือ monthly")

    try:
        facts = await edgar.get_company_facts(symbol, force_refresh=refresh)
    except ValueError:
        raise ValueError("โมเดลนี้ใช้งบรายไตรมาสจาก SEC EDGAR — รองรับเฉพาะหุ้นสหรัฐที่ยื่น SEC เท่านั้น "
                          "(หุ้นไทย/ต่างประเทศยังไม่รองรับ)")

    raw_revenue_meta = _synthesize_missing_quarter(
        _quarterly_with_fp(facts, _REVENUE_CONCEPTS, "USD"),
        _annual_with_end(facts, _REVENUE_CONCEPTS, "USD"),
    )
    raw_eps_meta = _synthesize_missing_quarter(
        _quarterly_with_fp(facts, _EPS, "USD/shares", prefer_latest_value=True),
        _annual_with_end(facts, _EPS, "USD/shares", prefer_latest_value=True),
    )
    raw_eps = {d: m["val"] for d, m in raw_eps_meta.items()}
    raw_ni_meta = _synthesize_missing_quarter(
        _quarterly_with_fp(facts, _NET_INCOME_CONCEPTS, "USD"),
        _annual_with_end(facts, _NET_INCOME_CONCEPTS, "USD"),
    )
    raw_ocf_meta = _synthesize_missing_quarter(
        _quarterly_with_fp(facts, _OCF_CONCEPTS, "USD"),
        _annual_with_end(facts, _OCF_CONCEPTS, "USD"),
    )

    # กระแสเงินสดอิสระ (Free Cash Flow = OCF - CapEx) แบบรายปี (10-K) เท่านั้น — ไม่ใช้รายไตรมาสแบบ
    # รายได้/กำไร เพราะหลายบริษัทยื่น XBRL ของกระแสเงินสดแบบ "สะสมตั้งแต่ต้นปีงบ" (YTD) ในไตรมาส 2-3
    # ไม่ใช่ยอดเฉพาะไตรมาสนั้น ทำให้ดึงเป็นรายไตรมาสตรง ๆ ไม่ได้แม่นยำ ส่วนรายปีมีครบทุกบริษัทเสมอ (10-K)
    ann_ocf = _annual_with_end(facts, _OCF_CONCEPTS, "USD")
    ann_capex = _annual_with_end(facts, _CAPEX_CONCEPTS, "USD")
    ann_shares = _annual_with_end(facts, _SHARES, "shares")
    fcf_annual = []
    for fy in sorted(set(ann_ocf) & set(ann_capex)):
        fcf_val = ann_ocf[fy]["val"] - ann_capex[fy]["val"]
        shares_val = ann_shares.get(fy, {}).get("val")
        fcf_annual.append({
            "period": ann_ocf[fy]["end"], "fy": fy, "fcf": fcf_val,
            "fcf_per_share": (fcf_val / shares_val) if shares_val else None,
        })

    dates = sorted(raw_revenue_meta.keys())
    if len(dates) < 5:
        raise ValueError("ข้อมูลรายได้รายไตรมาสจาก SEC ไม่พอสำหรับคำนวณ YoY (ต้องการอย่างน้อย 5 ไตรมาส)")

    # จับคู่ YoY ด้วยไตรมาสบัญชีจริง (fp, fy-1) ไม่ใช่นับถอยหลัง 4 ตำแหน่งในลิสต์
    rev_by_key = _yoy_lookup(raw_revenue_meta)
    ni_by_key = _yoy_lookup(raw_ni_meta)
    ocf_by_key = _yoy_lookup(raw_ocf_meta)

    quarters_full = []
    for d in dates:
        meta = raw_revenue_meta[d]
        rev = meta["val"]
        k = _fp_key(meta, d)
        yoy = None
        profit_yoy = None
        net_income = None
        ocf = None
        ocf_yoy = None
        if k:
            prev = rev_by_key.get((k[0], k[1] - 1))
            if prev:
                yoy = (rev - prev) / abs(prev)
            net_income = ni_by_key.get(k)
            ni_prev = ni_by_key.get((k[0], k[1] - 1))
            if net_income is not None and ni_prev:
                profit_yoy = (net_income - ni_prev) / abs(ni_prev)
            ocf = ocf_by_key.get(k)
            ocf_prev = ocf_by_key.get((k[0], k[1] - 1))
            if ocf is not None and ocf_prev:
                ocf_yoy = (ocf - ocf_prev) / abs(ocf_prev)
        quarters_full.append({"period": d, "label": _quarter_label(d, meta), "revenue": rev, "yoy_pct": yoy,
                              "net_income": net_income, "profit_yoy_pct": profit_yoy,
                              "ocf": ocf, "ocf_yoy_pct": ocf_yoy})

    quarters = [q for q in quarters_full if q["yoy_pct"] is not None][-max_quarters:]
    if not quarters:
        raise ValueError("ยังไม่มีไตรมาสที่คำนวณ YoY ได้ครบ (ต้องมีงบย้อนหลังอย่างน้อย 5 ไตรมาส)")

    # เสริมไตรมาสล่าสุดจาก Yahoo ถ้า SEC ยังไม่มี (บริษัทประกาศผลแล้วแต่ยังไม่ยื่น 10-Q ทางการ) —
    # ดึงสดทุกครั้ง ไม่ cache เพื่อให้ทันข่าวที่สุด ไม่พลาดโอกาสระหว่างรอ SEC
    prelim = await _preliminary_quarter(symbol, quarters_full)
    if prelim:
        quarters = (quarters + [prelim])[-max_quarters:]

    # TTM EPS (ผลรวม EPS 4 ไตรมาสล่าสุด ตามลำดับเวลาจริง — สังเคราะห์ไตรมาสขาดแล้วจึงครบ 4/ปี)
    eps_dates = sorted(raw_eps.keys())
    ttm_eps_by_date = [
        (eps_dates[i], sum(raw_eps[d] for d in eps_dates[i - 3:i + 1]))
        for i in range(3, len(eps_dates))
    ]

    from app.main import provider  # lazy import กันวน circular import (ตามแบบ trend_radar.py)
    get_history = getattr(provider, "get_history", None)
    try:
        candles = await get_history(symbol, "1d", max_bars=8000) if get_history \
            else await provider.get_candles(symbol, "1d", 1000)
    except Exception:  # noqa: BLE001 — ราคาย้อนหลังดึงไม่ได้ก็ยังโชว์กล่อง YoY ได้ (ไม่มีกราฟราคา/P/E)
        candles = []
    bars = _resample(candles, granularity) if candles else []

    pe_series = []
    for w in bars:
        w_date = datetime.fromtimestamp(w["time"], tz=timezone.utc).date().isoformat()
        ttm = next((val for qd, val in reversed(ttm_eps_by_date) if qd <= w_date), None)
        if ttm and ttm > 0:
            pe_series.append({"time": w["time"], "pe": round(w["close"] / ttm, 2)})

    # อ้างอิงจากช่วง ~2 ปีล่าสุดเท่านั้น (ไม่ใช่ทั้ง 6 ปี) — EPS ย้อนหลังของหุ้นที่เคย split นาน ๆ มาแล้ว
    # (เช่น GOOGL 2022, NVDA 2021/2024) อาจไม่ถูกปรับปรุงย้อนหลังครบทุกไตรมาสตามข้อมูลที่ SEC ให้มา
    # ช่วงใกล้ปัจจุบันเชื่อถือได้กว่ามาก จึงใช้คำนวณเส้นอ้างอิงแทนค่ามัธยฐานทั้งช่วง
    bars_per_q = _BARS_PER_QUARTER[granularity]
    pe_reference = None
    recent_pe = pe_series[-(bars_per_q * 8):]  # ~2 ปีล่าสุด
    if recent_pe:
        pe_sorted = sorted(p["pe"] for p in recent_pe)
        median = pe_sorted[len(pe_sorted) // 2]
        pe_reference = {"median": round(median, 1), "label": f"{round(median)}x P/E (median, ~2 ปีล่าสุด)"}

    # P/FCF (ราคา ÷ Free Cash Flow ต่อหุ้น) — ใช้ FCF ต่อหุ้นของปีงบล่าสุดที่ประกาศ "ค้างไว้" จนกว่าจะมี
    # 10-K ปีถัดไป (อัปเดตปีละครั้งแทนรายไตรมาส เพราะข้อมูลกระแสเงินสดรายไตรมาสไม่ครบตามที่อธิบายด้านบน)
    pfcf_series = []
    for w in bars:
        w_date = datetime.fromtimestamp(w["time"], tz=timezone.utc).date().isoformat()
        entry = next((e for e in reversed(fcf_annual) if e["period"] <= w_date and e["fcf_per_share"]), None)
        if entry and entry["fcf_per_share"] > 0:
            pfcf_series.append({"time": w["time"], "pfcf": round(w["close"] / entry["fcf_per_share"], 2)})

    pfcf_reference = None
    recent_pfcf = pfcf_series[-(bars_per_q * 8):]  # ~2 ปีล่าสุด
    if recent_pfcf:
        pfcf_sorted = sorted(p["pfcf"] for p in recent_pfcf)
        median_pfcf = pfcf_sorted[len(pfcf_sorted) // 2]
        pfcf_reference = {"median": round(median_pfcf, 1), "label": f"{round(median_pfcf)}x P/FCF (median, ~2 ปีล่าสุด)"}

    window = max(len(quarters) * bars_per_q, 60)
    return {
        "symbol": symbol,
        "entity_name": facts.get("entityName"),
        "granularity": granularity,
        "quarters": quarters,
        "price_candles": bars[-window:],
        "pe_series": pe_series[-window:],
        "pe_reference": pe_reference,
        "fcf_annual": fcf_annual,
        "pfcf_series": pfcf_series[-window:],
        "pfcf_reference": pfcf_reference,
        "has_preliminary": bool(prelim),
        "disclaimer": "ข้อมูลจาก SEC EDGAR (งบที่ยื่นจริง) + ราคาย้อนหลังจาก provider ปัจจุบัน — "
                      "P/E ช่วงเก่า (หลายปีก่อน) ของหุ้นที่เคย split อาจคลาดเคลื่อนถ้า SEC ไม่มีข้อมูลปรับปรุงย้อนหลังครบ — "
                      + ("ไตรมาสล่าสุด (มี * ต่อท้าย) เป็นตัวเลขเบื้องต้นจาก Yahoo ที่บริษัทประกาศเอง "
                         "ยังไม่ผ่านการยื่น 10-Q อย่างเป็นทางการกับ SEC อาจมีการปรับแก้ภายหลัง — "
                         if prelim else "")
                      + "เพื่อการศึกษาเท่านั้น ไม่ใช่คำแนะนำการลงทุน",
    }
