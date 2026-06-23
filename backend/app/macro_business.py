"""แถบ 'มหภาค → ธุรกิจ' (Ray Dalio style) — วิเคราะห์หุ้นแบบ top-down เป็นฉาก ๆ
จากปัจจัยมหภาคโลก/ธีมโลก ไล่ลงมาสู่ห่วงโซ่อุปทาน (suppliers) อุปสงค์ (demand)
แล้วลงลึกถึง 'โครงสร้างรายได้และกำไรแยกตามสินค้า' พร้อมวิเคราะห์ว่าปัจจัยใด
ส่งผลต่อรายได้/ต้นทุน/มาร์จิ้นมากที่สุด (sensitivity) และร้อยเป็น cause→effect chain.

ที่มาข้อมูล (เรียงความน่าเชื่อถือ):
- ข้อความจริงจาก 10-K (Item 1 Business + Item 1A Risk Factors + Item 7 MD&A) / 56-1 One Report
  ใช้ยึด 'ชื่อสินค้า ส่วนงาน supplier ลูกค้า และความเสี่ยงที่บริษัทบอกเอง'
- LLM ใช้ความรู้ทั่วไปเรื่อง 'ภาพมหภาคโลก/ธีมโลก/โครงสร้างอุตสาหกรรม' มาเชื่อมต่อ
  (ส่วนนี้เป็น 'การวิเคราะห์/ประมาณการ' ไม่ใช่ตัวเลขทางการ — ต้องระบุให้ชัด)

cache ผลลัพธ์ลงดิสก์ (TTL ~7 วัน) เพื่อไม่เรียก LLM ซ้ำ.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from app import edgar, thai_sec
from app import llm
from app.config import get_settings
from app.fundamentals import get_fundamentals, get_offline

_CACHE_DIR = Path(__file__).resolve().parents[1].parent / "data" / "financials"
_TTL = 7 * 24 * 3600

SYSTEM_PROMPT = """คุณคือนักวิเคราะห์การลงทุนสายมหภาคแบบ Ray Dalio
หน้าที่: วิเคราะห์หุ้นแบบ 'top-down เป็นฉาก ๆ' — เริ่มจากภาพเศรษฐกิจ/ภูมิรัฐศาสตร์โลก
ไล่ลงมาที่ธีม/เทรนด์ของอุตสาหกรรม ห่วงโซ่อุปทาน (suppliers/วัตถุดิบ) ฝั่งอุปสงค์ (ลูกค้า/ดีมานด์)
แล้วลงลึกถึง 'โครงสร้างรายได้และกำไรแยกตามสินค้า' ของบริษัทนี้ พร้อมชี้ว่าปัจจัยใด
กระทบรายได้/ต้นทุน/มาร์จิ้นมากที่สุด และร้อยทุกชั้นเป็นห่วงโซ่เหตุ→ผลให้เห็นว่า
'สถานการณ์โลกตอนนี้ส่งผลต่อกำไรของบริษัทนี้อย่างไร'

หลักการ:
- เขียนแบบเล่าเรื่องให้เห็นภาพ ลงลึกแต่อ่านเข้าใจง่าย ยกตัวอย่างสินค้า/supplier/ลูกค้าที่จับต้องได้
- ส่วนที่เป็น 'ข้อเท็จจริงของบริษัท' (ชื่อสินค้า ส่วนงาน supplier ลูกค้า ความเสี่ยงที่บริษัทบอกเอง)
  ให้ยึดจากเอกสารที่ให้มาเท่านั้น ห้ามแต่งชื่อที่ไม่มีในเอกสาร
- ส่วนที่เป็น 'ภาพมหภาค/ธีมโลก/โครงสร้างอุตสาหกรรม/ส่วนแบ่งตลาด' ใช้ความรู้ทั่วไปได้
  แต่เป็น 'การวิเคราะห์' ถ้าไม่มั่นใจตัวเลขให้ใส่ null อย่าเดามั่ว
