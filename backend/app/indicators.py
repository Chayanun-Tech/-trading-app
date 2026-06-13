"""เครื่องคำนวณอินดิเคเตอร์ (pure Python — ไม่งอก dependency).

โปรดักชันจริงควรย้ายไป pandas-ta / TA-Lib เพื่อความเร็วและความครบถ้วน.
ครอบคลุม: SMA, EMA, RSI(Wilder), MACD, swing structure, แนวรับ/แนวต้านอย่างง่าย.
"""
from __future__ import annotations

from app.schemas import Candle


def sma(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = []
    for i in range(len(values)):
        if i + 1 < period:
            out.append(None)
        else:
            out.append(sum(values[i + 1 - period:i + 1]) / period)
    return out


def ema(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    k = 2 / (period + 1)
    # seed ด้วย SMA ของ period แรก
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(values: list[float], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return out
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        d = values[i] - values[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    avg_gain = gains / period
    avg_loss = losses / period
    rs = avg_gain / avg_loss if avg_loss else float("inf")
    out[period] = 100 - 100 / (1 + rs)
    for i in range(period + 1, len(values)):
        d = values[i] - values[i - 1]
        gain = max(d, 0)
        loss = max(-d, 0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        rs = avg_gain / avg_loss if avg_loss else float("inf")
        out[i] = 100 - 100 / (1 + rs)
    return out


def macd(values: list[float], fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = ema(values, fast)
    ema_slow = ema(values, slow)
    macd_line: list[float | None] = []
    for f, s in zip(ema_fast, ema_slow):
        macd_line.append(f - s if (f is not None and s is not None) else None)
    valid = [m for m in macd_line if m is not None]
    sig_valid = ema(valid, signal) if valid else []
    # map กลับเข้าตำแหน่งเดิม
    signal_line: list[float | None] = [None] * len(macd_line)
    j = 0
    for i, m in enumerate(macd_line):
        if m is not None:
            signal_line[i] = sig_valid[j] if j < len(sig_valid) else None
            j += 1
    hist: list[float | None] = [
        (m - s) if (m is not None and s is not None) else None
        for m, s in zip(macd_line, signal_line)
    ]
    return macd_line, signal_line, hist


def _last(seq: list[float | None]):
    for v in reversed(seq):
        if v is not None:
            return round(v, 4)
    return None


def swing_structure(highs: list[float], lows: list[float], lookback: int = 5):
    """หา swing high/low อย่างง่ายและประเมินโครงสร้างตลาด (HH/HL/LH/LL/range)."""
    swing_highs, swing_lows = [], []
    n = len(highs)
    for i in range(lookback, n - lookback):
        window_h = highs[i - lookback:i + lookback + 1]
        window_l = lows[i - lookback:i + lookback + 1]
        if highs[i] == max(window_h):
            swing_highs.append((i, highs[i]))
        if lows[i] == min(window_l):
            swing_lows.append((i, lows[i]))

    structure = "range"
    if len(swing_highs) >= 2 and len(swing_lows) >= 2:
        hh = swing_highs[-1][1] > swing_highs[-2][1]
        hl = swing_lows[-1][1] > swing_lows[-2][1]
        lh = swing_highs[-1][1] < swing_highs[-2][1]
        ll = swing_lows[-1][1] < swing_lows[-2][1]
        if hh and hl:
            structure = "uptrend (HH/HL)"
        elif lh and ll:
            structure = "downtrend (LH/LL)"
        else:
            structure = "mixed/range"
    return {
        "structure": structure,
        "recent_swing_highs": [round(v, 2) for _, v in swing_highs[-3:]],
        "recent_swing_lows": [round(v, 2) for _, v in swing_lows[-3:]],
    }


def compute_indicators(candles: list[Candle]) -> dict:
    """คำนวณอินดิเคเตอร์ทั้งหมดและสรุปค่าล่าสุด — ใช้ทั้งแสดงผลและป้อนให้ AI."""
    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    volumes = [c.volume for c in candles]

    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    sma200 = sma(closes, 200)
    rsi14 = rsi(closes, 14)
    macd_line, signal_line, hist = macd(closes)

    price = closes[-1] if closes else None
    structure = swing_structure(highs, lows)

    # แนวรับ/แนวต้านอย่างง่ายจาก min/max ช่วงล่าสุด
    window = closes[-50:] if len(closes) >= 50 else closes
    support = round(min(lows[-50:]), 2) if lows else None
    resistance = round(max(highs[-50:]), 2) if highs else None

    avg_vol = round(sum(volumes[-20:]) / min(len(volumes), 20), 0) if volumes else None
    last_vol = round(volumes[-1], 0) if volumes else None

    summary = {
        "price": round(price, 2) if price is not None else None,
        "ema20": _last(ema20),
        "ema50": _last(ema50),
        "sma200": _last(sma200),
        "rsi14": _last(rsi14),
        "macd": _last(macd_line),
        "macd_signal": _last(signal_line),
        "macd_hist": _last(hist),
        "support_recent": support,
        "resistance_recent": resistance,
        "avg_volume_20": avg_vol,
        "last_volume": last_vol,
        "price_vs_ema20": (
            "above" if (price and _last(ema20) and price > _last(ema20)) else "below"
        ),
        "price_vs_ema50": (
            "above" if (price and _last(ema50) and price > _last(ema50)) else "below"
        ),
        **structure,
    }

    return {
        "summary": summary,
        # ส่ง series ไปให้ frontend วาดได้ (เฉพาะค่าล่าสุด ๆ เพื่อความเบา)
        "series": {
            "ema20": [None if v is None else round(v, 2) for v in ema20],
            "ema50": [None if v is None else round(v, 2) for v in ema50],
            "rsi14": [None if v is None else round(v, 2) for v in rsi14],
        },
    }
