"""แถบ '📜 ประวัติศาสตร์ราคา' — ไล่กราฟราคาย้อนหลังของหุ้นรายตัว หา 'ช่วงที่ราคาขยับแรง'
(swing) จากราคาจริง แล้วให้ AI เติม 'เหตุการณ์จริงในโลก' ที่เป็นตัวหนุน/กดราคาในช่วงนั้น
ร้อยเป็นไทม์ไลน์ตามแกนเวลา.

แนวคิด (แบบ Ray Dalio — history rhymes, doesn't repeat):
- 'จุดที่ราคาขยับ' คำนวณจากราคาจริง 100% (แม่น) — ตรวจจับด้วย zigzag ตาม % การกลับตัว
- 'สาเหตุของการขยับ' เป็นการวิเคราะห์ของ AI จากความรู้เรื่องประวัติหุ้นตัวนั้น (อาจคลาดเคลื่อน)
  จึงติดป้ายระดับความมั่นใจ (confidence) ทุกเหตุการณ์ และย้ำว่า correlation ≠ causation

ที่มาข้อมูล:
- ราคาย้อนหลัง: provider (yahoo) ดึงรายวันลึกสุดแล้ว resample เป็นรายเดือนลดสัญญาณรบกวน
- เหตุการณ์: LLM (ยึดความรู้ทั่วไปเรื่องประวัติบริษัท) — ดีมากกับหุ้นใหญ่ US บางกับหุ้นเล็ก/ไทย

cache ผลลัพธ์ลงดิสก์ (TTL ~7 วัน) เพราะประวัติศาสตร์แทบไม่เปลี่ยน (มีแค่ช่วงล่าสุดที่ขยับได้).
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from app import llm, thai_sec
from app.config import get_settings

_CACHE_DIR = Path(__file__).resolve().parents[1].parent / "data" / "history"
_TTL = 7 * 24 * 3600

# swing detection: ลอง reversal % จากหลวมไปแน่น ให้ได้จำนวน swing ที่พอเหมาะ (อ่านไหว + ครบ)
_TARGET_MAX_SWINGS = 20     # เพดานที่ยังอ่านไหว — เลือก threshold ละเอียดสุดที่ไม่เกินนี้
_HARD_CAP = 26             # เพดานแข็งสุดที่ส่งให้ LLM/แสดงผล (กัน prompt บวม)


SYSTEM_PROMPT = """คุณคือนักประวัติศาสตร์ตลาดทุน หน้าที่: อธิบาย 'เหตุการณ์จริงในโลก' ที่ทำให้
ราคาหุ้นตัวหนึ่งขยับขึ้น/ลงแรงในแต่ละช่วงเวลา โดยดูจาก 'ช่วงที่ราคาขยับ' ที่คำนวณจากกราฟจริง
มาให้แล้ว แล้วเติมว่าช่วงนั้นเกิดอะไรขึ้นกับบริษัทนี้/อุตสาหกรรมนี้/เศรษฐกิจโลก

หลักการ:
- ยึด 'ความจริงทางประวัติศาสตร์' เท่านั้น — เหตุการณ์ที่เกิดจริงและเป็นที่รู้กัน (งบเซอร์ไพรส์,
  เปิดตัวสินค้า, ดีล M&A, คดี/ปรับ, วิกฤตเศรษฐกิจ, สงคราม, โควิด, การขึ้นดอกเบี้ย ฯลฯ)
- ถ้าไม่มั่นใจว่าช่วงนั้นเกิดจากอะไร ให้ใส่ confidence เป็น "low" และบอกตามตรงว่าเป็นการอนุมาน
  จากบริบทตลาด/อุตสาหกรรม อย่าแต่งเหตุการณ์หรือวันที่ที่ไม่มีจริงขึ้นมา
- อธิบาย 'กลไก' (mechanism) ว่าทำไมเหตุการณ์นั้นทำให้ราคาขึ้น/ลง ไม่ใช่แค่เล่าข่าว
- เป็นกลาง ไม่เชียร์ซื้อขาย ไม่ทำนายอนาคต (อธิบายอดีตเท่านั้น)

