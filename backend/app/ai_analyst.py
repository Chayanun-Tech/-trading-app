"""แถบ 'ผู้ช่วยวิเคราะห์ AI' — รวม 7 กรอบวิเคราะห์หุ้นจาก prompt ของผู้ใช้ ให้เป็น 5 โหมด
โดย 'ดึงข้อมูลจริงออนไลน์เองทั้งหมด' (ผู้ใช้ไม่ต้องหา/วางข้อมูล) แล้วป้อนให้ AI วิเคราะห์.

โหมด (ตัดของซ้ำจาก 7 → 5):
- financial : สแกนสุขภาพการเงิน + สรุปงบล่าสุด (รวม prompt #1 + #6)
- news      : แกะข่าวว่ากระทบหุ้นยังไง (prompt #2)
- compare   : เทียบหุ้นในกลุ่มเดียวกันหลายตัว (prompt #3)
- bull_bear : ฟังสองฝั่ง Bull vs Bear (prompt #4)
- decision  : กรอบช่วยตัดสินใจ ซื้อ/ขาย/ถือ — สังเคราะห์ทุกอย่าง + ฝัง risk checklist (#5) (prompt #7)

ที่มาข้อมูล (reuse ของเดิมทั้งหมด): SEC EDGAR (งบลึก/10-K), 56-1 One Report (ไทย),
fundamentals snapshot, ราคา provider, intrinsic value, และข่าวจาก Google News RSS (ฟรี ไม่ต้องคีย์).
AI ทำหน้าที่ 'วิเคราะห์/เรียบเรียง' เท่านั้น — ตัวเลขทั้งหมดมาจากข้อมูลจริง ห้ามแต่งเอง.

cache ผลลัพธ์ลงดิสก์ (TTL สั้น เพราะข่าว/ราคาเปลี่ยนไว) เพื่อประหยัดโควตา Gemini ฟรี.
"""
from __future__ import annotations

import asyncio
import html
import json
import re
import time
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx

from app import edgar, llm, thai_sec
from app.config import get_settings
from app.financials import build_financials
from app.fundamentals import get_fundamentals, get_offline
from app.intrinsic_value import build_iv_report

_CACHE_DIR = Path(__file__).resolve().parents[1].parent / "data" / "ai_analyst"
_TTL = 6 * 3600  # ข่าว/ราคาขยับไว — cache สั้น
_UA = {"User-Agent": "ChayanunTradingApp/1.0 (ai analyst; contact chayanun250841@gmail.com)"}

_MODES = {"financial", "news", "compare", "bull_bear", "decision"}


# ============================================================ JSON utilities ==
def _extract_json(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1] if "```" in t[3:] else t
        t = t.lstrip("json").strip("` \n")
    start, end = t.find("{"), t.rfind("}")
    if start == -1:
        raise ValueError("ไม่พบ JSON ในคำตอบของโมเดล")
    body = t[start:end + 1] if end > start else t[start:]
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return _salvage_json(t[start:])


def _salvage_json(s: str) -> dict:
    """กู้ JSON ที่ถูกตัดกลางคัน (Gemini ฟรีตัดที่ ~6000 โทเค็น) — ปิดวงเล็บที่ค้าง."""
    in_str, esc, last_safe, depth = False, False, -1, 0
    for i, ch in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
        elif ch == "," and depth <= 2:
            last_safe = i
    if last_safe == -1:
        raise ValueError("กู้ JSON ที่ถูกตัดไม่สำเร็จ")
    frag = s[:last_safe]
    stack: list[str] = []
    in_str, esc = False, False
    for ch in frag:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in "}]" and stack:
            stack.pop()
    if in_str:
        frag += '"'
    frag += "".join(reversed(stack))
    return json.loads(frag)


