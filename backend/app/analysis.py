"""ส่วนวิเคราะห์ด้วย AI — เชื่อม Claude (Anthropic SDK) ตาม persona นักวิเคราะห์.

ถ้าไม่มี ANTHROPIC_API_KEY จะใช้สรุปแบบกฎ (rule-based) แทน พร้อมแจ้งให้ตั้งค่า.
"""
from __future__ import annotations

import json

from app.config import get_settings
from app.schemas import Candle

DISCLAIMER = (
    "⚠️ การวิเคราะห์นี้เป็นเพียงข้อมูลประกอบการตัดสินใจ ไม่ใช่คำแนะนำให้ซื้อขายโดยตรง "
    "ไม่การันตีผลกำไร โปรดบริหารความเสี่ยงเสมอ (แนะนำไม่เกิน 1–2% ต่อไม้)"
)

SYSTEM_PROMPT = """คุณคือผู้เชี่ยวชาญด้านการวิเคราะห์กราฟเทรดระดับมืออาชีพ เชี่ยวชาญ Technical Analysis,
Price Action, EMA/SMA, RSI, MACD, Volume, Support & Resistance, Market Structure, Risk Management
และ Trading Psychology

หน้าที่: วิเคราะห์กราฟจากข้อมูลตัวเลข (OHLCV + อินดิเคเตอร์ที่คำนวณมาให้) อย่างเป็นระบบ ตามหัวข้อ:
1. ภาพรวมกราฟ: สินทรัพย์, timeframe, แนวโน้มหลัก (ขาขึ้น/ขาลง/sideway), โครงสร้างตลาด (HH/HL/LH/LL/range)
2. แนวรับ–แนวต้าน: ระบุโซนสำคัญ และโซนที่ควรรอการยืนยัน
3. อินดิเคเตอร์: ตีความ EMA/SMA, RSI (overbought/oversold/divergence), MACD (ตัดกัน/โมเมนตัม), Volume
4. Price Action & Market Structure: breakout/fakeout/pullback, โซน liquidity ที่ควรระวัง
5. Sentiment: ประเมินอารมณ์ตลาดจากกราฟ (ถ้าไม่มีข้อมูลข่าว ห้ามแต่งข่าว ให้ระบุว่าไม่มีข้อมูล)
6. แผนการเทรด อย่างน้อย 2 แผน (Long และ Short) แต่ละแผนมี: เงื่อนไขที่ควรรอ, จุดเข้าโดยประมาณ,
   Stop Loss, Take Profit 1/2/3, Risk:Reward โดยประมาณ, เหตุผลสนับสนุน, จุดที่ทำให้แผนใช้ไม่ได้
   (ถ้ากราฟไม่ชัด ให้เสนอแผน "รอดูจังหวะ")
7. ประเมินความเสี่ยง: ความเสี่ยงหลัก, สัญญาณหลอกที่ต้องระวัง, กรณีที่ไม่ควรเข้าเทรด, ขนาดความเสี่ยงต่อพอร์ต
8. สรุปแบบเข้าใจง่าย: มุมมองระยะสั้น/กลาง/ยาว, Bias (Bullish/Bearish/Neutral), สิ่งที่ควรรอเพื่อยืนยัน

ข้อบังคับ:
- ใช้ภาษาไทย จัดเป็นหัวข้อ อ่านง่าย
- ห้ามฟันธง ห้ามใช้คำว่า "ต้องซื้อ/ต้องขาย/รับประกันกำไร/ไม่มีทางขาดทุน"
  ให้ใช้ "มีโอกาส", "ควรรอการยืนยัน", "หากราคาเบรก", "หากราคาหลุด", "เป็นไปได้ว่า"
- แยก "ข้อเท็จจริงจากกราฟ" ออกจาก "สมมติฐานการวิเคราะห์" ให้ชัดเจน
- เน้น Risk Management เสมอ
- ถ้าข้อมูลไม่พอ ให้ระบุว่าจุดใดสรุปไม่ได้ และท้ายคำตอบบอก "ข้อมูลที่ควรแนบเพิ่มเพื่อให้วิเคราะห์แม่นขึ้น" """


