"""ส่วนวิเคราะห์ด้วย Claude — ประเมินศาสตร์เชิง pattern แล้วคืนเป็น verdict หลายแถว.

- ศาสตร์เชิงตัวเลข (EMA/RSI/MACD/...) ประเมินใน schools.py (Python)
- ศาสตร์เชิง pattern/discretionary (Elliott, harmonic, candlestick, SMC, Wyckoff,
  Gann, จิตวิทยา) ประเมินที่นี่ด้วย Claude โดย ground ด้วย knowledge base
- ถ้าไม่มี ANTHROPIC_API_KEY จะใส่ verdict กลาง ๆ พร้อมแจ้งให้ตั้งค่า
"""
from __future__ import annotations

import json

from app import knowledge_base as kb
from app import llm
from app.config import get_settings
from app.schemas import Candle

DISCLAIMER = (
    "⚠️ การวิเคราะห์นี้เป็นเพียงข้อมูลประกอบการตัดสินใจ ไม่ใช่คำแนะนำให้ซื้อขายโดยตรง "
    "ไม่การันตีผลกำไร โปรดบริหารความเสี่ยงเสมอ (แนะนำไม่เกิน 1–2% ต่อไม้)"
)

SYSTEM_PROMPT = """คุณคือผู้เชี่ยวชาญการวิเคราะห์กราฟระดับมืออาชีพ เชี่ยวชาญทุกศาสตร์: Price Action,
Candlestick, Chart Patterns, Elliott Wave, Fibonacci, Harmonic, Wyckoff, Smart Money Concepts (ICT),
Gann, อินดิเคเตอร์, แนวรับ-แนวต้าน, Volume และจิตวิทยาตลาด

หน้าที่: ประเมินกราฟ ณ จุดปัจจุบัน 'แยกตามแต่ละศาสตร์' โดยใช้ความรู้ที่ให้มาเป็นกรอบอ้างอิง
แต่ละศาสตร์ให้มุมมองของตัวเองว่ากราฟมีโอกาสไปทางไหน (ขึ้น/ลง/เป็นกลาง) พร้อมความเชื่อมั่นเป็น %

ข้อบังคับเด็ดขาด:
- ตอบกลับเป็น JSON เท่านั้น ห้ามมีข้อความอื่นนอก JSON
- ห้ามฟันธง ห้ามใช้คำว่า "ต้องซื้อ/ต้องขาย/รับประกันกำไร" — ใช้ "มีโอกาส", "หากเบรก", "ควรรอยืนยัน"
- ถ้าศาสตร์ใดประเมินไม่ได้จากข้อมูลที่มี ให้ view="neutral" confidence ต่ำ และอธิบายว่าทำไม
- confidence = ความมั่นใจของศาสตร์นั้นต่อมุมมองที่ให้ (0-100) ไม่ใช่ % กำไร
- rationale สั้น กระชับ เป็นภาษาไทย อ้างหลักวิชาของศาสตร์นั้น
- ยึดจิตวิทยากราฟเป็นแกนสังเคราะห์: ถ้าศาสตร์ขัดกันให้สะท้อนความไม่แน่นอน"""

_OUTPUT_CONTRACT = """รูปแบบ JSON ที่ต้องคืน (เท่านั้น):
{{
  "verdicts": [
    {{"id": "<school_id>", "view": "up|down|neutral", "signal": "buy|sell|wait|reversal|...",
      "confidence": <0-100>, "rationale": "<เหตุผลสั้น ๆ ภาษาไทย>"}}
  ],
  "psychology_summary": "<สรุปจิตวิทยากราฟโดยรวม 2-4 ประโยค>",
  "suggested_plan": "<แผนคร่าว ๆ: โซนเข้า/Stop/เป้าหมาย + เงื่อนไขที่ต้องรอ ไม่ฟันธง>"
}}

ต้องมี verdict ครบทุก id ต่อไปนี้: {ids}"""