async def _run_llm(system: str, user_msg: str) -> dict:
    """เรียก LLM พร้อม fallback provider เมื่อโควตาหมด (เลียน trend_radar)."""
    settings = get_settings()
    exclude: set = set()
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            text = await llm.complete(system, user_msg, exclude=exclude)
            return _extract_json(text)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            reason = str(exc).lower()
            is_quota = "429" in reason or "quota" in reason or "resource_exhausted" in reason
            cur = settings.resolve_llm(exclude=exclude)["provider"]
            if is_quota and attempt == 0 and cur not in ("none", ""):
                exclude.add(cur)
                continue
            raise ValueError(f"AI วิเคราะห์ไม่สำเร็จ: {exc}") from exc
    raise ValueError(f"AI วิเคราะห์ไม่สำเร็จ: {last_err}")


# =============================================================== formatting ====
def _fmt(v) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, (int, float)):
        a = abs(v)
        if a >= 1e9:
            return f"{v / 1e9:.2f}B"
        if a >= 1e6:
            return f"{v / 1e6:.2f}M"
        if a >= 1e3:
            return f"{v / 1e3:.2f}K"
        return f"{v:.2f}"
    return str(v)


def _pct(v) -> str:
    if v is None:
        return "n/a"
    try:
        return f"{float(v) * 100:.1f}%"
    except (TypeError, ValueError):
        return str(v)


# คีย์ metric สำคัญต่อกลุ่ม (ดึงเฉพาะที่จำเป็นเพื่อไม่ให้ context ยาวเกิน)
_KEY_METRICS = {
    "income": ["revenue", "gross_profit", "operating_income", "net_income", "eps_diluted"],
    "balance": ["cash", "total_assets", "total_debt", "long_term_debt", "total_equity"],
    "cashflow": ["operating_cash_flow", "capex", "free_cash_flow", "dividends_paid"],
    "ratios": ["gross_margin", "operating_margin", "net_margin", "roe", "roa",
               "debt_to_equity", "current_ratio"],
}


def _financials_digest(fin: dict, max_periods: int = 6) -> str:
    """ทำงบ build_financials() เป็นข้อความกระชับ: metric: ปี1=.. ปี2=.. (ล่าสุดอยู่ขวา)."""
    periods = (fin.get("periods") or [])[-max_periods:]
    if not periods:
        return ""
    n_all = len(fin.get("periods") or [])
    sl = slice(max(0, n_all - max_periods), n_all)
    lines = [f"ช่วงเวลา (เก่า→ใหม่): {', '.join(str(p) for p in periods)}"]
    for grp in fin.get("groups") or []:
        keep = _KEY_METRICS.get(grp.get("key"))
        if keep is None:
            continue
        for m in grp.get("metrics") or []:
            if m.get("key") not in keep:
                continue
            vals = (m.get("values") or [])[sl]
            is_ratio = grp.get("key") == "ratios"
            shown = [(_pct(v) if is_ratio else _fmt(v)) for v in vals]
            lines.append(f"  {m.get('label') or m.get('key')}: {', '.join(shown)}")
    return "\n".join(lines)


_FUND_FIELDS = [
    ("market_cap", "มูลค่าตลาด", _fmt), ("pe", "P/E", _fmt), ("peg", "PEG", _fmt),
    ("pb", "P/B", _fmt), ("dividend_yield", "ปันผล", _pct),
    ("revenue_growth", "การเติบโตรายได้", _pct), ("earnings_growth", "การเติบโตกำไร", _pct),
    ("gross_margin", "อัตรากำไรขั้นต้น", _pct), ("operating_margin", "อัตรากำไรจากดำเนินงาน", _pct),
    ("profit_margin", "อัตรากำไรสุทธิ", _pct), ("roe", "ROE", _pct),
    ("debt_to_equity", "หนี้สิน/ทุน", _fmt), ("current_ratio", "อัตราส่วนสภาพคล่อง", _fmt),
    ("fcf_yield", "FCF yield", _pct),
]


def _fundamentals_digest(snap: dict) -> str:
    if not snap:
        return ""
    parts = []
    for key, label, fn in _FUND_FIELDS:
        if snap.get(key) is not None:
            parts.append(f"{label}={fn(snap.get(key))}")
    price = snap.get("price")
    if price:
        parts.insert(0, f"ราคาปัจจุบัน={_fmt(price)}")
    return "; ".join(parts)