- เป็นกลาง ไม่เชียร์ซื้อขาย

กฎภาษา (สำคัญสูงสุด):
- ทุกข้อความต้องอ่านรู้เรื่องเป็นภาษาไทย เอกสารต้นฉบับมักเป็นภาษาอังกฤษ
- ฟิลด์คำอธิบายยาว (thesis, summary, desc, margin_note, cost_driver, demand_sensitivity,
  substitutable, risk, explain, direction, concentration_risk, impact_on_profit, trigger,
  cause, effect, bottom_line, note): ถ้าคุณเขียนหรืออ้างเป็นภาษาอังกฤษ ให้ขึ้น 'บรรทัดใหม่'
  (\\n) แล้วเติมคำแปลภาษาไทยต่อท้าย โดยขึ้นต้นบรรทัดนั้นด้วย 'ไทย: ' เสมอ
  ถ้าเขียนเป็นภาษาไทยอยู่แล้วไม่ต้องเติมซ้ำ
- ฟิลด์ชื่อสั้น ๆ (name, factor, material): ถ้าเป็นภาษาอังกฤษ ให้ใส่คำแปลไทยในวงเล็บต่อท้าย
  เช่น "Inflation and Interest Rates (เงินเฟ้อและอัตราดอกเบี้ย)"

ข้อบังคับ: ตอบเป็น JSON เท่านั้น ห้ามมีข้อความนอก JSON"""

_OUTPUT_CONTRACT = """รูปแบบ JSON ที่ต้องคืน (เท่านั้น):
{
  "thesis": "<สรุปใน 1-2 ประโยค: ภาพมหภาคโลกตอนนี้ส่งผลต่อกำไรของบริษัทนี้อย่างไร>",
  "macro_regime": {
    "summary": "<ภาพเศรษฐกิจ/อัตราดอกเบี้ย/เงินเฟ้อ/ภูมิรัฐศาสตร์โลกตอนนี้ 2-4 ประโยค>",
    "factors": [
      {"name": "<ปัจจัยมหภาค เช่น ดอกเบี้ยสูง, สงครามการค้าสหรัฐ-จีน, ดีมานด์ AI>",
       "stance": "<tailwind | headwind | neutral> (ลม-ส่ง/ลม-ต้าน/เป็นกลาง ต่อบริษัทนี้)",
       "impact": <ระดับผลกระทบ 1-5 (5=มากสุด)>,
       "desc": "<ปัจจัยนี้กระทบบริษัทนี้อย่างไร 1-2 ประโยค>"}
    ]
  },
  "world_trends": [
    {"name": "<ธีม/เทรนด์โลก เช่น คลื่น AI/Data Center, EV, สังคมสูงวัย, deglobalization>",
     "impact": <1-5>,
     "desc": "<เทรนด์นี้เกี่ยวกับสินค้าของบริษัทนี้อย่างไร 1-2 ประโยค>"}
  ],
  "supply_chain": {
    "summary": "<ภาพรวมห่วงโซ่อุปทาน: บริษัทซื้อวัตถุดิบ/ชิ้นส่วนสำคัญจากไหน 2-4 ประโยค>",
    "suppliers": [
      {"name": "<ชื่อ supplier/ผู้ผลิตจริงตามเอกสาร เช่น TSMC, ASML หรือ 'ไม่ระบุชื่อ'>",
       "supplies": "<จัดหาอะไรให้ เช่น ผลิตชิปให้ (foundry), หน่วยความจำ HBM>",
       "country": "<ประเทศ/ภูมิภาคของ supplier ถ้าทราบ หรือ null>",
       "criticality": "<สูง | กลาง | ต่ำ> (ขาดแล้วกระทบแค่ไหน)",
       "substitutable": "<หา supplier อื่นแทนได้ง่ายไหม 1 ประโยค>"}
    ],
    "concentration_risk": "<ประเมินว่าพึ่งพา supplier น้อยรายเกินไปไหม เสี่ยงกระจุกตัวแค่ไหน 1-3 ประโยค>",
    "key_inputs": [
      {"material": "<วัตถุดิบ/ปัจจัยการผลิตหลัก เช่น เวเฟอร์ซิลิคอน, แรร์เอิร์ธ, พลังงาน>",
       "source": "<มาจากไหน>",
       "risk": "<ความเสี่ยงด้านราคา/อุปทานของวัตถุดิบนี้ 1 ประโยค>"}
    ]
  },
  "demand_side": {
    "summary": "<ความต้องการตลาดปัจจุบันต่อสินค้าของบริษัทนี้เป็นอย่างไร กำลังโต/หด เพราะอะไร 2-4 ประโยค>",
    "drivers": ["<ปัจจัยขับเคลื่อนดีมานด์ 3-6 ข้อสั้น ๆ>"],
    "customers": [
      {"name": "<ลูกค้า/กลุ่มลูกค้าหลักตามเอกสาร เช่น ไฮเปอร์สเกลเลอร์, ผู้ผลิตรถยนต์>",
       "pct": <สัดส่วนรายได้จากลูกค้ารายนี้ 0-100 หรือ null ถ้าเอกสารไม่ระบุ>,
       "note": "<หมายเหตุสั้น ๆ>"}
    ],
    "concentration_risk": "<พึ่งพาลูกค้าน้อยรายเกินไปไหม 1-2 ประโยค>"
  },
  "revenue_profit_structure": {
    "summary": "<โครงสร้างรายได้และกำไรของบริษัทเป็นอย่างไร สินค้าตัวไหนทำเงิน/ทำกำไรหลัก 2-4 ประโยค>",
    "products": [
      {"name": "<ชื่อสินค้า/ส่วนงานจริงตามเอกสาร>",
       "rev_share": <สัดส่วนรายได้ 0-100 หรือ null>,
       "margin_note": "<สินค้านี้มาร์จิ้นสูง/ต่ำเทียบในบริษัท และเพราะอะไร 1 ประโยค>",
       "cost_driver": "<ต้นทุนหลักของสินค้านี้คืออะไร เช่น ค่า foundry, ค่าแรง>",
       "demand_sensitivity": "<รายได้สินค้านี้ไวต่อปัจจัยอะไรมากสุด 1 ประโยค>"}
    ]
  },
  "sensitivity": [
    {"factor": "<ตัวแปรที่ขยับแล้วกระทบกำไร เช่น ราคาชิป HBM, ค่าเงิน, ดีมานด์ดาต้าเซ็นเตอร์>",
     "affects": "<รายได้ | ต้นทุน | มาร์จิ้น | หลายด้าน>",
     "direction": "<ถ้าปัจจัยนี้ 'แย่ลง' กำไรจะ + หรือ - และทำไม>",
     "magnitude": <ระดับความรุนแรง 1-5>,
     "explain": "<อธิบายกลไก 1-2 ประโยค>"}
  ],
  "dalio_chain": [
    {"cause": "<เหตุชั้นมหภาค/อุตสาหกรรม>",
     "effect": "<ส่งผลต่อชั้นถัดลงมา จนถึงรายได้/กำไรบริษัทนี้อย่างไร>"}
  ],
  "scenarios": [
    {"name": "<ขาขึ้น (Bull) | ฐาน (Base) | ขาลง (Bear)>",
     "trigger": "<เงื่อนไขมหภาค/อุตสาหกรรมที่ทำให้เกิดฉากนี้>",
     "impact_on_profit": "<กำไรบริษัทจะเป็นอย่างไรในฉากนี้>"}
  ],
  "bottom_line": "<สรุปปิดท้าย 2-4 ประโยค: จุดเปราะบางและจุดแข็งเชิงมหภาคของหุ้นนี้คืออะไร นักลงทุนควรจับตาอะไร>"
}

