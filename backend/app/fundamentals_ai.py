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
                                  doc_context: dict | None = None,  # 10-K Risk Factors/MD&A (ถ้ามี)
                                  only_ids: list[str] | None = None) -> dict:
    """ประเมินด้านเชิงคุณภาพสาย VI ตามลำดับความสด:
    (1) LLM สด (Gemini→ตก Groq เมื่อ quota หมด) → (2) คำวิเคราะห์ AI ที่ cache ไว้ใน snapshot
    → (3) เดาเชิงกฎจากตัวเลข. คืน ai_status บอกที่มา.
    """
    settings = get_settings()
    claude_ids = [s["id"] for s in kb.value_schools_by_evaluator("claude")]
    if only_ids is not None:
        claude_ids = [i for i in claude_ids if i in only_ids]
    if not claude_ids:
        return {"verdicts": [], "summary": None, "ai_status": "none"}

    # 1) LLM สด พร้อม fallback Gemini → Groq
    if settings.llm_enabled():
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
        if doc_context:
            if doc_context.get("risk_factors"):
                payload["10k_risk_factors_excerpt"] = doc_context["risk_factors"][:3500]
            if doc_context.get("mda"):
                payload["10k_mda_excerpt"] = doc_context["mda"][:3500]
        user_msg = (
            "ประเมินด้านเชิงคุณภาพของกิจการนี้แยกตามแต่ละด้าน. ใช้ความรู้อ้างอิงเป็นกรอบ "
            "และถ้ามีข้อความจาก 10-K จริง (risk_factors/mda) ให้ใช้ประกอบการประเมินความเสี่ยง/คุณภาพ:\n\n"
            "=== KNOWLEDGE BASE ===\n" + kb.value_knowledge_bundle()
            + "\n\n=== ข้อมูลกิจการ ===\n" + json.dumps(payload, ensure_ascii=False)
        )
        system = SYSTEM_PROMPT + "\n\n" + _OUTPUT_CONTRACT.format(ids=", ".join(claude_ids))
        exclude: set = set()
        for attempt in range(2):
            try:
                text = await llm.complete(system, user_msg, exclude=exclude)
                verdicts = _materialize(_extract_json(text).get("verdicts", []), claude_ids)
                if verdicts:
                    grounded = bool(doc_context and (doc_context.get("risk_factors") or doc_context.get("mda")))
                    return {"verdicts": verdicts, "summary": _safe_summary(text),
                            "ai_status": "live", "grounded_10k": grounded}
                break
            except Exception as exc:  # noqa: BLE001
                reason = str(exc).lower()
                is_quota = "429" in reason or "quota" in reason or "resource_exhausted" in reason
                cur = settings.resolve_llm(exclude=exclude)["provider"]
                if is_quota and attempt == 0 and cur not in ("none", ""):
                    exclude.add(cur)  # ตกไปลอง provider ถัดไป (groq)
                    continue
                break

    # 2) คำวิเคราะห์ AI ที่ cache ไว้ (จาก snapshot ออฟไลน์)
    cached = snapshot.get("ai_qualitative")
    if cached and cached.get("verdicts"):
        vs = [x for x in cached["verdicts"] if x.get("id") in claude_ids]
        if vs:
            return {"verdicts": vs, "summary": cached.get("summary"),
                    "ai_status": "cached", "ai_as_of": cached.get("at")}

    # 3) เดาเชิงกฎจากตัวเลข
    return {"verdicts": _rule_based_qualitative(snapshot, claude_ids),
            "summary": None, "ai_status": "rule"}


def _safe_summary(text: str) -> str | None:
    try:
        return _extract_json(text).get("summary")
    except Exception:  # noqa: BLE001
        return None


def _rule_based_qualitative(snapshot: dict, ids: list[str]) -> list[dict]:
    """เดาด้านเชิงคุณภาพคร่าว ๆ จากตัวเลข เมื่อ AI ไม่พร้อม (ดีกว่าขึ้น error เปล่า)."""
    meta = {s["id"]: (s["display_name"], s["category"]) for s in kb.value_schools()}
    roe, gm, om = snapshot.get("roe"), snapshot.get("gross_margin"), snapshot.get("operating_margin")
    de, rg = snapshot.get("debt_to_equity"), snapshot.get("revenue_growth")
    tail = " — ประเมินจากตัวเลข (AI ไม่พร้อม)"

    def v(sid, view, sig, conf, why):
        name, cat = meta.get(sid, (sid, "qualitative"))
        return {"id": sid, "name": name, "category": cat, "view": view, "signal": sig,
                "confidence": conf, "metric_value": None, "rationale": why + tail, "evaluator": "python"}

    out: dict[str, dict] = {}
    if roe is not None and gm is not None:
        if roe >= 0.20 and gm >= 0.40:
            out["moat"] = v("moat", "good", "strong", 55,
                            f"ROE {roe*100:.0f}% + อัตรากำไรขั้นต้น {gm*100:.0f}% สูง บ่งชี้ความได้เปรียบ/อำนาจตั้งราคา")
        elif roe < 0.08:
            out["moat"] = v("moat", "poor", "weak", 45, f"ROE ต่ำ ({roe*100:.0f}%) อาจสะท้อนการแข่งขันสูง/ไม่มี moat ชัด")
        else:
            out["moat"] = v("moat", "fair", "unclear", 40, "ตัวเลขกลาง ๆ ยังสรุปความได้เปรียบไม่ชัด")
    else:
        out["moat"] = v("moat", "fair", "no-data", 20, "ข้อมูลไม่พอประเมิน moat")

    if om is not None:
        if om >= 0.12 and (rg is None or rg >= 0):
            out["business_quality"] = v("business_quality", "good", "stable", 52,
                                        "อัตรากำไรดำเนินงานดีและรายได้ไม่หดตัว — ธุรกิจค่อนข้างมั่นคง")
        elif om < 0:
            out["business_quality"] = v("business_quality", "poor", "lossmaking", 50,
                                        "ขาดทุนจากการดำเนินงาน — คุณภาพธุรกิจน่ากังวล")
        else:
            out["business_quality"] = v("business_quality", "fair", "moderate", 40, "คุณภาพการทำกำไรปานกลาง")
    else:
        out["business_quality"] = v("business_quality", "fair", "no-data", 20, "ข้อมูลไม่พอ")

    if roe is not None:
        if roe >= 0.15 and (de is None or de < 1.5):
            out["management"] = v("management", "good", "disciplined", 48, "ใช้ทุนได้ผลตอบแทนดีและก่อหนี้พอเหมาะ")
        elif de is not None and de > 3:
            out["management"] = v("management", "poor", "over-levered", 42, "ก่อหนี้สูงมาก — ต้องระวังการจัดสรรทุน")
        else:
            out["management"] = v("management", "fair", "moderate", 38, "จัดสรรทุนระดับกลาง")
    else:
        out["management"] = v("management", "fair", "no-data", 20, "ข้อมูลไม่พอ")

    return [out[i] for i in ids if i in out]