# ================================================================ news feed ===
async def _fetch_news(client: httpx.AsyncClient, query: str, limit: int = 12) -> list[dict]:
    """ข่าวล่าสุดของบริษัทจาก Google News RSS (ฟรี ไม่ต้องคีย์)."""
    q = httpx.QueryParams({"q": f"{query} when:30d", "hl": "en-US", "gl": "US", "ceid": "US:en"})
    try:
        r = await client.get(f"https://news.google.com/rss/search?{q}", headers=_UA, timeout=20)
        r.raise_for_status()
        root = ET.fromstring(r.text)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for item in list(root.iterfind(".//item"))[:limit]:
        title = (item.findtext("title") or "").strip()
        if title:
            out.append({
                "title": html.unescape(title),
                "url": item.findtext("link") or "",
                "date": (item.findtext("pubDate") or "")[:16],
                "source": (item.findtext("source") or "").strip() or "Google News",
            })
    return out


# =========================================================== context builder ==
async def _company_name(key: str, is_thai: bool) -> str:
    if is_thai:
        return (get_offline(key) or {}).get("long_name") or key
    try:
        facts = await edgar.get_company_facts(key)
        return facts.get("entityName") or key
    except Exception:  # noqa: BLE001
        return key


async def _price(key: str) -> float | None:
    from app.main import provider  # lazy: เลี่ยง circular import
    try:
        q = await provider.get_quote(key)
        return float(q.price) if q and q.price else None
    except Exception:  # noqa: BLE001
        return None


async def _gather(symbol: str, *, with_financials=True, with_business=True,
                  with_news=False, with_iv=False) -> dict:
    """ดึงข้อมูลจริงออนไลน์ทั้งหมดของหุ้น 1 ตัว แล้วคืนเป็นบล็อกข้อความให้ AI."""
    key = symbol.upper().strip()
    is_thai = thai_sec.is_thai_symbol(key)
    ctx: dict = {"symbol": key, "is_thai": is_thai}

    facts = None
    if not is_thai and (with_financials or with_business):
        try:
            facts = await edgar.get_company_facts(key)
        except Exception:  # noqa: BLE001
            facts = None

    name = await _company_name(key, is_thai)
    price = await _price(key)
    ctx["name"] = name
    ctx["price"] = price

    # fundamentals snapshot
    try:
        snap = await get_fundamentals(key, facts=facts, price=price)
        if price:
            snap = {**snap, "price": price}
        ctx["snapshot"] = snap
        ctx["fundamentals_text"] = _fundamentals_digest(snap)
    except Exception:  # noqa: BLE001
        ctx["snapshot"] = {}
        ctx["fundamentals_text"] = ""

    # งบย้อนหลังลึก
    if with_financials and facts is not None:
        try:
            fin = build_financials(facts, "annual")
            ctx["financials_text"] = _financials_digest(fin)
        except Exception:  # noqa: BLE001
            ctx["financials_text"] = ""
    else:
        ctx["financials_text"] = ""

    # คำอธิบายธุรกิจ / risk factors จากเอกสารจริง
    if with_business:
        try:
            if is_thai:
                doc = await thai_sec.get_one_report_context(key, name)
            else:
                doc = await edgar.get_10k_context(key)
            biz = (doc.get("business") or "")[:6000] if doc else ""
            mda = (doc.get("mda") or "")[:3000] if doc else ""
            ctx["business_text"] = biz
            ctx["mda_text"] = mda
        except Exception:  # noqa: BLE001
            ctx["business_text"] = ""
            ctx["mda_text"] = ""
    else:
        ctx["business_text"] = ""
        ctx["mda_text"] = ""

    # ข่าวล่าสุด
    if with_news:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            q = name if is_thai else f"{key} stock {name}"
            ctx["news"] = await _fetch_news(client, q)
    else:
        ctx["news"] = []

    # มูลค่าที่แท้จริง (IV)
    if with_iv:
        try:
            fin = build_financials(facts, "annual") if facts is not None else None
            ctx["iv"] = build_iv_report(ctx.get("snapshot") or {}, fin)
        except Exception:  # noqa: BLE001
            ctx["iv"] = None
    else:
        ctx["iv"] = None

    return ctx


