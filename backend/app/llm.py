"""ชั้นกลางเรียก LLM — รองรับหลายผู้ให้บริการผ่านการตั้งค่าเดียว.

- anthropic: ใช้ Anthropic SDK (claude-*)
- gemini / groq / openai: ใช้ OpenAI SDK ผ่าน OpenAI-compatible endpoint
  (Gemini และ Groq เปิด endpoint แบบ OpenAI ให้ใช้ฟรี)

ทุก provider รับ system + ข้อความ (+ ภาพ base64 ถ้ามี) แล้วคืนข้อความดิบ
ให้ผู้เรียก (analysis.py) ไป parse JSON เอง.
"""
from __future__ import annotations

from app.config import get_settings


class LLMError(Exception):
    pass


async def _call_anthropic(cfg: dict, system: str, user_text: str,
                          image: dict | None) -> str:
    import anthropic
    client = anthropic.AsyncAnthropic(api_key=cfg["api_key"])
    content: list = []
    if image:
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": image["media_type"], "data": image["b64"]}})
    content.append({"type": "text", "text": user_text})
    resp = await client.messages.create(
        model=cfg["model"], max_tokens=6000,
        thinking={"type": "adaptive"},
        system=system,
        messages=[{"role": "user", "content": content}],
    )
    return "".join(b.text for b in resp.content if b.type == "text")


async def _call_openai_compatible(cfg: dict, system: str, user_text: str,
                                  image: dict | None) -> str:
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])
    user_content: list = [{"type": "text", "text": user_text}]
    if image:
        user_content.insert(0, {"type": "image_url", "image_url": {
            "url": f"data:{image['media_type']};base64,{image['b64']}"}})
    kwargs = dict(
        model=cfg["model"], max_tokens=6000,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user_content}],
    )
    # บังคับ JSON ถ้า provider รองรับ (Groq/OpenAI/Gemini-compat ส่วนใหญ่รองรับ)
    try:
        resp = await client.chat.completions.create(
            response_format={"type": "json_object"}, **kwargs)
    except Exception:
        resp = await client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content or ""


async def complete(system: str, user_text: str, image: dict | None = None) -> str:
    """เรียก LLM ที่ resolve ได้. image = {'b64':..., 'media_type':...} หรือ None."""
    cfg = get_settings().resolve_llm()
    if not cfg["api_key"]:
        raise LLMError("ยังไม่ได้ตั้งคีย์ AI ใด ๆ")
    if image and not cfg["vision"]:
        raise LLMError(f"ผู้ให้บริการ '{cfg['provider']}' ไม่รองรับการอ่านภาพ "
                       "— ใช้ Gemini / OpenAI / Anthropic สำหรับโหมดภาพ")
    if cfg["kind"] == "anthropic":
        return await _call_anthropic(cfg, system, user_text, image)
    return await _call_openai_compatible(cfg, system, user_text, image)
