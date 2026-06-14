"""วิเคราะห์ภาพ screenshot กราฟด้วย Claude vision.

โหมดภาพ: ไม่มีข้อมูลตัวเลขให้คำนวณ Python ได้ จึงให้ Claude อ่านภาพและประเมิน
'ทุกศาสตร์' (ทั้งเชิง pattern และเชิงอินดิเคเตอร์ที่มองเห็นในภาพ) แล้วคืน verdict ครบทุกแถว.
"""
from __future__ import annotations

import json

from app import knowledge_base as kb
from app.analysis import _friendly_llm_error, placeholder_verdicts, run_llm
from app.config import get_settings


async def analyze_image(image_b64: str, media_type: str,
                        symbol: str | None = None, timeframe: str | None = None,
                        note: str | None = None) -> dict:
    """ประเมินกราฟจากภาพ — LLM (ที่รองรับ vision) ประเมินทุกศาสตร์ใน registry."""
    settings = get_settings()
    all_ids = [s["id"] for s in kb.schools()]
    cfg = settings.resolve_llm()

    if not cfg["api_key"]:
        return {"verdicts": placeholder_verdicts(all_ids),
                "psychology_summary": None, "suggested_plan": None, "source": "no-key"}
    if not cfg["vision"]:
        return {"verdicts": placeholder_verdicts(all_ids),
                "psychology_summary": (f"ผู้ให้บริการ AI ปัจจุบัน ({cfg['provider']}) "
                                       "ไม่รองรับการอ่านภาพ — ตั้งค่า GEMINI_API_KEY (ฟรี) "
                                       "หรือใช้ Anthropic/OpenAI เพื่อใช้โหมดอัปโหลดภาพ"),
                "suggested_plan": None, "source": f"{cfg['provider']}-no-vision"}

    context = {
        "symbol": symbol or "ไม่ระบุ",
        "timeframe": timeframe or "ไม่ระบุ",
        "user_note": note or "ไม่มี",
    }
    instruction = (
        "นี่คือภาพ screenshot กราฟที่ผู้ใช้กำลังดูอยู่ ณ ปัจจุบัน "
        "อ่านภาพอย่างละเอียด (แท่งเทียน เทรนด์ แนวรับ-ต้าน รูปแบบ อินดิเคเตอร์ที่ปรากฏ volume) "
        "แล้วประเมินแยกตามแต่ละศาสตร์ ใช้ความรู้อ้างอิงนี้เป็นกรอบ:\n\n=== KNOWLEDGE BASE ===\n"
        + kb.claude_knowledge_bundle()
        + "\n\n=== บริบทจากผู้ใช้ ===\n" + json.dumps(context, ensure_ascii=False)
        + "\n\nถ้าศาสตร์ใดมองไม่เห็นหลักฐานในภาพ ให้ view=neutral confidence ต่ำ พร้อมบอกว่าต้องการอะไรเพิ่ม"
    )
    try:
        result = await run_llm(instruction, all_ids,
                               image={"b64": image_b64, "media_type": media_type})
    except Exception as exc:  # noqa: BLE001 — AI ล่ม/โควต้าหมด ไม่ทำให้คำขอพัง
        reason = _friendly_llm_error(exc)
        return {"verdicts": placeholder_verdicts(all_ids, reason),
                "psychology_summary": reason, "suggested_plan": None,
                "source": "llm-error"}
    result["source"] = f"{cfg['provider']}-vision"
    return result