def _context_block(ctx: dict, *, business=True, financials=True, news=False, iv=False) -> str:
    """ประกอบบล็อกข้อมูลจริงเป็นข้อความเดียวสำหรับ user message."""
    out = [f"หุ้น: {ctx['name']} ({ctx['symbol']})"]
    if ctx.get("price"):
        out.append(f"ราคาปัจจุบัน: {_fmt(ctx['price'])}")
    if ctx.get("fundamentals_text"):
        out.append("\n[ตัวเลขพื้นฐานล่าสุด]\n" + ctx["fundamentals_text"])
    if financials and ctx.get("financials_text"):
        out.append("\n[งบการเงินย้อนหลัง (จาก SEC EDGAR)]\n" + ctx["financials_text"])
    if iv and ctx.get("iv"):
        ivr = ctx["iv"]
        fair = ivr.get("fair_value") or ivr.get("intrinsic_value")
        out.append(f"\n[มูลค่าที่แท้จริง (IV)] ประเมินได้ ~{_fmt(fair)} "
                   f"(สรุป: {str(ivr.get('verdict') or ivr.get('summary') or '')[:200]})")
    if business and ctx.get("business_text"):
        out.append("\n[คำอธิบายธุรกิจจริงจากเอกสาร (10-K / 56-1)]\n" + ctx["business_text"])
    if business and ctx.get("mda_text"):
        out.append("\n[บทวิเคราะห์ผู้บริหาร / ปัจจัยเสี่ยง (MD&A)]\n" + ctx["mda_text"])
    if news and ctx.get("news"):
        lines = [f"- ({n['date']}) {n['title']} [{n['source']}]" for n in ctx["news"]]
        out.append("\n[ข่าวล่าสุด 30 วัน (Google News)]\n" + "\n".join(lines))
    return "\n".join(out)


# ================================================================== prompts ===
_BASE_RULES = """คุณคือนักวิเคราะห์การลงทุนมืออาชีพที่อธิบายให้คนทั่วไปเข้าใจง่าย
หลักการ:
- วิเคราะห์จาก 'ข้อมูลจริง' ที่ให้มาเท่านั้น ห้ามแต่งตัวเลขที่ไม่มีในข้อมูล ถ้าข้อมูลไม่พอให้บอกตรง ๆ
- เป็นกลาง ไม่เชียร์ซื้อ/ขาย — นี่คือเครื่องมือช่วยคิด ไม่ใช่คำแนะนำการลงทุน
- ตอบเป็นภาษาไทยที่คนทั่วไปเข้าใจ เลี่ยงศัพท์ยากเกินไป (ถ้ามีศัพท์เทคนิคให้วงเล็บแปล)
- ตอบเป็น JSON เท่านั้น ห้ามมีข้อความนอก JSON และต้องปิดวงเล็บครบสมบูรณ์เสมอ"""

