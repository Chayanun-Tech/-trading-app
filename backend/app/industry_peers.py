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

# ── จับหุ้น "ภายนอก S&P 500" (ADR ต่างชาติ) เข้ากลุ่ม GICS ของ universe ─────────────────
# Yahoo ใช้ชื่อ sector ต่างจาก GICS เล็กน้อย — แม็ปเป็นชื่อ GICS Sector ที่ใช้ในไฟล์ constituents
_YAHOO_SECTOR_TO_GICS = {
    "technology": "Information Technology",
    "healthcare": "Health Care",
    "financial services": "Financials",
    "consumer cyclical": "Consumer Discretionary",
    "consumer defensive": "Consumer Staples",
    "communication services": "Communication Services",
    "energy": "Energy",
    "industrials": "Industrials",
    "basic materials": "Materials",
    "real estate": "Real Estate",
    "utilities": "Utilities",
}
# คำที่ไม่ช่วยแยกกลุ่ม (ตัดทิ้งก่อนจับคู่ token) + synonym ให้ตรงคำศัพท์ GICS
_MATCH_STOPWORDS = {"and", "the", "of", "for", "general", "specialty", "diversified",
                    "other", "nec", "services", "products", "misc", "miscellaneous"}
_MATCH_SYNONYMS = {"auto": "automobile", "autos": "automobile", "drug": "pharmaceutical",
                   "drugs": "pharmaceutical", "pharma": "pharmaceutical", "biotech": "biotechnology",
                   "chip": "semiconductor", "chips": "semiconductor", "telecom": "telecommunication"}


def _norm_tokens(s: str) -> set[str]:
    """แตกชื่ออุตสาหกรรมเป็นชุด token ที่เทียบข้าม taxonomy ได้: lowercase, ตัดอักขระไม่ใช่ตัวอักษร,
    ตัด 's' ท้ายคำ (semiconductors→semiconductor), ตัด stopword, แทน synonym."""
    import re
    out: set[str] = set()
    for raw in re.split(r"[^a-z0-9]+", (s or "").lower()):
        if not raw:
            continue
        t = raw[:-1] if len(raw) > 3 and raw.endswith("s") else raw
        t = _MATCH_SYNONYMS.get(raw, _MATCH_SYNONYMS.get(t, t))
        if t and t not in _MATCH_STOPWORDS:
            out.add(t)
    return out


def _best_sub_industry(industry_name: str, sub_industries: set[str]) -> str | None:
    """หา GICS Sub-Industry ที่ตรงกับชื่ออุตสาหกรรมของ Yahoo/SEC มากที่สุด ด้วย token overlap.
    คืนเฉพาะเมื่อ 'ชนะเดี่ยว' ที่อันดับ 1 (ไม่เสมอ score+jaccard) — กันจับผิดตอนกำกวม เช่น
    'Internet Retail' ที่ก้ำกึ่งหลายกลุ่ม จะคืน None แล้วปล่อยให้ตกไปเทียบระดับ sector แทน."""
    qt = _norm_tokens(industry_name)
    if not qt:
        return None
    scored = []
    for sub in sub_industries:
        st = _norm_tokens(sub)
        overlap = len(qt & st)
        if overlap:
            jac = overlap / len(qt | st)
            scored.append((overlap, round(jac, 4), sub))
    if not scored:
        return None
    scored.sort(reverse=True)
    if len(scored) >= 2 and scored[0][:2] == scored[1][:2]:
        return None  # เสมอที่อันดับ 1 = กำกวม
    return scored[0][2]


def classify_external(universe: list[dict], sector: str | None, industry: str | None,
                      sic_desc: str | None = None) -> dict:
    """จัดหุ้นภายนอก S&P 500 (ADR) เข้ากลุ่ม GICS ของ universe: คืน {sub, sector, matched_on}
    เป็น 'เมล็ด' ให้ peers_for ป้อนตรรกะเลือกกลุ่ม (sub-industry ก่อน ไม่พอ fallback sector) เดิม."""
    sub_set = {u["sub_industry"] for u in universe if u.get("sub_industry")}
    sub = _best_sub_industry(industry, sub_set) if industry else None
    matched_on = f"Yahoo industry '{industry}'" if sub else None
    if not sub and sic_desc:  # industry ไม่ช่วย ลองใช้คำอธิบาย SIC ของ SEC เสริม
        sub = _best_sub_industry(sic_desc, sub_set)
        if sub:
            matched_on = f"SEC SIC '{sic_desc}'"
    gics_sector = _YAHOO_SECTOR_TO_GICS.get((sector or "").strip().lower())
    return {"sub": sub, "sector": gics_sector, "matched_on": matched_on}


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


