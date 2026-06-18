"""สาย VI เชิงคุณภาพด้วย Claude — ประเมินสิ่งที่ตัวเลขไม่บอก (moat/ผู้บริหาร/คุณภาพธุรกิจ).

- ด้านเชิงตัวเลข (มูลค่า/กำไร/หนี้/...) ประเมินใน value_schools.py (Python)
- ด้านเชิงคุณภาพ ประเมินที่นี่ด้วย Claude โดย ground ด้วย knowledge/value/qualitative_principles.json
- ถ้าไม่มีคีย์ AI จะใส่ verdict กลาง ๆ (fair) พร้อมแจ้งให้ตั้งค่า
"""
from __future__ import annotations

import json

from app import knowledge_base as kb
from app import llm
from app.config import get_settings

SYSTEM_PROMPT = """คุณคือนักลงทุนเน้นคุณค่า (value investor) สไตล์ Buffett/Munger ที่ประเมินธุรกิจระยะยาว
อย่างถ่อมตัวและตรงไปตรงมา ไม่เชียร์ ไม่ฟันธงซื้อขาย

หน้าที่: ประเมิน 'ด้านเชิงคุณภาพ' ของกิจการ (สิ่งที่งบการเงินไม่บอกตรง ๆ) แยกตามแต่ละด้าน
โดยใช้คำอธิบายธุรกิจ + ตัวเลขพื้นฐานที่ให้มา และความรู้อ้างอิงเป็นกรอบ

ข้อบังคับเด็ดขาด:
- ตอบกลับเป็น JSON เท่านั้น ห้ามมีข้อความอื่นนอก JSON
- view = "good" (แข็งแรง/น่าสนใจ) / "fair" (กลาง/ไม่ชัด) / "poor" (อ่อน/น่ากังวล)
- ถ้าข้อมูลไม่พอประเมินด้านใด ให้ view="fair" confidence ต่ำ และบอกว่าทำไม
- ห้ามใช้คำว่า "ต้องซื้อ/ต้องขาย/รับประกัน" — ใช้ "มีแนวโน้ม", "ดูเหมือน", "ควรตรวจเพิ่ม"
- rationale สั้น กระชับ ภาษาไทย อ้างหลักการลงทุนเน้นคุณค่า
- confidence = ความมั่นใจต่อมุมมองนั้น (0-100) ไม่ใช่ % ผลตอบแทน"""

_OUTPUT_CONTRACT = """รูปแบบ JSON ที่ต้องคืน (เท่านั้น):
{{
  "verdicts": [
    {{"id": "<dimension_id>", "view": "good|fair|poor", "signal": "<คำสั้น>",
      "confidence": <0-100>, "rationale": "<เหตุผลสั้น ภาษาไทย>"}}
  ],
  "summary": "<สรุปคุณภาพธุรกิจโดยรวม 2-4 ประโยค สำหรับนักลงทุนระยะยาว>"
}}

ต้องมี verdict ครบทุก id ต่อไปนี้: {ids}"""


def _extract_json(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1] if "```" in t[3:] else t
        t = t.lstrip("json").strip("` \n")
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end != -1:
        return json.loads(t[start:end + 1])
    raise ValueError("ไม่พบ JSON ในคำตอบของโมเดล")


def _materialize(items: list[dict], allowed_ids: list[str]) -> list[dict]:
    meta = {s["id"]: (s["display_name"], s["category"]) for s in kb.value_schools()}
    allowed = set(allowed_ids)
    out = []
    for it in items:
        sid = it.get("id")
        if sid not in meta or sid not in allowed:
            continue
        view = it.get("view", "fair")
        if view not in ("good", "fair", "poor"):
            view = "fair"
        name, category = meta[sid]
        out.append({
            "id": sid, "name": name, "category": category, "view": view,
            "signal": str(it.get("signal", "—"))[:40],
            "confidence": max(0, min(100, int(it.get("confidence", 30)))),
            "metric_value": None,
            "rationale": str(it.get("rationale", ""))[:500],
            "evaluator": "claude",
        })
    return out


def placeholder_verdicts(ids: list[str], reason: str | None = None) -> list[dict]:
    msg = reason or "ต้องตั้งค่าคีย์ AI เพื่อให้ AI ประเมินด้านเชิงคุณภาพนี้"
    return _materialize(
        [{"id": sid, "view": "fair", "signal": "no-ai", "confidence": 0, "rationale": msg}
         for sid in ids], ids)


async def analyze_fundamentals_ai(symbol: str, snapshot: dict, note: str | None = None,
                                  only_ids: list[str] | None = None) -> dict:
    """Claude ประเมินด้านเชิงคุณภาพ (evaluator='claude') ของสาย VI."""
    settings = get_settings()
    claude_ids = [s["id"] for s in kb.value_schools_by_evaluator("claude")]
    if only_ids is not None:
        claude_ids = [i for i in claude_ids if i in only_ids]
    if not claude_ids:
        return {"verdicts": [], "summary": None}

    if not settings.llm_enabled():
        return {"verdicts": placeholder_verdicts(claude_ids), "summary": None}

    payload = {
        "symbol": symbol,
        "company": snapshot.get("long_name"),
        "sector": snapshot.get("sector"),
        "industry": snapshot.get("industry"),
        "business_summary": snapshot.get("summary") or "ไม่มีคำอธิบายธุรกิจ",
        "fundamentals": {k: snapshot.get(k) for k in (
            "pe", "peg", "roe", "gross_margin", "operating_margin", "debt_to_equity",
            "revenue_growth", "earnings_growth", "fcf_yield", "market_cap")},
        "user_note": note or "ไม่มี",
    }
    user_msg = (
        "ประเมินด้านเชิงคุณภาพของกิจการนี้แยกตามแต่ละด้าน. ใช้ความรู้อ้างอิงเป็นกรอบ:\n\n"
        "=== KNOWLEDGE BASE ===\n" + kb.value_knowledge_bundle()
        + "\n\n=== ข้อมูลกิจการ ===\n" + json.dumps(payload, ensure_ascii=False)
    )
    system = SYSTEM_PROMPT + "\n\n" + _OUTPUT_CONTRACT.format(ids=", ".join(claude_ids))
    try:
        text = await llm.complete(system, user_msg)
        data = _extract_json(text)
        return {"verdicts": _materialize(data.get("verdicts", []), claude_ids),
                "summary": data.get("summary")}
    except Exception as exc:  # noqa: BLE001
        reason = str(exc)
        if "429" in reason or "quota" in reason.lower() or "resource_exhausted" in reason.lower():
            msg = "โควต้า AI ฟรีหมดชั่วคราว — แสดงผลด้านเชิงตัวเลขก่อน ลองใหม่ภายหลัง"
        else:
            msg = f"AI ประเมินด้านคุณภาพไม่สำเร็จชั่วคราว ({type(exc).__name__})"
        return {"verdicts": placeholder_verdicts(claude_ids, msg), "summary": msg}