_CONTRACTS = {
    "financial": """รูปแบบ JSON (เท่านั้น):
{
  "overall_health": "แข็งแรง | ปานกลาง | อ่อนแอ",
  "health_reason": "<เพราะอะไร 2-3 ประโยค>",
  "revenue_trend": "<แนวโน้มรายได้ย้อนหลัง โตหรือถดถอย อ้างตัวเลข>",
  "profit_trend": "<แนวโน้มกำไรย้อนหลัง อ้างตัวเลข>",
  "debt_cashflow": "<ภาระหนี้และกระแสเงินสด น่ากังวลไหม>",
  "latest_period": "<งบ/ตัวเลขล่าสุด ดูดีหรือแย่ มีอะไรเด่น>",
  "strengths": ["<จุดเด่น 3 ข้อ>"],
  "red_flags": ["<สัญญาณอันตราย 3 ข้อ>"],
  "questions_to_research": ["<คำถามที่ควรหาคำตอบต่อ 3-5 ข้อ>"],
  "summary": "<สรุปปิดท้าย 2-3 ประโยค>"
}""",
    "news": """รูปแบบ JSON (เท่านั้น):
{
  "headlines_summary": "<สรุปภาพรวมข่าวช่วงนี้ 2-3 ประโยค>",
  "items": [
    {"title": "<พาดหัวข่าวที่หยิบมาวิเคราะห์>",
     "impact": "บวก | ลบ | กลาง",
     "short_term": "<ผลระยะสั้นต่อราคา/อารมณ์ตลาด 1 ประโยค>",
     "long_term": "<ผลระยะยาวต่อตัวธุรกิจจริง 1 ประโยค>"}
  ],
  "winners_losers": "<ใครได้ ใครเสียประโยชน์จากข่าวเหล่านี้>",
  "fundamental_change": "เปลี่ยนพื้นฐานบริษัท | แค่ข่าวระยะสั้นที่ตลาดตื่นเต้น",
  "fundamental_reason": "<เหตุผลของบรรทัดบน แยกข้อเท็จจริงออกจากการคาดเดา>",
  "net_effect": "<สรุปสุทธิ บวกหรือลบ และนักลงทุนควรโฟกัสอะไร>"
}""",
    "compare": """รูปแบบ JSON (เท่านั้น):
{
  "table": [
    {"symbol": "<ticker>",
     "strengths": ["<จุดแข็ง 1-3 ข้อ>"],
     "weaknesses": ["<จุดอ่อน 1-3 ข้อ>"],
     "growth": "<เด่น/กลาง/อ่อน เรื่องการเติบโต + เหตุผลสั้น>",
     "stability": "<เด่น/กลาง/อ่อน เรื่องความมั่นคง>",
     "valuation": "<ถูก/เหมาะสม/แพง เทียบพื้นฐาน>",
     "key_risk": "<ความเสี่ยงเฉพาะตัว 1 ข้อ>",
     "fits": "<เหมาะกับนักลงทุนแบบไหน>"}
  ],
  "best_growth": "<ticker ที่เด่นเรื่องเติบโตสุด>",
  "best_stability": "<ticker ที่มั่นคงสุด>",
  "best_value": "<ticker ที่ราคาน่าสนใจสุด>",
  "long_term_pick": "<ถ้าเน้นลงทุนยาว ตัวไหนน่าสนใจกว่า>",
  "pick_reason": "<เพราะอะไร 2-3 ประโยค>"
}""",
    "bull_bear": """รูปแบบ JSON (เท่านั้น) — ทั้งสองฝั่งต้องแข็งแรงพอ ๆ กัน ห้ามเข้าข้างใคร:
{
  "bull": {"thesis": "<ใจความฝั่งเชียร์ซื้อ 1 ประโยค>",
            "points": ["<เหตุผลที่ดีที่สุดว่าทำไมน่าซื้อ 3-5 ข้อ>"]},
  "bear": {"thesis": "<ใจความฝั่งระวัง 1 ประโยค>",
            "points": ["<เหตุผลที่หนักแน่นที่สุดว่าทำไมควรเลี่ยง/รอ 3-5 ข้อ>"]},
  "swing_factors": ["<ปัจจัยชี้ขาดที่ต้องจับตา 3-5 ข้อ>"],
  "turns_bullish_if": "<ถ้าเกิดอะไรขึ้นถึงจะเอียงไปฝั่งซื้อ>",
  "turns_bearish_if": "<ถ้าเกิดอะไรขึ้นถึงควรถอย>",
  "neutral_summary": "<สรุปกลาง ๆ 2-3 ประโยค>"
}""",
    "decision": """รูปแบบ JSON (เท่านั้น) — ผู้ตัดสินใจสุดท้ายคือผู้ใช้ คุณแค่จัดระบบความคิด:
{
  "snapshot": "<สรุปสถานการณ์หุ้นตอนนี้ 2-3 ประโยค>",
  "bull_case": ["<เหตุผลสนับสนุน 3-4 ข้อ>"],
  "bear_case": ["<เหตุผลค้าน 3-4 ข้อ>"],
  "risks": [
    {"category": "ธุรกิจ | การเงิน | มูลค่า/ราคา | ภาพใหญ่ | มองข้าม",
     "level": "ต่ำ | กลาง | สูง",
     "detail": "<อธิบายความเสี่ยง 1 ประโยค>"}
  ],
  "valuation_view": "<ตอนนี้แพง/เหมาะสม/ถูกเทียบพื้นฐาน อ้าง IV/PE ถ้ามี>",
  "scenarios": {
    "buy_if": "<เงื่อนไขที่ถ้าเกิดควรพิจารณาซื้อ>",
    "hold_if": "<เงื่อนไขที่ควรถือรอดู>",
    "sell_if": "<เงื่อนไขที่ควรขาย/ลดน้ำหนัก>"
  },
  "what_breaks_thesis": "<'สิ่งที่ต้องเกิดขึ้น' ที่จะทำให้การลงทุนนี้พัง — ไว้เตรียมแผนรับมือ>",
  "framework_questions": ["<คำถามที่ผู้ใช้ควรตอบตัวเองก่อนตัดสินใจ 3-5 ข้อ>"],
  "reminder": "การตัดสินใจสุดท้ายเป็นของคุณ และทุกตัวเลขควรเช็กกับแหล่งต้นทางอีกครั้ง"
}"""
}

