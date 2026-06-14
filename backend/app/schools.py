"""Python evaluators เชิงกฎ — ประเมินศาสตร์ที่คำนวณได้แน่นอนจากตัวเลข.

แต่ละฟังก์ชันคืน verdict dict (ตรงกับ schema SchoolVerdict) = หนึ่งแถวในตาราง.
ศาสตร์เชิง pattern (Elliott/harmonic/candlestick/SMC/Wyckoff/Gann/psychology)
ประเมินด้วย Claude ใน analysis.py แทน.
"""
from __future__ import annotations

from app import knowledge_base as kb
from app.schemas import Candle

# id -> (name, category) จาก registry
_META = {s["id"]: (s["display_name"], s["category"]) for s in kb.schools()}


def _verdict(school_id: str, view: str, signal: str, confidence: int, rationale: str) -> dict:
    name, category = _META.get(school_id, (school_id, "indicator"))
    return {
        "id": school_id, "name": name, "category": category,
        "view": view, "signal": signal,
        "confidence": max(0, min(100, int(confidence))),
        "rationale": rationale, "evaluator": "python",
    }


def _eval_trend_ema(s: dict, candles, ind) -> dict:
    price, e20, e50, s200 = s.get("price"), s.get("ema20"), s.get("ema50"), s.get("sma200")
    if not (price and e20 and e50):
        return _verdict("trend_ema", "neutral", "wait", 20, "ข้อมูลไม่พอคำนวณเส้นค่าเฉลี่ย")
    if price > e20 > e50:
        conf = 78 if (s200 and price > s200) else 68
        extra = " และเหนือ SMA200 (เทรนด์ใหญ่ขึ้น)" if (s200 and price > s200) else ""
        return _verdict("trend_ema", "up", "buy", conf,
                        f"ราคา {price} เรียงเหนือ EMA20({e20})/EMA50({e50}){extra}")
    if price < e20 < e50:
        conf = 78 if (s200 and price < s200) else 68
        extra = " และต่ำกว่า SMA200 (เทรนด์ใหญ่ลง)" if (s200 and price < s200) else ""
        return _verdict("trend_ema", "down", "sell", conf,
                        f"ราคา {price} เรียงใต้ EMA20({e20})/EMA50({e50}){extra}")
    view = "up" if price > e20 else "down"
    return _verdict("trend_ema", view, "wait", 40,
                    f"เส้นค่าเฉลี่ยพันกัน (ราคา {price} vs EMA20 {e20}, EMA50 {e50}) — เทรนด์ไม่ชัด")


def _eval_rsi(s: dict, candles, ind) -> dict:
    r = s.get("rsi14")
    if r is None:
        return _verdict("rsi", "neutral", "wait", 20, "ข้อมูลไม่พอคำนวณ RSI")
    if r >= 70:
        return _verdict("rsi", "down", "sell", min(45 + int(r - 70) * 2, 65),
                        f"RSI={r} เข้าเขต overbought มีโอกาสพักตัว/ย่อ")
    if r <= 30:
        return _verdict("rsi", "up", "buy", min(45 + int(30 - r) * 2, 65),
                        f"RSI={r} เข้าเขต oversold มีโอกาสเด้ง")
    if r >= 55:
        return _verdict("rsi", "up", "buy", 45 + int(r - 55), f"RSI={r} โมเมนตัมฝั่งขึ้น (>50)")
    if r <= 45:
        return _verdict("rsi", "down", "sell", 45 + int(45 - r), f"RSI={r} โมเมนตัมฝั่งลง (<50)")
    return _verdict("rsi", "neutral", "wait", 35, f"RSI={r} กลาง ๆ ใกล้ 50 — ไม่มีโมเมนตัมเด่น")


def _eval_macd(s: dict, candles, ind) -> dict:
    m, sig, hist = s.get("macd"), s.get("macd_signal"), s.get("macd_hist")
    if m is None or sig is None:
        return _verdict("macd", "neutral", "wait", 20, "ข้อมูลไม่พอคำนวณ MACD")
    above = m > sig
    pos = m > 0
    if above and pos:
        return _verdict("macd", "up", "buy", 65, f"MACD({m}) เหนือ signal({sig}) และเหนือศูนย์ — โมเมนตัมขึ้น")
    if not above and not pos:
        return _verdict("macd", "down", "sell", 65, f"MACD({m}) ใต้ signal({sig}) และใต้ศูนย์ — โมเมนตัมลง")
    if above and not pos:
        return _verdict("macd", "up", "buy", 50, f"MACD({m}) ตัดขึ้นเหนือ signal แต่ยังใต้ศูนย์ — เริ่มฟื้น")
    return _verdict("macd", "down", "sell", 50, f"MACD({m}) ตัดลงใต้ signal แต่ยังเหนือศูนย์ — เริ่มอ่อนแรง")