กฎภาษา: เขียนเป็นภาษาไทยที่อ่านรู้เรื่อง เก็บชื่อเฉพาะ/ตัวย่อ (เช่น NVDA, AI, Fed, COVID) ไว้ตามเดิม

ข้อบังคับ: ตอบเป็น JSON เท่านั้น ห้ามมีข้อความนอก JSON"""

_OUTPUT_CONTRACT = """รูปแบบ JSON ที่ต้องคืน (เท่านั้น):
{
  "overview": "<ภาพรวมประวัติศาสตร์ราคาหุ้นนี้ 2-4 ประโยค: เส้นทางใหญ่ ๆ จากอดีตถึงปัจจุบัน>",
  "events": [
    {
      "id": <เลข id ของช่วง swing ที่ให้มา ต้องตรงกัน>,
      "title": "<พาดหัวสั้น ๆ ของเหตุการณ์หลักในช่วงนั้น>",
      "catalyst": "<เหตุการณ์จริงที่เกิดขึ้นช่วงนั้นคืออะไร 1-3 ประโยค — อ้างเหตุการณ์/ตัวเลข/วันที่ที่รู้จริง>",
      "mechanism": "<กลไก: ทำไมเหตุการณ์นี้ทำให้ราคาขยับทิศนั้น 1-2 ประโยค>",
      "category": "<tailwind | headwind> (หนุนราคา / กดราคา — ให้ตรงกับทิศที่ราคาขยับจริง)",
      "scope": "<company | industry | macro> (ต้นเหตุมาจากตัวบริษัทเอง / ทั้งอุตสาหกรรม / เศรษฐกิจมหภาค)",
      "confidence": "<high | medium | low> (มั่นใจแค่ไหนว่านี่คือสาเหตุจริงของช่วงนั้น)"
    }
  ]
}
ต้องมี event ให้ครบทุก id ที่ให้มา เรียงตาม id จากน้อยไปมาก ถ้าช่วงไหนไม่รู้สาเหตุจริง ให้ confidence=low
และอธิบายตามบริบทเท่าที่ทราบ ห้ามข้าม id"""


# ---------------------------------------------------------------- price utils --
def _resample_monthly(candles: list) -> list[dict]:
    """รวมแท่งรายวันเป็นราคาปิดสิ้นเดือน (แท่งสุดท้ายของแต่ละเดือนชนะ) เพื่อลดสัญญาณรบกวน."""
    by_month: dict[tuple[int, int], object] = {}
    for c in candles:  # candles เรียงเก่า→ใหม่อยู่แล้ว
        dt = datetime.fromtimestamp(c.time, tz=timezone.utc)
        by_month[(dt.year, dt.month)] = c
    series = []
    for (y, m), c in sorted(by_month.items()):
        if c.close and c.close > 0:
            dt = datetime.fromtimestamp(c.time, tz=timezone.utc)
            # floor เป็นเที่ยงคืน UTC ของวันนั้น เพื่อให้กราฟ (lightweight-charts) แสดงแกนเป็น 'วันที่' ไม่ใช่เวลา
            day_mid = int(datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc).timestamp())
            series.append({"time": day_mid, "date": f"{y:04d}-{m:02d}", "close": float(c.close)})
    return series


def _detect_swings(series: list[dict], reversal: float) -> list[dict]:
    """ตรวจจับ swing แบบ zigzag: บันทึกจุดกลับตัวเมื่อราคาย้อนจาก 'จุดสุดขั้ว' เกิน reversal (สัดส่วน).

    คืน list ของช่วง (pivot→pivot ถัดไป) พร้อม % การเปลี่ยนแปลงจากราคาจริง."""
    n = len(series)
    if n < 3:
        return []
    pivots = [0]
    trend = 0            # 0=ยังไม่รู้ทิศ, 1=ขาขึ้น, -1=ขาลง
    ext_i = 0            # index ของจุดสุดขั้วปัจจุบัน
    for i in range(1, n):
        p = series[i]["close"]
        e = series[ext_i]["close"]
        if e <= 0:
            ext_i = i
            continue
        if trend >= 0:                       # กำลังมองหา/ติดตามจุดสูง
            if p > e:
                ext_i = i
            elif (e - p) / e >= reversal:     # ย้อนลงเกินเกณฑ์ → จุดสูงคือ pivot
                if ext_i != pivots[-1]:
                    pivots.append(ext_i)
                trend = -1
                ext_i = i
                continue
        if trend <= 0:                       # กำลังมองหา/ติดตามจุดต่ำ
            if p < e:
                ext_i = i
            elif (p - e) / e >= reversal:     # ย้อนขึ้นเกินเกณฑ์ → จุดต่ำคือ pivot
                if ext_i != pivots[-1]:
                    pivots.append(ext_i)
                trend = 1
                ext_i = i
                continue
    if ext_i != pivots[-1]:
        pivots.append(ext_i)
    if pivots[-1] != n - 1:
        pivots.append(n - 1)                 # ปิดท้ายด้วยจุดล่าสุดเสมอ

    piv = sorted(set(pivots))
    segs = []
    for a, b in zip(piv, piv[1:]):
        pa, pb = series[a]["close"], series[b]["close"]
        if pa <= 0:
            continue
        chg = (pb - pa) / pa * 100
        segs.append({
            "start_date": series[a]["date"], "end_date": series[b]["date"],
            "start_price": round(pa, 2), "end_price": round(pb, 2),
            "change_pct": round(chg, 1),
            "direction": "up" if chg >= 0 else "down",
            "start_time": series[a]["time"], "end_time": series[b]["time"],
        })
    return segs


def _best_swings(series: list[dict]) -> list[dict]:
    """เลือกชุด swing ที่ 'ละเอียดที่สุดเท่าที่ยังอ่านไหว' — ไล่ threshold จากหลวม (จับได้เยอะ)
    ไปแน่น (จับได้น้อย) แล้วหยิบชุดแรกที่จำนวนไม่เกินเพดาน จึงได้ไทม์ไลน์ที่ครบสุดโดยไม่รก."""
    thresholds = (0.15, 0.18, 0.22, 0.28, 0.35, 0.45, 0.60)
    segs: list[dict] = []
    for thr in thresholds:
        segs = _detect_swings(series, thr)
        if len(segs) <= _TARGET_MAX_SWINGS:
            break
    # ตัดช่วงจิ๋ว (<8%) ที่มักเป็น 'หาง' ต่อจากจุดกลับตัวล่าสุดถึงเดือนปัจจุบัน — ไม่ใช่เหตุการณ์จริง
    trimmed = [s for s in segs if abs(s["change_pct"]) >= 8]
    if trimmed:
        segs = trimmed
    # ถ้าแม้ threshold แน่นสุดก็ยังเยอะเกินเพดาน เก็บช่วงที่ขยับแรงสุด (|%|) แล้วเรียงกลับตามเวลา
    if len(segs) > _HARD_CAP:
        top = sorted(segs, key=lambda s: abs(s["change_pct"]), reverse=True)[:_HARD_CAP]
        segs = sorted(top, key=lambda s: s["start_time"])
    for i, s in enumerate(segs):
        s["id"] = i
    return segs


# ---------------------------------------------------------------- json utils --
def _extract_json(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1] if "```" in t[3:] else t
        t = t.lstrip("json").strip("` \n")
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end != -1:
        return json.loads(t[start:end + 1])
    raise ValueError("ไม่พบ JSON ในคำตอบของโมเดล")


def _s(v, n: int):
    return str(v)[:n] if v is not None else None


def _company_name(symbol: str) -> tuple[str | None, str | None]:
    """(ชื่อบริษัท, sector) แบบ offline ก่อน (รายชื่อ S&P 500) — ไม่ยิงเน็ต. คืน (None, None) ถ้าไม่พบ."""
    try:
        from app.industry_peers import load_constituents, _to_sym
        meta = load_constituents().get(_to_sym(symbol))
        if meta:
            return meta.get("name") or None, meta.get("sector") or None
    except Exception:  # noqa: BLE001
        pass
    return None, None


def _cache_path(symbol: str) -> Path:
    safe = "".join(c if c.isalnum() else "_" for c in symbol.upper().strip())
    return _CACHE_DIR / f"hist_{safe}.json"


# ---------------------------------------------------------------- main entry --
async def _fetch_candles(symbol: str) -> list:
    from app.main import provider  # lazy import กันวน circular import
    get_history = getattr(provider, "get_history", None)
    if get_history:
        return await get_history(symbol, "1d", max_bars=8000) or []
    return await provider.get_candles(symbol, "1d", 8000) or []


def _norm_category(v, direction: str) -> str:
    s = str(v or "").lower()
    if "tail" in s or "หนุน" in s or "ส่ง" in s:
        return "tailwind"
    if "head" in s or "กด" in s or "ต้าน" in s:
        return "headwind"
    return "tailwind" if direction == "up" else "headwind"


def _norm_scope(v) -> str:
    s = str(v or "").lower()
    if "macro" in s or "มหภาค" in s:
        return "macro"
    if "indus" in s or "อุตสาห" in s:
        return "industry"
    return "company"


def _norm_conf(v) -> str:
    s = str(v or "").lower()
    if "high" in s or "สูง" in s:
        return "high"
    if "low" in s or "ต่ำ" in s:
        return "low"
    return "medium"


def _merge_events(swings: list[dict], payload: dict) -> list[dict]:
    """รวม 'ราคาจริงต่อ swing' (แม่น) เข้ากับ 'narrative จาก LLM' โดยจับคู่ด้วย id."""
    by_id: dict[int, dict] = {}
    for ev in (payload.get("events") or []):
        if not isinstance(ev, dict):
            continue
        try:
            eid = int(ev.get("id"))
        except (TypeError, ValueError):
            continue
        by_id[eid] = ev
    out = []
    for s in swings:
        ev = by_id.get(s["id"], {})
        out.append({
            **s,
            "title": _s(ev.get("title"), 160),
            "catalyst": _s(ev.get("catalyst"), 600),
            "mechanism": _s(ev.get("mechanism"), 400),
            "category": _norm_category(ev.get("category"), s["direction"]),
            "scope": _norm_scope(ev.get("scope")),
            "confidence": _norm_conf(ev.get("confidence")),
        })
    return out


async def get_stock_history(symbol: str, *, refresh: bool = False) -> dict:
    """คืนไทม์ไลน์ประวัติศาสตร์ราคาของหุ้นตัวเดียว (ภาษาไทย). อ่าน cache ก่อน เว้นแต่ refresh."""
    key = symbol.upper().strip()
    if not key:
        raise ValueError("กรุณาระบุสัญลักษณ์หุ้น")
    is_thai = thai_sec.is_thai_symbol(key)

    cache = _cache_path(key)
    if not refresh and cache.exists() and time.time() - cache.stat().st_mtime < _TTL:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass

    candles = await _fetch_candles(key)
    series = _resample_monthly(candles)
    if len(series) < 6:
        raise ValueError("ข้อมูลราคาย้อนหลังไม่พอสำหรับไล่ประวัติศาสตร์ (ต้องมีอย่างน้อย ~6 เดือน)")

    swings = _best_swings(series)
    if not swings:
        raise ValueError("ไม่พบช่วงที่ราคาขยับแรงพอจะไล่เป็นเหตุการณ์ได้")

    settings = get_settings()
    if not settings.llm_enabled():
        raise ValueError("ต้องตั้งค่าคีย์ AI (เช่น Gemini ฟรี) เพื่อให้ AI ไล่เหตุการณ์ประวัติศาสตร์ราคา")

    name, sector = _company_name(key)
    if is_thai and not name:
        try:
            from app.fundamentals import get_offline
            name = (get_offline(key) or {}).get("long_name")
        except Exception:  # noqa: BLE001
            pass

    # ---- สร้าง prompt: ให้ LLM เห็นช่วง swing จริง แล้วเติมเหตุการณ์ ----
    header = f"บริษัท: {name or key} ({key})" + (f" · กลุ่ม: {sector}" if sector else "")
    lines = []
    for s in swings:
        arrow = "▲ ขึ้น" if s["direction"] == "up" else "▼ ลง"
        lines.append(
            f"[id {s['id']}] ช่วง {s['start_date']} → {s['end_date']} : "
            f"ราคา {s['start_price']} → {s['end_price']} "
            f"({'+' if s['change_pct'] >= 0 else ''}{s['change_pct']}% {arrow})"
        )
    user_msg = (
        f"{header}\n\n"
        "ด้านล่างคือ 'ช่วงที่ราคาขยับแรง' ที่ตรวจจับจากกราฟราคาจริง (ราคาปิดรายเดือน) "
        "เรียงตามเวลาจากอดีต→ปัจจุบัน สำหรับแต่ละ id ให้ระบุเหตุการณ์จริงในโลกที่ทำให้ราคา"
        "ขยับทิศนั้นในช่วงเวลานั้น พร้อมกลไกและระดับความมั่นใจ ตามรูปแบบ JSON ที่กำหนด:\n\n"
        + "\n".join(lines)
    )
    system = SYSTEM_PROMPT + "\n\n" + _OUTPUT_CONTRACT

    exclude: set = set()
    last_err: Exception | None = None
    payload: dict = {}
    for attempt in range(2):
        try:
            text = await llm.complete(system, user_msg, exclude=exclude)
            payload = _extract_json(text)
            break
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            reason = str(exc).lower()
            is_quota = "429" in reason or "quota" in reason or "resource_exhausted" in reason
            cur = settings.resolve_llm(exclude=exclude)["provider"]
            if is_quota and attempt == 0 and cur not in ("none", ""):
                exclude.add(cur)
                continue
            raise ValueError(f"AI ไล่เหตุการณ์ประวัติศาสตร์ราคาไม่สำเร็จ: {exc}") from exc
    else:  # pragma: no cover
        raise ValueError(f"AI ไล่เหตุการณ์ประวัติศาสตร์ราคาไม่สำเร็จ: {last_err}")

    events = _merge_events(swings, payload)

    first = series[0]
    last = series[-1]
    peak = max(series, key=lambda p: p["close"])
    trough = min(series, key=lambda p: p["close"])
    result = {
        "symbol": key,
        "market": "TH" if is_thai else "US",
        "name": name,
        "sector": sector,
        "overview": _s(payload.get("overview"), 800),
        "price_series": [{"time": p["time"], "value": round(p["close"], 2)} for p in series],
        "events": events,
        "stats": {
            "first_date": first["date"], "first_price": round(first["close"], 2),
            "last_date": last["date"], "last_price": round(last["close"], 2),
            "all_time_high": round(peak["close"], 2), "ath_date": peak["date"],
            "all_time_low": round(trough["close"], 2), "atl_date": trough["date"],
            "total_change_pct": round((last["close"] / first["close"] - 1) * 100, 1) if first["close"] > 0 else None,
            "months": len(series),
        },
        "generated_at": int(time.time()),
        "disclaimer": (
            "จุดที่ราคาขยับคำนวณจากราคาจริง แต่ 'สาเหตุ' เป็นการวิเคราะห์ของ AI จากความรู้ประวัติหุ้น "
            "อาจคลาดเคลื่อนหรือไม่ใช่เหตุจริงทั้งหมด (correlation ≠ causation) — ใช้เป็นจุดเริ่มค้นคว้าต่อ ไม่ใช่คำแนะนำซื้อขาย"
        ),
    }
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result