_MODE_INTRO = {
    "financial": "ภารกิจ: สแกนสุขภาพการเงินของบริษัท และสรุปงบล่าสุด ว่าควรดีใจหรือกังวล",
    "news": "ภารกิจ: แกะข่าวด้านล่างว่ากระทบหุ้นตัวนี้อย่างไร แยกข้อเท็จจริงออกจากการคาดเดา",
    "compare": "ภารกิจ: เปรียบเทียบหุ้นหลายตัวในกลุ่มเดียวกันด้านล่าง ว่าตัวไหนเด่นเรื่องอะไร",
    "bull_bear": "ภารกิจ: สวมบทนักลงทุน 2 คนที่เห็นต่าง (Bull เชียร์ซื้อ / Bear ระวัง) แล้วสรุปกลาง ๆ",
    "decision": "ภารกิจ: จัดระบบความคิดช่วยตัดสินใจ ซื้อ/ขาย/ถือ พร้อมเช็กลิสต์ความเสี่ยงทุกด้าน",
}


# ============================================================== public API ====
def _cache_path(mode: str, token: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", token.upper())[:60]
    return _CACHE_DIR / f"{mode}_{safe}.json"


async def get_analysis(mode: str, symbol: str = "", *, symbols: str = "",
                       horizon: str = "", risk: str = "", reason: str = "",
                       refresh: bool = False) -> dict:
    """จุดเข้าเดียวของทุกโหมด. ดึงข้อมูลจริงออนไลน์ → ให้ AI วิเคราะห์ → คืน JSON ที่ frontend เรนเดอร์."""
    if mode not in _MODES:
        raise ValueError(f"โหมดไม่ถูกต้อง: {mode} (รองรับ {', '.join(sorted(_MODES))})")

    settings = get_settings()
    if not settings.llm_enabled():
        raise ValueError("ต้องตั้งค่าคีย์ AI (เช่น Gemini ฟรี) ก่อนใช้ผู้ช่วยวิเคราะห์")

    # โหมด decision รับ input ของผู้ใช้ → ไม่ cache (เปลี่ยนตามคำถาม)
    user_extra = (horizon or risk or reason)
    token = symbols if mode == "compare" else symbol
    cache = _cache_path(mode, token)
    if not refresh and not user_extra and cache.exists() \
            and time.time() - cache.stat().st_mtime < _TTL:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass

    if mode == "compare":
        result = await _do_compare(symbols)
    else:
        result = await _do_single(mode, symbol, horizon=horizon, risk=risk, reason=reason)

    result["mode"] = mode
    result["generated_at"] = int(time.time())
    if not user_extra:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


async def _do_single(mode: str, symbol: str, *, horizon="", risk="", reason="") -> dict:
    key = (symbol or "").upper().strip()
    if not key:
        raise ValueError("กรุณาระบุสัญลักษณ์หุ้น")

    want_news = mode in ("news", "decision")
    want_iv = mode in ("decision",)
    ctx = await _gather(key, with_financials=True, with_business=(mode != "news"),
                        with_news=want_news, with_iv=want_iv)

    if mode == "news" and not ctx.get("news"):
        raise ValueError(f"ไม่พบข่าวล่าสุดของ {key} (ลองหุ้นที่มีข่าวภาษาอังกฤษ หรือดูแท็บข่าว)")

    block = _context_block(
        ctx,
        business=(mode != "news"),
        financials=(mode != "news"),
        news=want_news,
        iv=want_iv,
    )

    user_msg = _MODE_INTRO[mode] + "\n\n=== ข้อมูลจริง ===\n" + block
    if mode == "decision":
        extra = []
        if reason:
            extra.append(f"- เหตุผลที่ผู้ใช้สนใจ: {reason}")
        if horizon:
            extra.append(f"- ระยะเวลาที่ตั้งใจลงทุน: {horizon}")
        if risk:
            extra.append(f"- ระดับที่รับความเสี่ยงได้: {risk}")
        if extra:
            user_msg += "\n\n=== บริบทจากผู้ใช้ ===\n" + "\n".join(extra)

    system = _BASE_RULES + "\n\n" + _CONTRACTS[mode]
    data = await _run_llm(system, user_msg)
    data["symbol"] = key
    data["name"] = ctx["name"]
    data["price"] = ctx.get("price")
    if want_news:
        data["news_sources"] = [{"title": n["title"], "url": n["url"]} for n in ctx.get("news", [])][:12]
    return data


async def _discover_peers(main: str, ctx: dict, want: int = 4) -> list[str]:
    """ให้ AI เสนอ 'คู่แข่ง/หุ้นในอุตสาหกรรมเดียวกัน' แล้วคืนเฉพาะ ticker (ยังไม่ตรวจของจริง).

    เลียน trend_radar: AI เก่งเรื่อง 'ใครแข่งกับใคร' — ติกเกอร์จะถูกตรวจด้วยข้อมูลจริงทีหลัง."""
    snap = ctx.get("snapshot") or {}
    sector = snap.get("sector") or "-"
    industry = snap.get("industry") or "-"
    summary = (snap.get("summary") or ctx.get("business_text") or "")[:1200]
    system = (
        "คุณคือนักวิเคราะห์ที่รู้จักบริษัทจดทะเบียนทั่วโลก หน้าที่: หา 'คู่แข่งโดยตรง/บริษัทในกลุ่ม"
        "อุตสาหกรรมเดียวกัน' ของหุ้นที่กำหนด เพื่อเอาไปเปรียบเทียบ\n"
        "- เลือกบริษัทที่ 'แข่งในตลาดเดียวกันจริง ๆ' และมีหุ้นซื้อขายได้ (มี ticker จริง)\n"
        "- เน้นตลาดเดียวกับหุ้นต้นทาง (หุ้นไทย .BK → คู่แข่งไทย .BK; หุ้นสหรัฐ → คู่แข่งสหรัฐ)\n"
        "- ใส่เฉพาะ ticker ที่มั่นใจว่าถูกต้อง 100% ห้ามเดา ถ้าไม่แน่ใจให้ข้าม\n"
        "ตอบเป็น JSON เท่านั้น"
    )
    um = (
        f"หุ้นต้นทาง: {ctx.get('name')} ({main})\n"
        f"Sector: {sector} | Industry: {industry}\n"
        f"คำอธิบายธุรกิจ: {summary}\n\n"
        'คืน JSON: {"peers": ["<ticker1>", "<ticker2>", ...]} '
        f"ราว {want}-{want + 1} ตัว (ห้ามใส่ {main} ซ้ำ; ใช้รูปแบบ ticker เดียวกับ {main} เช่นหุ้นไทยต้องมี .BK)"
    )
    try:
        data = await _run_llm(system, um)
    except Exception:  # noqa: BLE001
        return []
    out, seen = [], {main.upper()}
    for t in (data.get("peers") or []):
        tk = str(t or "").strip().upper()
        if tk and tk not in seen and re.match(r"^[A-Z0-9.\-]{1,15}$", tk):
            seen.add(tk)
            out.append(tk)
    return out[: want + 2]


async def _do_compare(symbols: str) -> dict:
    syms = [s.strip().upper() for s in re.split(r"[,\s]+", symbols or "") if s.strip()]
    syms = list(dict.fromkeys(syms))[:5]
    if not syms:
        raise ValueError("กรุณาระบุสัญลักษณ์หุ้นอย่างน้อย 1 ตัว")

    def _sector(c: dict) -> str:
        return str(((c.get("snapshot") or {}).get("sector") or "")).strip().lower()

    ctx_by: dict[str, dict] = {}
    auto_peers = False
    if len(syms) == 1:
        # โหมดอัตโนมัติ: หาคู่แข่งใน sector/อุตสาหกรรมเดียวกันให้เอง ผู้ใช้ไม่ต้องกรอก
        auto_peers = True
        main = syms[0]
        seed = await _gather(main, with_financials=True, with_business=True, with_news=False)
        ctx_by[main] = seed
        cands = await _discover_peers(main, seed)
        # ดึงข้อมูลจริงของผู้สมัครทุกตัว → กรองด้วย 'sector ต้องตรงกับหุ้นต้นทาง' (ตัด ticker มั่ว/คนละกลุ่ม)
        pctxs = await asyncio.gather(
            *(_gather(p, with_financials=True, with_business=False, with_news=False) for p in cands),
            return_exceptions=True,
        )
        main_sec = _sector(seed)
        same_sec, fallback = [], []
        for p, c in zip(cands, pctxs):
            if not isinstance(c, dict) or not c.get("price"):
                continue  # ดึงราคาไม่ได้ = ไม่มีจริง
            ctx_by[p] = c
            if main_sec and _sector(c) == main_sec:
                same_sec.append(p)
            else:
                fallback.append(p)
        # เลือก sector เดียวกันก่อน; ถ้าไม่พอ (หรือไม่รู้ sector) ค่อยเสริมจาก fallback
        peers = (same_sec + fallback)[:4]
        syms = [main] + peers
        if len(syms) < 2:
            raise ValueError(
                f"หาคู่แข่งใน sector เดียวกันของ {main} อัตโนมัติไม่สำเร็จ — "
                "ลองใส่หลายตัวเองคั่นคอมมา เช่น AAPL,MSFT,GOOGL")

    # ดึง ctx ของหุ้นที่ยังไม่มี (กรณีผู้ใช้กรอกหลายตัวเอง)
    missing = [s for s in syms if s not in ctx_by]
    if missing:
        got = await asyncio.gather(
            *(_gather(s, with_financials=True, with_business=False, with_news=False) for s in missing),
            return_exceptions=True,
        )
        for s, c in zip(missing, got):
            if isinstance(c, dict):
                ctx_by[s] = c

    blocks, ok = [], []
    for s in syms:
        c = ctx_by.get(s)
        if isinstance(c, dict):
            # ใช้ตัวเลขพื้นฐานล่าสุด (มี growth/margin/PE/ROE/หนี้ครบ) — กระชับพอสำหรับเทียบหลายตัว
            # ไม่ใส่งบลึกราย period ของทุกตัว เพราะ context จะใหญ่เกิน TPM ของ LLM
            sec = (c.get("snapshot") or {}).get("sector") or "-"
            blocks.append(_context_block(c, business=False, financials=False, news=False)
                          + f"\nSector: {sec}")
            ok.append({"symbol": s, "name": c["name"], "price": c.get("price")})
    if len(ok) < 2:
        raise ValueError("ดึงข้อมูลได้ไม่ถึง 2 ตัว — ลองตรวจสัญลักษณ์หุ้นอีกครั้ง")

    user_msg = (_MODE_INTRO["compare"] + "\n\n=== ข้อมูลจริงของแต่ละหุ้น ===\n\n"
                + "\n\n----------\n\n".join(blocks))
    system = _BASE_RULES + "\n\n" + _CONTRACTS["compare"]
    data = await _run_llm(system, user_msg)
    data["symbols"] = ok
    data["auto_peers"] = auto_peers
    if auto_peers and len(ok) > 1:
        data["peers_found"] = [o["symbol"] for o in ok[1:]]
    return data