def _pe_note(u: dict) -> str | None:
    """เหตุผลว่าทำไมหุ้นตัวนี้ไม่มีค่า PE ให้แสดง (คืน None ถ้ามี PE อยู่แล้ว) — ให้ frontend เอาไป
    ทำ tooltip บนช่อง "—" กันเข้าใจผิดว่าข้อมูลหาย ทั้งที่จริงคือ 'คิด P/E ไม่ได้'."""
    if u.get("pe") is not None:
        return None
    basis = str(u.get("basis") or "")
    if basis and not basis.startswith("P/E"):
        # กลุ่มธนาคาร/ประกัน/REIT ประเมินด้วยตัวคูณอื่น (P/B, P/FFO) — P/E ไม่มีความหมายกับกลุ่มนี้
        return f"กลุ่มนี้ประเมินด้วย {basis} ไม่ใช่ P/E จึงไม่มีค่า PE ให้เทียบ"
    ps = u.get("per_share")
    if isinstance(ps, (int, float)) and ps <= 0:
        return "กำไรต่อหุ้น (EPS) ปีล่าสุดติดลบ/ศูนย์ — คิด P/E ไม่ได้"
    # ไม่มีในแคชสแกนมูลค่าเลย = ประเมินราคายุติธรรมด้วยตัวคูณไม่ได้ (มักเป็นหุ้นขาดทุน/ข้อมูลงบไม่พอ)
    return "ยังประเมิน P/E ไม่ได้ (หุ้นขาดทุนหรือข้อมูลงบไม่พอ)"


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


