"""เลนส์ "เทียบอุตสาหกรรม" — รวม PE เฉลี่ยของกลุ่ม + ตารางคู่แข่ง + ราคาที่ควรเป็นตาม PE กลุ่ม.

โมดูลนี้ไม่ยิงเน็ตเลย: อ่านจากไฟล์แคชที่สแกนไว้แล้ว (data_sp500_value_scan.json,
data_sp500_revenue_scan.json) join กับรายชื่อสมาชิก S&P 500 (sp500_constituents.csv ที่มี
GICS Sub-Industry) แล้วสรุปเป็นสถิติต่อ "อุตสาหกรรม" — จึงตอบเร็วมากและใช้ได้ทั้งบนคลาวด์.

การจัดกลุ่ม: ใช้ GICS Sub-Industry ก่อน (ใกล้ระดับ "Industry" ของ Finviz map มากที่สุด) ถ้ากลุ่ม
นั้นมีหุ้นที่คำนวณ PE ได้ < MIN_PEERS ตัว จะ fallback ไปใช้ GICS Sector (กว้างกว่า แต่ตัวอย่างเยอะ
พอเชื่อถือได้) เพื่อไม่ให้ median เพี้ยนเพราะกลุ่มเล็กเกินไป.

PE ปัจจุบันต่อหุ้น = price ÷ per_share (EPS TTM) — ใช้ได้เฉพาะแถวที่ตัวสแกนมูลค่าประเมินด้วย
"P/E median" (กลุ่มทั่วไป/วัฏจักร ฯลฯ) เพราะกลุ่มที่ประเมินด้วย P/B, P/FFO จะไม่มี EPS ให้คิด PE.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from statistics import mean, median

_HERE = Path(__file__).resolve().parent
_CSV_PATH = _HERE / "sp500_constituents.csv"
_VALUE_CACHE = _HERE / "data_sp500_value_scan.json"
_REVENUE_CACHE = _HERE / "data_sp500_revenue_scan.json"

MIN_PEERS = 4          # กลุ่มต้องมีหุ้นคำนวณ PE ได้อย่างน้อยเท่านี้ ถึงจะเชื่อ median ของ sub-industry
PE_CAP = 200.0         # ตัด PE สุดโต่ง (>200) และ ≤0 ทิ้งก่อนหา median — กัน outlier ลาก


def _to_sym(s: str) -> str:
    # SEC/Yahoo ใช้ขีด (-) แทนจุดในหุ้นหลาย class เช่น BRK.B → BRK-B (ให้ตรงกับที่แคชสแกนเก็บ)
    return (s or "").strip().upper().replace(".", "-")


def load_constituents() -> dict[str, dict]:
    """map symbol → {name, sector, sub_industry} จาก CSV รายชื่อ S&P 500."""
    out: dict[str, dict] = {}
    if not _CSV_PATH.exists():
        return out
    with _CSV_PATH.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            sym = _to_sym(row.get("Symbol") or "")
            if not sym:
                continue
            out[sym] = {
                "name": (row.get("Security") or "").strip(),
                "sector": (row.get("GICS Sector") or "").strip(),
                "sub_industry": (row.get("GICS Sub-Industry") or "").strip(),
            }
    return out


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _pe_of(row: dict) -> float | None:
    """PE ปัจจุบัน = price ÷ EPS(TTM) เฉพาะแถวที่ประเมินด้วย 'P/E median'. คืน None ถ้าคิดไม่ได้."""
    if not str(row.get("basis") or "").startswith("P/E"):
        return None
    ps = row.get("per_share")
    price = row.get("price")
    if not isinstance(ps, (int, float)) or not isinstance(price, (int, float)) or ps <= 0:
        return None
    pe = price / ps
    if pe <= 0 or pe > PE_CAP or pe != pe:  # ตัด ≤0 / สุดโต่ง / NaN
        return None
    return pe


def _value_rows() -> dict[str, dict]:
    """map symbol → แถวจากแคชสแกนมูลค่า (มี per_share, price, basis, upside_pct)."""
    data = _load_json(_VALUE_CACHE)
    return {_to_sym(r.get("symbol") or ""): r for r in data.get("results", []) if r.get("symbol")}


def _value_cache_as_of() -> float | None:
    """เวลาที่สแกนมูลค่าล่าสุด (unix ts) — ใช้บอกผู้ใช้ว่าข้อมูล PE กลุ่ม 'สด' แค่ไหน. None ถ้าไม่มีแคช."""
    ts = _load_json(_VALUE_CACHE).get("as_of")
    return float(ts) if isinstance(ts, (int, float)) else None


def can_scan_live() -> bool:
    """สแกนสดได้เฉพาะตอนรันในเครื่อง (มี .git) — เหมือนตัวสแกนมูลค่า. บนคลาวด์เสิร์ฟแคชอย่างเดียว."""
    return (_HERE.parents[1] / ".git").exists()


def _revenue_yoy() -> dict[str, float]:
    """map symbol → โตรายได้ YoY ล่าสุด (%) จากแคชสแกนรายได้."""
    data = _load_json(_REVENUE_CACHE)
    out: dict[str, float] = {}
    for r in data.get("results", []):
        sym = _to_sym(r.get("symbol") or "")
        yoy = r.get("latest_yoy_pct")
        if sym and isinstance(yoy, (int, float)):
            out[sym] = yoy
    return out


def _stat_block(values: list[float]) -> dict | None:
    clean = [v for v in values if isinstance(v, (int, float)) and v == v]
    if not clean:
        return None
    return {"median": round(median(clean), 1), "mean": round(mean(clean), 1), "n": len(clean)}


def _build_universe() -> list[dict]:
    """รวมทุกแหล่งเป็นรายการหุ้นเดียว: symbol, name, sector, sub_industry, pe, upside_pct, yoy_pct."""
    const = load_constituents()
    vrows = _value_rows()
    yoy = _revenue_yoy()
    universe: list[dict] = []
    for sym, meta in const.items():
        vr = vrows.get(sym) or {}
        universe.append({
            "symbol": sym,
            "name": meta["name"],
            "sector": meta["sector"],
            "sub_industry": meta["sub_industry"],
            "pe": _pe_of(vr),
            "price": vr.get("price"),
            "per_share": vr.get("per_share"),
            "upside_pct": vr.get("upside_pct"),
            "yoy_pct": yoy.get(sym),
        })
    return universe


def _group_stats(universe: list[dict], key: str, value: str) -> dict | None:
    """สถิติ PE/upside/yoy ของกลุ่ม (key = 'sub_industry' หรือ 'sector', value = ชื่อกลุ่ม)."""
    members = [u for u in universe if u.get(key) == value]
    pes = [u["pe"] for u in members if u.get("pe") is not None]
    pe_stat = _stat_block(pes)
    if pe_stat is None:
        return None
    ups = _stat_block([u["upside_pct"] for u in members if isinstance(u.get("upside_pct"), (int, float))])
    yoys = _stat_block([u["yoy_pct"] for u in members if isinstance(u.get("yoy_pct"), (int, float))])
    return {
        "level": key,
        "name": value,
        "pe_median": pe_stat["median"],
        "pe_mean": pe_stat["mean"],
        "n": pe_stat["n"],
        "upside_median": ups["median"] if ups else None,
        "yoy_median": yoys["median"] if yoys else None,
    }


def industry_pe_map() -> dict[str, dict]:
    """map symbol → {industry_pe, industry_name, level} ตามกลุ่มที่เลือก (sub-industry ก่อน,
    ไม่พอ fallback sector) — ใช้แนบคอลัมน์ 'PE กลุ่ม' ในตารางสแกนมูลค่า. คำนวณกลุ่มครั้งเดียวแล้ว map."""
    universe = _build_universe()
    const = load_constituents()
    subs = {u["sub_industry"] for u in universe if u["sub_industry"]}
    secs = {u["sector"] for u in universe if u["sector"]}
    sub_stats = {s: _group_stats(universe, "sub_industry", s) for s in subs}
    sec_stats = {s: _group_stats(universe, "sector", s) for s in secs}
    out: dict[str, dict] = {}
    for sym, meta in const.items():
        ind = sub_stats.get(meta["sub_industry"])
        if not ind or ind["n"] < MIN_PEERS:
            ss = sec_stats.get(meta["sector"])
            if ss and (not ind or ss["n"] > ind["n"]):
                ind = ss
        if ind:
            out[sym] = {"industry_pe": ind["pe_median"], "industry_name": ind["name"], "level": ind["level"]}
    return out


def attach_industry_pe(candidates: list[dict]) -> list[dict]:
    """แนบ industry_pe/industry_name ให้แต่ละแถวผลสแกน (join ด้วย symbol). ไม่แก้ของเดิม คืนลิสต์ใหม่."""
    pe_map = industry_pe_map()
    out = []
    for c in candidates or []:
        info = pe_map.get(_to_sym(c.get("symbol") or ""))
        out.append({**c, **(info or {})})
    return out


def peers_for(symbol: str) -> dict:
    """เลนส์เทียบอุตสาหกรรมของหุ้นตัวเดียว. คืน dict เสมอ (degrade เอง ถ้าข้อมูลเพื่อนไม่พอ)."""
    sym = _to_sym(symbol)
    if not sym:
        raise ValueError("กรุณาระบุสัญลักษณ์หุ้น")

    universe = _build_universe()
    const = load_constituents()
    meta = const.get(sym)

    as_of = _value_cache_as_of()
    scan_live = can_scan_live()

    # หุ้นนอก S&P 500 (ไม่มีในรายชื่อ) — คืนโครงว่างให้ frontend ขึ้น "ข้อมูลเพื่อนไม่พอ" ไม่ error
    if not meta:
        return {"symbol": sym, "name": None, "gics_sector": None, "gics_sub_industry": None,
                "industry": None, "peers": [], "justified": None, "as_of": as_of,
                "can_scan_live": scan_live,
                "note": "หุ้นนี้ไม่อยู่ในรายชื่อ S&P 500 — ยังไม่มีข้อมูลอุตสาหกรรมให้เทียบ"}

    sector = meta["sector"]
    sub = meta["sub_industry"]

    # จัดกลุ่ม: sub-industry ก่อน (n≥MIN_PEERS) ไม่พอ fallback sector
    industry = _group_stats(universe, "sub_industry", sub) if sub else None
    if not industry or industry["n"] < MIN_PEERS:
        sector_stat = _group_stats(universe, "sector", sector) if sector else None
        if sector_stat and (not industry or sector_stat["n"] > industry["n"]):
            industry = sector_stat

    # รายชื่อเพื่อนในกลุ่มที่เลือก (คู่แข่ง) — เรียงตาม PE จากน้อยไปมาก
    group_key = industry["level"] if industry else "sub_industry"
    group_val = industry["name"] if industry else sub
    peers = [u for u in universe if u.get(group_key) == group_val]
    peers_out = []
    for u in peers:
        peers_out.append({
            "symbol": u["symbol"],
            "name": u["name"],
            "pe": round(u["pe"], 1) if u.get("pe") is not None else None,
            "upside_pct": u.get("upside_pct"),
            "yoy_pct": round(u["yoy_pct"], 1) if isinstance(u.get("yoy_pct"), (int, float)) else None,
            "is_focus": u["symbol"] == sym,
        })
    # เรียง: มี PE ก่อน (จากถูกไปแพง) แล้วค่อยตัวไม่มี PE
    peers_out.sort(key=lambda p: (p["pe"] is None, p["pe"] if p["pe"] is not None else 0))

    # ข้อ 3: ราคาที่ควรเป็นตาม PE อุตสาหกรรม = EPS จริง × PE median กลุ่ม.
    # เงื่อนไข: หุ้นตัวนี้ต้องถูกประเมินด้วย P/E เอง (focus.pe คำนวณได้) — per_share ถึงจะเป็น EPS จริง.
    # กลุ่มธนาคาร/REIT ที่ประเมินด้วย P/B, P/FFO จะไม่โชว์การ์ดนี้ (การคูณ PE กับ BVPS/FFO ไร้ความหมาย)
    focus = next((u for u in universe if u["symbol"] == sym), None)
    justified = None
    if focus and industry and focus.get("pe") is not None and isinstance(focus.get("per_share"), (int, float)):
        eps = focus["per_share"]
        price = focus.get("price")
        ind_pe = industry["pe_median"]
        implied = round(eps * ind_pe, 2)
        upside = round((implied / price - 1) * 100, 1) if isinstance(price, (int, float)) and price > 0 else None
        justified = {
            "eps": round(eps, 2),
            "current_pe": round(focus["pe"], 1),
            "industry_pe": ind_pe,
            "implied_price": implied,
            "current_price": round(price, 2) if isinstance(price, (int, float)) else None,
            "upside_pct": upside,
        }

    return {
        "symbol": sym,
        "name": meta["name"],
        "gics_sector": sector or None,
        "gics_sub_industry": sub or None,
        "industry": industry,
        "peers": peers_out,
        "justified": justified,
        "as_of": as_of,
        "can_scan_live": scan_live,
    }