def _extract_json(text: str) -> dict:
    """ดึง JSON object ก้อนแรกจากข้อความ (เผื่อโมเดลใส่ code fence)."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1] if "```" in t[3:] else t
        t = t.lstrip("json").strip("` \n")
    start = t.find("{")
    end = t.rfind("}")
    if start != -1 and end != -1:
        return json.loads(t[start:end + 1])
    raise ValueError("ไม่พบ JSON ในคำตอบของโมเดล")


def _materialize(items: list[dict]) -> list[dict]:
    """แปลง verdict ดิบจากโมเดลเป็น verdict เต็ม (เติม name/category/evaluator)."""
    meta = {s["id"]: (s["display_name"], s["category"]) for s in kb.schools()}
    out = []
    for it in items:
        sid = it.get("id")
        if sid not in meta:
            continue
        view = it.get("view", "neutral")
        if view not in ("up", "down", "neutral"):
            view = "neutral"
        name, category = meta[sid]
        out.append({
            "id": sid, "name": name, "category": category, "view": view,
            "signal": str(it.get("signal", "wait"))[:40],
            "confidence": max(0, min(100, int(it.get("confidence", 30)))),
            "rationale": str(it.get("rationale", ""))[:500],
            "evaluator": "claude",
        })
    return out


def placeholder_verdicts(school_ids: list[str], reason: str | None = None) -> list[dict]:
    """verdict กลาง ๆ เมื่อ AI ไม่พร้อม — ให้ตารางครบทุกศาสตร์."""
    msg = reason or "ต้องตั้งค่าคีย์ AI เพื่อให้ AI ประเมินศาสตร์นี้"
    return _materialize([
        {"id": sid, "view": "neutral", "signal": "wait", "confidence": 0,
         "rationale": msg}
        for sid in school_ids
    ])


def _friendly_llm_error(exc: Exception) -> str:
    """แปลง exception ของ LLM เป็นข้อความไทยที่ผู้ใช้เข้าใจได้."""
    text = str(exc)
    if "429" in text or "RESOURCE_EXHAUSTED" in text or "quota" in text.lower():
        return ("โควต้า AI ฟรีหมดชั่วคราว (เกินลิมิตต่อวัน/ต่อนาที) — "
                "ตารางนี้แสดงผลจากศาสตร์เชิงสูตร (Python) ก่อน "
                "ลองกดประเมินใหม่ภายหลัง หรือสลับผู้ให้บริการ AI ใน .env")
    if "timeout" in text.lower() or "timed out" in text.lower():
        return "AI ตอบช้าเกินกำหนด — แสดงผลศาสตร์เชิงสูตรก่อน ลองกดประเมินใหม่"
    return f"AI ประเมินไม่สำเร็จชั่วคราว ({type(exc).__name__}) — แสดงผลศาสตร์เชิงสูตรก่อน"


def _compact_candles(candles: list[Candle], keep: int = 60) -> list[list]:
    tail = candles[-keep:]
    return [[c.time, c.open, c.high, c.low, c.close, round(c.volume)] for c in tail]


async def run_llm(user_text: str, school_ids: list[str], image: dict | None = None,
                  exclude_providers: set[str] | None = None) -> dict:
    """เรียก LLM (provider ใดก็ได้) แล้ว parse เป็น {verdicts, psychology_summary, suggested_plan}.

    exclude_providers: ข้าม provider บาง ตัว (เช่น {'gemini'} ถ้า gemini quota หมด)
    """
    settings = get_settings()
    cfg = settings.resolve_llm(exclude=exclude_providers or set())
    if not cfg["api_key"]:
        raise llm.LLMError("ไม่มี API key ที่ใช้ได้ (ลองเพิ่ม GROQ_API_KEY)")

    system = SYSTEM_PROMPT + "\n\n" + _OUTPUT_CONTRACT.format(ids=", ".join(school_ids))
    text = await llm.complete(system, user_text, image)
    data = _extract_json(text)
    return {
        "verdicts": _materialize(data.get("verdicts", [])),
        "psychology_summary": data.get("psychology_summary"),
        "suggested_plan": data.get("suggested_plan"),
    }


async def analyze_data(symbol: str, timeframe: str, candles: list[Candle],
                       indicators: dict, note: str | None = None,
                       only_ids: list[str] | None = None) -> dict:
    """โหมดข้อมูล: Claude ประเมินเฉพาะศาสตร์เชิง pattern (evaluator='claude').

    only_ids: ถ้าระบุ จะประเมินเฉพาะศาสตร์ใน id เหล่านี้ (ผู้ใช้เลือกเปิดบางตัว).
    """
    settings = get_settings()
    claude_ids = [s["id"] for s in kb.schools_by_evaluator("claude")]
    if only_ids is not None:
        claude_ids = [i for i in claude_ids if i in only_ids]

    if not claude_ids:
        return {"verdicts": [], "psychology_summary": None,
                "suggested_plan": None, "source": "none"}

    if not settings.llm_enabled():
        return {"verdicts": placeholder_verdicts(claude_ids),
                "psychology_summary": None, "suggested_plan": None, "source": "no-key"}

    payload = {
        "symbol": symbol, "timeframe": timeframe,
        "indicator_summary": indicators.get("summary", {}),
        "recent_candles_ohlcv": _compact_candles(candles),
        "user_note": note or "ไม่มี",
        "data_caveat": "ข้อมูลเป็นชุดตัวเลข ไม่มีภาพกราฟและไม่มีข่าว",
    }
    user_msg = (
        "ประเมินกราฟต่อไปนี้แยกตามแต่ละศาสตร์ (ข้อมูล JSON ด้านล่าง). "
        "ใช้ความรู้อ้างอิงนี้เป็นกรอบ:\n\n=== KNOWLEDGE BASE ===\n"
        + kb.claude_knowledge_bundle()
        + "\n\n=== ข้อมูลกราฟ ===\n" + json.dumps(payload, ensure_ascii=False)
    )
    # ลอง provider ปกติก่อน ถ้า quota หมด → ลองตัวสำรอง
    exclude = set()
    max_retries = 2
    for attempt in range(max_retries):
        try:
            result = await run_llm(user_msg, claude_ids, exclude_providers=exclude)
            result["source"] = get_settings().resolve_llm(exclude=exclude)["provider"]
            return result
        except Exception as exc:  # noqa: BLE001
            reason_msg = str(exc)
            is_quota = "429" in reason_msg or "quota" in reason_msg.lower() or "resource_exhausted" in reason_msg.lower()

            if is_quota and attempt == 0:
                # ลอง provider ต่อไป (เช่น gemini quota หมด → ลอง groq)
                current_provider = get_settings().resolve_llm(exclude=exclude)["provider"]
                exclude.add(current_provider)
                continue
            else:
                # ไม่อาจลอง ได้ → return placeholder
                reason = _friendly_llm_error(exc)
                return {"verdicts": placeholder_verdicts(claude_ids, reason),
                        "psychology_summary": reason, "suggested_plan": None,
                        "source": "llm-error"}