def _eps_yoy() -> dict[str, float]:
    """map symbol → EPS (diluted) โต YoY ล่าสุด (%) จากแคชสแกนรายได้."""
    data = _load_json(_REVENUE_CACHE)
    out: dict[str, float] = {}
    for r in data.get("results", []):
        sym = _to_sym(r.get("symbol") or "")
        v = r.get("latest_eps_yoy_pct")
        if sym and isinstance(v, (int, float)):
            out[sym] = v
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
    eyoy = _eps_yoy()
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
            "basis": vr.get("basis"),
            "upside_pct": vr.get("upside_pct"),
            "yoy_pct": yoy.get(sym),
            "eps_yoy_pct": eyoy.get(sym),
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
    eps_yoys = _stat_block([u["eps_yoy_pct"] for u in members if isinstance(u.get("eps_yoy_pct"), (int, float))])
    return {
        "level": key,
        "name": value,
        "pe_median": pe_stat["median"],
        "pe_mean": pe_stat["mean"],
        "n": pe_stat["n"],
        "upside_median": ups["median"] if ups else None,
        "yoy_median": yoys["median"] if yoys else None,
        "eps_yoy_median": eps_yoys["median"] if eps_yoys else None,
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


def _select_group(universe: list[dict], sub: str | None, sector: str | None) -> dict | None:
    """เลือกกลุ่มเทียบ: sub-industry ก่อน (n≥MIN_PEERS) ไม่พอ fallback sector — ตรรกะเดิม แชร์ทั้ง
    หุ้น S&P 500 และหุ้นภายนอกที่จับกลุ่มมาแล้ว."""
    industry = _group_stats(universe, "sub_industry", sub) if sub else None
    if not industry or industry["n"] < MIN_PEERS:
        sector_stat = _group_stats(universe, "sector", sector) if sector else None
        if sector_stat and (not industry or sector_stat["n"] > industry["n"]):
            industry = sector_stat
    return industry


def peers_for(symbol: str, *, ext_sector: str | None = None, ext_industry: str | None = None,
              ext_sic_desc: str | None = None, ext_name: str | None = None,
              ext_pe: float | None = None, ext_eps: float | None = None,
              ext_price: float | None = None, ext_yoy_pct: float | None = None,
              ext_eps_yoy_pct: float | None = None) -> dict:
    """เลนส์เทียบอุตสาหกรรมของหุ้นตัวเดียว. คืน dict เสมอ (degrade เอง ถ้าข้อมูลเพื่อนไม่พอ).

    หุ้นใน S&P 500: จัดกลุ่มจาก GICS ในไฟล์ constituents (ไม่ยิงเน็ต).
    หุ้นภายนอก (ADR ต่างชาติ): route จะส่ง ext_* (sector/industry จาก Yahoo, sicDescription จาก SEC,
    pe/eps/price ของหุ้นนั้น) มาให้ → จับเข้ากลุ่ม GICS ของ universe แล้ว reuse ตรรกะเดิมทั้งชุด."""
    sym = _to_sym(symbol)
    if not sym:
        raise ValueError("กรุณาระบุสัญลักษณ์หุ้น")

    universe = _build_universe()
    const = load_constituents()
    meta = const.get(sym)

    as_of = _value_cache_as_of()
    scan_live = can_scan_live()

    is_external = False
    matched_on = None
    if meta:
        sector, sub, name = meta["sector"], meta["sub_industry"], meta["name"]
    else:
        # หุ้นภายนอก S&P 500 — จับเข้ากลุ่ม GICS จาก sector/industry ที่ route ส่งมา (ADR ต่างชาติ)
        cls = classify_external(universe, ext_sector, ext_industry, ext_sic_desc)
        sub, sector, matched_on = cls["sub"], cls["sector"], cls["matched_on"]
        name = ext_name or sym
        is_external = True
        if not sub and not sector:
            return {"symbol": sym, "name": ext_name, "gics_sector": None, "gics_sub_industry": None,
                    "industry": None, "peers": [], "justified": None, "as_of": as_of,
                    "can_scan_live": scan_live, "is_external": True,
                    "note": ("หุ้นนี้ไม่อยู่ใน S&P 500 และจับกลุ่มอุตสาหกรรมอัตโนมัติไม่ได้ "
                             "(ไม่มีข้อมูล sector/industry) — ยังไม่มีคู่เทียบให้")}

    industry = _select_group(universe, sub, sector)

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
            "pe_note": _pe_note(u),
            "upside_pct": u.get("upside_pct"),
            "yoy_pct": round(u["yoy_pct"], 1) if isinstance(u.get("yoy_pct"), (int, float)) else None,
            "eps_yoy_pct": round(u["eps_yoy_pct"], 1) if isinstance(u.get("eps_yoy_pct"), (int, float)) else None,
            "is_focus": u["symbol"] == sym,
        })

    # ข้อ 3: ราคาที่ควรเป็นตาม PE อุตสาหกรรม = EPS จริง × PE median กลุ่ม.
    # เงื่อนไข: หุ้นตัวนี้ต้องถูกประเมินด้วย P/E เอง (focus.pe คำนวณได้) — per_share ถึงจะเป็น EPS จริง.
    # กลุ่มธนาคาร/REIT ที่ประเมินด้วย P/B, P/FFO จะไม่โชว์การ์ดนี้ (การคูณ PE กับ BVPS/FFO ไร้ความหมาย)
    justified = None
    if is_external:
        # ADR ไม่อยู่ใน universe — เพิ่มเป็นแถว focus เอง (PE/EPS/ราคาจาก Yahoo ระดับ ADR สกุล USD)
        focus_pe = round(ext_pe, 1) if isinstance(ext_pe, (int, float)) and ext_pe > 0 else None
        peers_out.append({
            "symbol": sym, "name": name, "pe": focus_pe,
            "pe_note": None if focus_pe is not None else "ยังคำนวณ P/E ของหุ้นนี้ไม่ได้",
            "upside_pct": None,
            "yoy_pct": round(ext_yoy_pct, 1) if isinstance(ext_yoy_pct, (int, float)) else None,
            "eps_yoy_pct": round(ext_eps_yoy_pct, 1) if isinstance(ext_eps_yoy_pct, (int, float)) else None,
            "is_focus": True,
        })
        if industry and isinstance(ext_eps, (int, float)) and ext_eps > 0 and focus_pe is not None:
            ind_pe = industry["pe_median"]
            implied = round(ext_eps * ind_pe, 2)
            price = ext_price
            upside = round((implied / price - 1) * 100, 1) if isinstance(price, (int, float)) and price > 0 else None
            justified = {
                "eps": round(ext_eps, 2), "current_pe": focus_pe, "industry_pe": ind_pe,
                "implied_price": implied,
                "current_price": round(price, 2) if isinstance(price, (int, float)) else None,
                "upside_pct": upside,
            }
    else:
        focus = next((u for u in universe if u["symbol"] == sym), None)
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

    # เรียง: มี PE ก่อน (จากถูกไปแพง) แล้วค่อยตัวไม่มี PE
    peers_out.sort(key=lambda p: (p["pe"] is None, p["pe"] if p["pe"] is not None else 0))

    out = {
        "symbol": sym,
        "name": name,
        "gics_sector": sector or None,
        "gics_sub_industry": sub or None,
        "industry": industry,
        "peers": peers_out,
        "justified": justified,
        "as_of": as_of,
        "can_scan_live": scan_live,
    }
    if is_external:
        out["is_external"] = True
        if industry:
            lvl = "อุตสาหกรรมย่อย" if industry["level"] == "sub_industry" else "กลุ่มเซกเตอร์"
            src = f"จับกลุ่มจาก {matched_on} → {lvl} \"{industry['name']}\"" if matched_on \
                else f"จับกลุ่มระดับ {lvl} \"{industry['name']}\" จาก sector"
            out["match_note"] = f"{src} — ADR ต่างชาติ (ไม่ใช่สมาชิก S&P 500 ทางการ) จับคู่โดยประมาณ"
        else:
            out["match_note"] = "ADR ต่างชาติ — ยังจับกลุ่มเทียบไม่ได้"
    return out