def _eval_bollinger(s: dict, candles, ind) -> dict:
    price, up, mid, low = s.get("price"), s.get("bb_upper"), s.get("bb_mid"), s.get("bb_lower")
    if not (price and up and low and up != low):
        return _verdict("bollinger", "neutral", "wait", 20, "ข้อมูลไม่พอคำนวณ Bollinger")
    pctb = (price - low) / (up - low)
    if pctb >= 0.95:
        return _verdict("bollinger", "down", "sell", 45, f"ราคาแตะแบนด์บน (%b={pctb:.2f}) ตึงด้านขึ้น เสี่ยงย่อ")
    if pctb <= 0.05:
        return _verdict("bollinger", "up", "buy", 45, f"ราคาแตะแบนด์ล่าง (%b={pctb:.2f}) ตึงด้านลง มีโอกาสเด้ง")
    view = "up" if price > mid else "down"
    return _verdict("bollinger", view, "wait", 35,
                    f"ราคาอยู่ {'เหนือ' if view=='up' else 'ใต้'}เส้นกลาง (%b={pctb:.2f}) — ปกติ")


def _eval_stochastic(s: dict, candles, ind) -> dict:
    k, d = s.get("stoch_k"), s.get("stoch_d")
    if k is None:
        return _verdict("stochastic", "neutral", "wait", 20, "ข้อมูลไม่พอคำนวณ Stochastic")
    if k >= 80:
        return _verdict("stochastic", "down", "sell", 48, f"Stochastic %K={k} overbought (>80)")
    if k <= 20:
        return _verdict("stochastic", "up", "buy", 48, f"Stochastic %K={k} oversold (<20)")
    if d is not None:
        view = "up" if k > d else "down"
        return _verdict("stochastic", view, "wait", 35,
                        f"%K={k} {'เหนือ' if view=='up' else 'ใต้'} %D={d} โมเมนตัมระยะสั้น")
    return _verdict("stochastic", "neutral", "wait", 30, f"%K={k} กลาง ๆ")


def _eval_support_resistance(s: dict, candles, ind) -> dict:
    price, sup, res = s.get("price"), s.get("support_recent"), s.get("resistance_recent")
    if not (price and sup and res and res != sup):
        return _verdict("support_resistance", "neutral", "wait", 20, "ข้อมูลไม่พอหาแนวรับ-ต้าน")
    pos = (price - sup) / (res - sup)  # 0=ที่แนวรับ, 1=ที่แนวต้าน
    if pos <= 0.15:
        return _verdict("support_resistance", "up", "buy", 58,
                        f"ราคา {price} ใกล้แนวรับ ~{sup} — มีโอกาสเด้ง (รอแท่งยืนยัน)")
    if pos >= 0.85:
        return _verdict("support_resistance", "down", "sell", 58,
                        f"ราคา {price} ใกล้แนวต้าน ~{res} — มีโอกาสถูกปฏิเสธ")
    return _verdict("support_resistance", "neutral", "wait", 35,
                    f"ราคา {price} อยู่กลางกรอบ {sup}-{res} — รอเข้าใกล้แนว")


def _eval_volume(s: dict, candles: list[Candle], ind) -> dict:
    last_v, avg_v = s.get("last_volume"), s.get("avg_volume_20")
    if not (last_v and avg_v) or len(candles) < 2:
        return _verdict("volume", "neutral", "wait", 20, "ข้อมูลปริมาณไม่พอ")
    ratio = last_v / avg_v if avg_v else 1
    last = candles[-1]
    bullish_bar = last.close >= last.open
    if ratio >= 1.5:
        if bullish_bar:
            return _verdict("volume", "up", "buy", 55,
                            f"Volume สูงผิดปกติ ({ratio:.1f}x) บนแท่งขึ้น — แรงซื้อยืนยัน")
        return _verdict("volume", "down", "sell", 55,
                        f"Volume สูงผิดปกติ ({ratio:.1f}x) บนแท่งลง — แรงขายยืนยัน")
    if ratio <= 0.6:
        return _verdict("volume", "neutral", "wait", 35,
                        f"Volume เบาบาง ({ratio:.1f}x) — ขาดแรงสนับสนุน ระวังสัญญาณหลอก")
    view = "up" if bullish_bar else "down"
    return _verdict("volume", view, "wait", 35, f"Volume ปกติ ({ratio:.1f}x) บนแท่ง{'ขึ้น' if bullish_bar else 'ลง'}")


def _eval_dow(s: dict, candles, ind) -> dict:
    st = str(s.get("structure", ""))
    if "uptrend" in st:
        return _verdict("dow_theory", "up", "buy", 66, f"โครงสร้างขาขึ้น {st} (ยอด-ก้นยกสูง)")
    if "downtrend" in st:
        return _verdict("dow_theory", "down", "sell", 66, f"โครงสร้างขาลง {st} (ยอด-ก้นกดต่ำ)")
    return _verdict("dow_theory", "neutral", "wait", 35, f"โครงสร้าง {st or 'ไม่ชัด'} — ยังไม่มีเทรนด์ชัด")


