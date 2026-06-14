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


def bollinger(values: list[float], period: int = 20, mult: float = 2.0):
    """คืน (mid, upper, lower) เป็น series."""
    mid = sma(values, period)
    upper: list[float | None] = [None] * len(values)
    lower: list[float | None] = [None] * len(values)
    for i in range(len(values)):
        if i + 1 >= period:
            window = values[i + 1 - period:i + 1]
            m = mid[i]
            var = sum((x - m) ** 2 for x in window) / period
            sd = var ** 0.5
            upper[i] = m + mult * sd
            lower[i] = m - mult * sd
    return mid, upper, lower


def stochastic(highs: list[float], lows: list[float], closes: list[float],
               k_period: int = 14, d_period: int = 3):
    """คืน (%K, %D) เป็น series."""
    k: list[float | None] = [None] * len(closes)
    for i in range(len(closes)):
        if i + 1 >= k_period:
            hh = max(highs[i + 1 - k_period:i + 1])
            ll = min(lows[i + 1 - k_period:i + 1])
            k[i] = 100 * (closes[i] - ll) / (hh - ll) if hh != ll else 50.0
    # %D = SMA ของ %K
    d: list[float | None] = [None] * len(closes)
    for i in range(len(closes)):
        window = [v for v in k[max(0, i + 1 - d_period):i + 1] if v is not None]
        if len(window) == d_period:
            d[i] = sum(window) / d_period
    return k, d


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


# ค่า period เริ่มต้นของอินดิเคเตอร์ (ผู้ใช้ปรับได้ผ่านแผงเครื่องมือ)
INDICATOR_DEFAULTS = {
    "ema_fast": 20, "ema_mid": 50, "ema_slow": 200,
    "rsi_period": 14,
    "bb_period": 20, "bb_mult": 2.0,
    "stoch_k": 14, "stoch_d": 3,
    "macd_fast": 12, "macd_slow": 26, "macd_signal": 9,
}


def _merge_params(params: dict | None) -> dict:
    p = dict(INDICATOR_DEFAULTS)
    if params:
        for k, v in params.items():
            if k in p and v is not None:
                try:
                    p[k] = float(v) if k == "bb_mult" else int(v)
                except (ValueError, TypeError):
                    pass
    return p


def compute_indicators(candles: list[Candle], params: dict | None = None) -> dict:
    """คำนวณอินดิเคเตอร์ทั้งหมดและสรุปค่าล่าสุด — ใช้ทั้งแสดงผลและป้อนให้ AI.

    params: ปรับ period ของอินดิเคเตอร์ได้ (ดู INDICATOR_DEFAULTS). คีย์ใน summary/series
    ยังคงเดิม (ema20/ema50/sma200/rsi14) เป็น 'ช่อง' fast/mid/slow เพื่อความเข้ากันได้.
    """
    p = _merge_params(params)
    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    volumes = [c.volume for c in candles]

    ema20 = ema(closes, p["ema_fast"])
    ema50 = ema(closes, p["ema_mid"])
    sma200 = ema(closes, p["ema_slow"])  # ช่อง slow ใช้ EMA ตามค่าที่ตั้ง
    rsi14 = rsi(closes, p["rsi_period"])
    macd_line, signal_line, hist = macd(closes, p["macd_fast"], p["macd_slow"], p["macd_signal"])
    bb_mid, bb_upper, bb_lower = bollinger(closes, p["bb_period"], p["bb_mult"])
    stoch_k, stoch_d = stochastic(highs, lows, closes, p["stoch_k"], p["stoch_d"])

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
        "bb_upper": _last(bb_upper),
        "bb_mid": _last(bb_mid),
        "bb_lower": _last(bb_lower),
        "stoch_k": _last(stoch_k),
        "stoch_d": _last(stoch_d),
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
        "params": p,   # ค่า period ที่ใช้จริง (ให้ frontend ทำ label)
        # ส่ง series ไปให้ frontend วาดได้ (เฉพาะค่าล่าสุด ๆ เพื่อความเบา)
        "series": {
            "ema20": [None if v is None else round(v, 2) for v in ema20],
            "ema50": [None if v is None else round(v, 2) for v in ema50],
            "sma200": [None if v is None else round(v, 2) for v in sma200],
            "rsi14": [None if v is None else round(v, 2) for v in rsi14],
            "macd": [None if v is None else round(v, 4) for v in macd_line],
            "macd_signal": [None if v is None else round(v, 4) for v in signal_line],
            "macd_hist": [None if v is None else round(v, 4) for v in hist],
            "bb_mid": [None if v is None else round(v, 2) for v in bb_mid],
            "bb_upper": [None if v is None else round(v, 2) for v in bb_upper],
            "bb_lower": [None if v is None else round(v, 2) for v in bb_lower],
            "stoch_k": [None if v is None else round(v, 2) for v in stoch_k],
            "stoch_d": [None if v is None else round(v, 2) for v in stoch_d],
        },
    }