def _compact_candles(candles: list[Candle], keep: int = 60) -> list[list]:
    """ย่อแท่งเทียนเป็น array สั้น ๆ เพื่อประหยัด token."""
    tail = candles[-keep:]
    return [[c.time, c.open, c.high, c.low, c.close, round(c.volume)] for c in tail]


def _rule_based_report(symbol: str, timeframe: str, summary: dict) -> str:
    """สรุปแบบกฎเมื่อไม่มี ANTHROPIC_API_KEY — ใช้ชี้ทิศทางคร่าว ๆ เท่านั้น."""
    bias = "Neutral"
    reasons = []
    price, ema20, ema50 = summary.get("price"), summary.get("ema20"), summary.get("ema50")
    rsi14 = summary.get("rsi14")
    if price and ema20 and ema50:
        if price > ema20 > ema50:
            bias = "Bullish (ราคายืนเหนือ EMA20/EMA50)"
            reasons.append("ราคาอยู่เหนือเส้นค่าเฉลี่ยทั้งสอง")
        elif price < ema20 < ema50:
            bias = "Bearish (ราคาอยู่ใต้ EMA20/EMA50)"
            reasons.append("ราคาอยู่ใต้เส้นค่าเฉลี่ยทั้งสอง")
    if rsi14 is not None:
        if rsi14 >= 70:
            reasons.append(f"RSI={rsi14} เข้าเขต overbought มีโอกาสพักตัว")
        elif rsi14 <= 30:
            reasons.append(f"RSI={rsi14} เข้าเขต oversold มีโอกาสเด้ง")
    return (
        f"# สรุปแบบกฎ (rule-based) — {symbol} [{timeframe}]\n\n"
        "> ยังไม่ได้ตั้งค่า ANTHROPIC_API_KEY จึงใช้สรุปอย่างง่ายแทนการวิเคราะห์เต็มรูปแบบของ AI\n\n"
        f"**Bias เบื้องต้น:** {bias}\n\n"
        f"**ข้อเท็จจริงจากกราฟ:** ราคา={price}, EMA20={ema20}, EMA50={ema50}, "
        f"RSI14={rsi14}, โครงสร้าง={summary.get('structure')}, "
        f"แนวรับ~{summary.get('support_recent')}, แนวต้าน~{summary.get('resistance_recent')}\n\n"
        "**ข้อสังเกต:** " + ("; ".join(reasons) if reasons else "ยังไม่เด่นชัด ควรรอการยืนยัน") + "\n\n"
        "ตั้งค่า ANTHROPIC_API_KEY เพื่อรับรายงานวิเคราะห์ครบ 8 หัวข้อตามเทมเพลตมืออาชีพ"
    )


async def analyze(symbol: str, timeframe: str, candles: list[Candle],
                  indicators: dict, note: str | None = None) -> tuple[str, str]:
    """คืน (report, source). source = 'claude' หรือ 'rule-based'."""
    settings = get_settings()
    summary = indicators.get("summary", {})

    if not settings.anthropic_api_key:
        return _rule_based_report(symbol, timeframe, summary), "rule-based"

    # เรียก Claude
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    payload = {
        "symbol": symbol,
        "timeframe": timeframe,
        "indicator_summary": summary,
        "recent_candles_ohlcv": _compact_candles(candles),
        "user_note": note or "ไม่มีข้อมูลเพิ่มเติม",
        "data_caveat": "ข้อมูลนี้เป็นชุดตัวเลขที่ระบบดึง/คำนวณมา ไม่มีภาพกราฟและไม่มีข้อมูลข่าว",
    }

    user_msg = (
        "วิเคราะห์ข้อมูลกราฟต่อไปนี้ตามเทมเพลต 8 หัวข้อ (ข้อมูลเป็น JSON):\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )

    resp = await client.messages.create(
        model=settings.anthropic_model,
        max_tokens=4000,
        thinking={"type": "adaptive"},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text")
    return text, "claude"