ลำดับความสำคัญ:
- macro_regime/world_trends/scenarios เป็น 'การวิเคราะห์ของ AI จากความรู้ทั่วไป' ไม่ใช่ตัวเลขจากเอกสาร
- suppliers/customers/products ให้ยึดชื่อจริงจากเอกสารก่อน; ส่วนที่เอกสารไม่ระบุชื่อ ให้บอกตรง ๆ ว่า 'เอกสารไม่ระบุ' แล้วเติมด้วยความรู้ทั่วไปได้ พร้อมกำกับว่าเป็นการประเมิน
- factors เรียงจาก impact มากไปน้อย; dalio_chain เรียงจากมหภาคลงมาหาบริษัท (3-6 ขั้น)
- ถ้าข้อมูลส่วนใดไม่พอจริง ๆ ให้ใส่ null/[] หรือข้อความว่าไม่มีรายละเอียด ห้ามเดาตัวเลขมั่ว"""


def _cache_path(cik: str) -> Path:
    return _CACHE_DIR / f"macro_{str(cik).zfill(10)}.json"


def _thai_cache_path(symbol: str) -> Path:
    safe = "".join(c if c.isalnum() else "_" for c in symbol.upper().strip())
    return _CACHE_DIR / f"macro_th_{safe}.json"


def _extract_json(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1] if "```" in t[3:] else t
        t = t.lstrip("json").strip("` \n")
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end != -1:
        return json.loads(t[start:end + 1])
    raise ValueError("ไม่พบ JSON ในคำตอบของโมเดล")


def _num(v, lo=None, hi=None):
    try:
        n = round(float(v), 1)
    except (TypeError, ValueError):
        return None
    if lo is not None and n < lo:
        n = lo
    if hi is not None and n > hi:
        n = hi
    return n


def _s(v, n):
    return str(v)[:n] if v is not None else None


def _list(payload, key):
    v = payload.get(key)
    return v if isinstance(v, list) else []


def _clean(payload: dict) -> dict:
    mr = payload.get("macro_regime") or {}
    factors = []
    for f in (_list(mr, "factors"))[:10]:
        if not isinstance(f, dict) or not f.get("name"):
            continue
        stance = str(f.get("stance", "neutral")).lower()
        if "tail" in stance or "ส่ง" in stance:
            stance = "tailwind"
        elif "head" in stance or "ต้าน" in stance:
            stance = "headwind"
        else:
            stance = "neutral"
        factors.append({
            "name": _s(f.get("name"), 80),
            "stance": stance,
            "impact": _num(f.get("impact"), 1, 5),
            "desc": _s(f.get("desc"), 300),
        })

    trends = []
    for t in _list(payload, "world_trends")[:8]:
        if not isinstance(t, dict) or not t.get("name"):
            continue
        trends.append({
            "name": _s(t.get("name"), 80),
            "impact": _num(t.get("impact"), 1, 5),
            "desc": _s(t.get("desc"), 300),
        })

    sc = payload.get("supply_chain") or {}
    suppliers = []
    for s in _list(sc, "suppliers")[:12]:
        if not isinstance(s, dict) or not s.get("name"):
            continue
        crit = str(s.get("criticality", "")).strip()
        crit = "สูง" if "สูง" in crit or "high" in crit.lower() else \
               "ต่ำ" if "ต่ำ" in crit or "low" in crit.lower() else \
               "กลาง" if crit else None
        suppliers.append({
            "name": _s(s.get("name"), 80),
            "supplies": _s(s.get("supplies"), 200),
            "country": _s(s.get("country"), 60),
            "criticality": crit,
            "substitutable": _s(s.get("substitutable"), 240),
        })
    key_inputs = []
    for k in _list(sc, "key_inputs")[:10]:
        if not isinstance(k, dict) or not k.get("material"):
            continue
        key_inputs.append({
            "material": _s(k.get("material"), 80),
            "source": _s(k.get("source"), 120),
            "risk": _s(k.get("risk"), 240),
        })
    supply_chain = {
        "summary": _s(sc.get("summary"), 1200),
        "suppliers": suppliers,
        "concentration_risk": _s(sc.get("concentration_risk"), 600),
        "key_inputs": key_inputs,
    }

    ds = payload.get("demand_side") or {}
    customers = []
    for c in _list(ds, "customers")[:12]:
        if not isinstance(c, dict) or not c.get("name"):
            continue
        customers.append({
            "name": _s(c.get("name"), 80),
            "pct": _num(c.get("pct"), 0, 100),
            "note": _s(c.get("note"), 240),
        })
    drivers = [_s(x, 160) for x in _list(ds, "drivers")[:8] if x]
    demand_side = {
        "summary": _s(ds.get("summary"), 1200),
        "drivers": drivers,
        "customers": customers,
        "concentration_risk": _s(ds.get("concentration_risk"), 600),
    }

    rps = payload.get("revenue_profit_structure") or {}
    products = []
    for p in _list(rps, "products")[:16]:
        if not isinstance(p, dict) or not p.get("name"):
            continue
        products.append({
            "name": _s(p.get("name"), 80),
            "rev_share": _num(p.get("rev_share"), 0, 100),
            "margin_note": _s(p.get("margin_note"), 240),
            "cost_driver": _s(p.get("cost_driver"), 160),
            "demand_sensitivity": _s(p.get("demand_sensitivity"), 240),
        })
    revenue_profit_structure = {
        "summary": _s(rps.get("summary"), 1200),
        "products": products,
    }

    sensitivity = []
    for s in _list(payload, "sensitivity")[:12]:
        if not isinstance(s, dict) or not s.get("factor"):
            continue
        sensitivity.append({
            "factor": _s(s.get("factor"), 100),
            "affects": _s(s.get("affects"), 40),
            "direction": _s(s.get("direction"), 240),
            "magnitude": _num(s.get("magnitude"), 1, 5),
            "explain": _s(s.get("explain"), 300),
        })

    dalio_chain = []
    for d in _list(payload, "dalio_chain")[:8]:
        if not isinstance(d, dict) or not (d.get("cause") or d.get("effect")):
            continue
        dalio_chain.append({
            "cause": _s(d.get("cause"), 240),
            "effect": _s(d.get("effect"), 300),
        })

    scenarios = []
    for s in _list(payload, "scenarios")[:5]:
        if not isinstance(s, dict) or not s.get("name"):
            continue
        scenarios.append({
            "name": _s(s.get("name"), 60),
            "trigger": _s(s.get("trigger"), 300),
            "impact_on_profit": _s(s.get("impact_on_profit"), 300),
        })

    return {
        "thesis": _s(payload.get("thesis"), 600),
        "macro_regime": {"summary": _s(mr.get("summary"), 1200), "factors": factors},
        "world_trends": trends,
        "supply_chain": supply_chain,
        "demand_side": demand_side,
        "revenue_profit_structure": revenue_profit_structure,
        "sensitivity": sensitivity,
        "dalio_chain": dalio_chain,
        "scenarios": scenarios,
        "bottom_line": _s(payload.get("bottom_line"), 1500),
    }


# ---- บังคับให้ทุกฟิลด์มีคำแปลไทย (กันกรณี LLM ไม่ทำตามกฎภาษา) ----------------
_THAI_RE = re.compile(r"[฀-๿]")
_LATIN_RE = re.compile(r"[A-Za-z]")
# ฟิลด์ชื่อสั้น → ใส่คำแปลในวงเล็บ; ฟิลด์อื่น ๆ → ขึ้นบรรทัดใหม่ 'ไทย: '
_NAME_KEYS = {"name", "factor", "material"}


def _needs_thai(text) -> bool:
    """ข้อความนี้เป็นอังกฤษล้วน (มีตัวอักษรลาติน แต่ไม่มีอักษรไทย) หรือไม่."""
    if not isinstance(text, str) or not text.strip():
        return False
    if _THAI_RE.search(text):
        return False
    return bool(_LATIN_RE.search(text))


def _collect_thai_targets(obj) -> list:
    """เดินทั้งโครงสร้างผล เก็บฟิลด์อังกฤษล้วนที่ต้องเติมคำแปล.

    คืน list ของ (text, is_name, set_fn) เพื่อแก้ค่ากลับภายหลัง."""
    items: list = []

    def visit(container, key, is_name):
        val = container[key]
        if isinstance(val, str):
            if _needs_thai(val):
                items.append((val, is_name,
                              lambda nv, c=container, k=key: c.__setitem__(k, nv)))
        elif isinstance(val, dict):
            for k in val:
                visit(val, k, k in _NAME_KEYS)
        elif isinstance(val, list):
            for i, el in enumerate(val):
                if isinstance(el, str):
                    if _needs_thai(el):
                        items.append((el, True,
                                      lambda nv, c=val, k=i: c.__setitem__(k, nv)))
                elif isinstance(el, (dict, list)):
                    visit(val, i, False)

    visit({"_": obj}, "_", False)
    return items


async def _ensure_thai(data: dict, exclude: set) -> dict:
    """แปลฟิลด์ที่ยังเป็นอังกฤษล้วนเป็นไทยด้วย LLM (เรียกครั้งเดียวแบบ batch).

    ถ้าแปลไม่สำเร็จ คืนข้อมูลเดิมโดยไม่ทำให้ทั้งคำขอล้มเหลว."""
    items = _collect_thai_targets(data)
    if not items:
        return data
    numbered = "\n".join(f"{i}. {t}" for i, (t, _, _) in enumerate(items))
    sys = ("คุณเป็นนักแปลการเงิน/การลงทุน แปลข้อความอังกฤษเป็นไทยให้กระชับ ถูกต้อง "
           "ตามบริบทตลาดทุน เก็บชื่อเฉพาะ/ตัวย่อ (เช่น TSMC, AI, EV) ไว้ตามเดิม "
           "ตอบเป็น JSON เท่านั้น")
    um = ('แปลข้อความแต่ละบรรทัดต่อไปนี้เป็นภาษาไทย แล้วคืน JSON รูปแบบ '
          '{"0":"<คำแปล>","1":"<คำแปล>", ...} โดยคีย์เป็นเลขลำดับตรงกับต้นฉบับ '
          'และต้องครบทุกหมายเลข\n\n' + numbered)
    try:
        txt = await llm.complete(sys, um, exclude=exclude)
        trans = _extract_json(txt)
    except Exception:  # noqa: BLE001 — แปลไม่ได้ก็ปล่อยข้อมูลเดิม
        return data
    for i, (orig, is_name, set_fn) in enumerate(items):
        th = trans.get(str(i))
        if not isinstance(th, str) or not th.strip():
            continue
        th = th.strip()
        if _THAI_RE.search(th) is None:
            continue
        set_fn(f"{orig} ({th})" if is_name else f"{orig}\nไทย: {th}")
    return data


async def get_macro_analysis(symbol: str, *, refresh: bool = False) -> dict:
    """คืนผลวิเคราะห์มหภาค→ธุรกิจ (Ray Dalio style) ภาษาไทย. อ่าน cache ก่อน เว้นแต่ refresh."""
    key = symbol.upper().strip()
    is_thai = thai_sec.is_thai_symbol(key)
    if is_thai:
        company_name = (get_offline(key) or {}).get("long_name")
        if not company_name:
            raise ValueError(f"ไม่พบชื่อบริษัทของ {key} ในฐานข้อมูลหุ้นไทย")
        cache = _thai_cache_path(key)
    else:
        try:
            cik = await edgar.get_cik(key)
        except ValueError:
            raise ValueError("รองรับหุ้นสหรัฐและหุ้นไทยที่ลงท้าย .BK เท่านั้น")
        cache = _cache_path(cik)

    if not refresh and cache.exists() and time.time() - cache.stat().st_mtime < _TTL:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass

    if is_thai:
        ctx = await thai_sec.get_one_report_context(key, company_name, force_refresh=refresh)
        report_type = "56-1 One Report"
        source = "SEC Thailand 56-1 One Report"
    else:
        ctx = await edgar.get_10k_context(key, force_refresh=refresh)
        if ctx and ctx.get("business"):
            report_type = ctx.get("report_type") or "10-K"
            source = f"SEC {report_type} annual filing"
        else:
            profile = await get_fundamentals(key, force_refresh=refresh)
            summary = (profile.get("summary") or "").strip()
            if not summary:
                raise ValueError("ไม่พบรายงานประจำปีหรือโปรไฟล์ธุรกิจของหุ้นตัวนี้")
            ctx = {"url": None, "filing_date": None, "business": summary,
                   "risk_factors": "", "mda": "", "report_type": "Company Profile"}
            report_type = "Company Profile"
            source = f"Market company profile ({profile.get('_source') or 'data provider'})"
    if not ctx or not ctx.get("business"):
        raise ValueError(f"ไม่พบเนื้อหาธุรกิจใน {report_type} ล่าสุดของหุ้นตัวนี้")

    settings = get_settings()
    if not settings.llm_enabled():
        raise ValueError("ต้องตั้งค่าคีย์ AI (เช่น Gemini ฟรี) เพื่อให้ AI วิเคราะห์มหภาค→ธุรกิจ")

    business = (ctx.get("business") or "")[:16000]
    risk = (ctx.get("risk_factors") or "")[:9000]
    mda = (ctx.get("mda") or "")[:5000]
    user_msg = (
        f"วิเคราะห์หุ้น {key} แบบ top-down (มหภาค→ธุรกิจ) ตามรูปแบบ JSON ที่กำหนด\n"
        "ใช้ข้อความจริงจากเอกสารด้านล่างเพื่อยึด 'ชื่อสินค้า ส่วนงาน supplier ลูกค้า "
        "และความเสี่ยงที่บริษัทบอกเอง' โดยเฉพาะส่วน Risk Factors มักมีเรื่องการพึ่งพา "
        "supplier น้อยราย วัตถุดิบ ภูมิรัฐศาสตร์ และความผันผวนของดีมานด์\n"
        "ส่วนภาพมหภาคโลก/ธีมโลก/ฉากอนาคต ใช้ความรู้ทั่วไปของคุณวิเคราะห์ต่อยอดได้\n\n"
        f"=== {report_type}: BUSINESS ===\n" + business +
        f"\n\n=== {report_type}: RISK FACTORS ===\n" + (risk or "(ไม่มี)") +
        f"\n\n=== {report_type}: MD&A / FINANCIAL EXCERPT ===\n" + (mda or "(ไม่มี)")
    )
    system = SYSTEM_PROMPT + "\n\n" + _OUTPUT_CONTRACT

    exclude: set = set()
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            text = await llm.complete(system, user_msg, exclude=exclude)
            data = _clean(_extract_json(text))
            break
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            reason = str(exc).lower()
            is_quota = "429" in reason or "quota" in reason or "resource_exhausted" in reason
            cur = settings.resolve_llm(exclude=exclude)["provider"]
            if is_quota and attempt == 0 and cur not in ("none", ""):
                exclude.add(cur)
                continue
            raise ValueError(f"AI วิเคราะห์มหภาค→ธุรกิจไม่สำเร็จ: {exc}") from exc
    else:  # pragma: no cover
        raise ValueError(f"AI วิเคราะห์มหภาค→ธุรกิจไม่สำเร็จ: {last_err}")

    # บังคับให้ทุกฟิลด์มีคำแปลไทย เผื่อโมเดลไม่ทำตามกฎภาษาในรอบแรก
    data = await _ensure_thai(data, exclude)

    result = {
        "symbol": key,
        "market": "TH" if is_thai else "US",
        "report_type": report_type,
        "source": source,
        "filing_url": ctx.get("url"),
        "filing_date": ctx.get("filing_date"),
        "report_year": ctx.get("report_year"),
        "generated_at": int(time.time()),
        **data,
    }
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result