def _find_swings(values: list[float], lookback: int = 3):
    highs, lows = [], []
    for i in range(lookback, len(values) - lookback):
        w = values[i - lookback:i + lookback + 1]
        if values[i] == max(w):
            highs.append((i, values[i]))
        if values[i] == min(w):
            lows.append((i, values[i]))
    return highs, lows


def _eval_divergence(s: dict, candles: list[Candle], ind) -> dict:
    closes = [c.close for c in candles]
    rsi_series = ind.get("series", {}).get("rsi14", [])
    if len(closes) < 30 or len(rsi_series) != len(closes):
        return _verdict("divergence", "neutral", "wait", 20, "ข้อมูลไม่พอตรวจ divergence")
    window = 40
    cl = closes[-window:]
    rs = rsi_series[-window:]
    p_highs, p_lows = _find_swings(cl)

    def rsi_at(idx):
        return rs[idx] if idx < len(rs) and rs[idx] is not None else None

    if len(p_lows) >= 2:
        (i1, l1), (i2, l2) = p_lows[-2], p_lows[-1]
        r1, r2 = rsi_at(i1), rsi_at(i2)
        if r1 and r2 and l2 < l1 and r2 > r1:
            return _verdict("divergence", "up", "buy", 62,
                            "Bullish divergence: ราคาทำ Lower Low แต่ RSI ทำ Higher Low — แรงขายอ่อนลง")
    if len(p_highs) >= 2:
        (i1, h1), (i2, h2) = p_highs[-2], p_highs[-1]
        r1, r2 = rsi_at(i1), rsi_at(i2)
        if r1 and r2 and h2 > h1 and r2 < r1:
            return _verdict("divergence", "down", "sell", 62,
                            "Bearish divergence: ราคาทำ Higher High แต่ RSI ทำ Lower High — แรงซื้ออ่อนลง")
    return _verdict("divergence", "neutral", "wait", 30, "ไม่พบ divergence ชัดเจนในช่วงล่าสุด")


def _eval_mmc(s: dict, candles: list[Candle], ind) -> dict:
    """Market Maker Concept — ตรวจ Liquidity Sweep บนแท่งล่าสุด (กวาด stop แล้วเด้งกลับ)."""
    lookback = 20
    if len(candles) < lookback + 2:
        return _verdict("mmc_liquidity", "neutral", "wait", 20, "ข้อมูลไม่พอตรวจ liquidity sweep")
    cur = candles[-1]
    prior = candles[-(lookback + 1):-1]
    prior_low = min(c.low for c in prior)
    prior_high = max(c.high for c in prior)
    # bullish sweep: ไส้หลุดใต้ swing low แต่ปิดกลับเหนือ
    if cur.low < prior_low and cur.close > prior_low:
        return _verdict("mmc_liquidity", "up", "buy", 70,
                        f"Bullish liquidity sweep: ไส้หลุดใต้แนวรับ ~{round(prior_low, 2)} แล้วปิดกลับเหนือ — เจ้ามือกวาด stop ฝั่งขายแล้วเด้ง")
    # bearish sweep: ไส้ทะลุเหนือ swing high แต่ปิดกลับใต้
    if cur.high > prior_high and cur.close < prior_high:
        return _verdict("mmc_liquidity", "down", "sell", 70,
                        f"Bearish liquidity sweep: ไส้ทะลุเหนือแนวต้าน ~{round(prior_high, 2)} แล้วปิดกลับใต้ — เจ้ามือกวาด stop ฝั่งซื้อแล้วย่อ")
    return _verdict("mmc_liquidity", "neutral", "wait", 30,
                    "ยังไม่เกิดการกวาดสภาพคล่อง (liquidity sweep) ชัดเจนบนแท่งล่าสุด")


_EVALUATORS = {
    "trend_ema": _eval_trend_ema,
    "rsi": _eval_rsi,
    "macd": _eval_macd,
    "bollinger": _eval_bollinger,
    "stochastic": _eval_stochastic,
    "support_resistance": _eval_support_resistance,
    "volume": _eval_volume,
    "dow_theory": _eval_dow,
    "divergence": _eval_divergence,
    "mmc_liquidity": _eval_mmc,
}


def evaluate_python_schools(candles: list[Candle], indicators: dict) -> list[dict]:
    """รัน evaluator เชิงกฎทั้งหมด คืนรายการ verdict (เรียงตาม registry)."""
    summary = indicators.get("summary", {})
    out = []
    for school in kb.schools_by_evaluator("python"):
        fn = _EVALUATORS.get(school["id"])
        if fn:
            out.append(fn(summary, candles, indicators))
    return out
