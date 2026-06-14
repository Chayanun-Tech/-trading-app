"""ท่าไม้ตาย (Signature Strategy) + Backtest Engine.

แนวคิด: ทุกแท่งในอดีตให้ "ทุกศาสตร์ที่คำนวณได้" โหวต แล้วถ่วงน้ำหนักเป็น
คะแนนฉันทามติ (up_probability 0-100) เหมือน engine.aggregate แต่ทำย้อนหลังทีละแท่ง.
เมื่อหลายศาสตร์เห็นพ้องแรงพอ + ผ่าน Trend Filter จึงเข้า Order, ออกด้วย ATR stop/target
หรือสัญญาณกลับด้าน. จากนั้นจำลองการเทรดย้อนหลังยาว ๆ แล้วสรุปสถิติ.

ศาสตร์เชิงสูตร (Python) ที่ร่วมประเมินย้อนหลังได้จริง:
  trend_ema, rsi, macd, bollinger, stochastic, support_resistance, volume,
  dow_theory(proxy), divergence(proxy), candlestick(proxy), price_action(proxy),
  market_psychology(proxy)

ศาสตร์เชิง pattern ที่ปกติใช้ AI (Elliott/Harmonic/Wyckoff/SMC/Gann/Fibonacci)
ไม่สามารถรันย้อนหลังหลายพันแท่งได้เพราะต้องเรียก LLM ทีละแท่ง — backtest จึงใช้
'ตัวแทนเชิงกฎ (proxy)' สำหรับศาสตร์ที่คำนวณได้ และระบุไว้ใน schools_used เพื่อความโปร่งใส.

⚠️ ผลย้อนหลังไม่การันตีอนาคต (past performance ≠ future results).
"""
from __future__ import annotations

from app import knowledge_base as kb
from app.indicators import compute_indicators
from app.schemas import Candle

# น้ำหนักรายศาสตร์จาก registry (เหมือน engine.py)
_WEIGHTS = {s["id"]: float(s.get("weight", 1.0)) for s in kb.schools()}
_NAMES = {s["id"]: s["display_name"] for s in kb.schools()}

# ศาสตร์ที่ร่วมประเมินใน backtest (คำนวณได้เชิงกฎ)
BACKTEST_SCHOOLS = [
    "trend_ema", "rsi", "macd", "bollinger", "stochastic",
    "support_resistance", "volume", "dow_theory", "divergence",
    "candlestick", "price_action", "market_psychology", "mmc_liquidity",
]

DISCLAIMER = (
    "⚠️ Backtest เป็นการจำลองจากข้อมูลในอดีต ผลที่ได้ไม่การันตีอนาคต "
    "(past performance ≠ future results). มีสมมติฐานหลายข้อ (ค่า slippage/ค่าคอมฯ ไม่รวม, "
    "เข้าที่ราคาปิดแท่งสัญญาณ, ตรวจ stop ก่อน target เมื่อชนกันในแท่งเดียว). "
    "ใช้เพื่อ 'เข้าใจสถิติของระบบ' เท่านั้น ไม่ใช่คำแนะนำให้ซื้อขาย และโปรดบริหารความเสี่ยงเสมอ."
)


# ---------- ATR (สำหรับ stop/target) ----------
def _atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> list[float | None]:
    n = len(closes)
    out: list[float | None] = [None] * n
    if n <= period:
        return out
    trs: list[float] = [0.0] * n
    for i in range(1, n):
        trs[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    first = sum(trs[1:period + 1]) / period
    out[period] = first
    prev = first
    for i in range(period + 1, n):
        prev = (prev * (period - 1) + trs[i]) / period
        out[i] = prev
    return out


def _rolling_min(values: list[float], i: int, window: int) -> float:
    lo = max(0, i - window + 1)
    return min(values[lo:i + 1])


def _rolling_max(values: list[float], i: int, window: int) -> float:
    lo = max(0, i - window + 1)
    return max(values[lo:i + 1])


# ---------- ตัวช่วยตรวจแท่งเทียน (candlestick proxy) ----------
def _candle_vote(candles: list[Candle], i: int, trend_up: bool) -> tuple[str, int] | None:
    if i < 1:
        return None
    c, p = candles[i], candles[i - 1]
    body = abs(c.close - c.open)
    rng = c.high - c.low
    if rng <= 0:
        return None
    upper = c.high - max(c.close, c.open)
    lower = min(c.close, c.open) - c.low

    # Bullish/Bearish engulfing
    if c.close > c.open and p.close < p.open and c.close >= p.open and c.open <= p.close:
        return ("up", 58)
    if c.close < c.open and p.close > p.open and c.open >= p.close and c.close <= p.open:
        return ("down", 58)
    # Hammer (ขาลง → กลับขึ้น): ไส้ล่างยาว, บอดี้เล็กอยู่ส่วนบน
    if lower >= 2 * body and upper <= body and not trend_up:
        return ("up", 52)
    # Shooting star (ขาขึ้น → กลับลง): ไส้บนยาว
    if upper >= 2 * body and lower <= body and trend_up:
        return ("down", 52)
    return None


class _Ctx:
    """เก็บ series ที่ precompute ไว้ล่วงหน้า เพื่อประเมินแต่ละแท่งเร็ว ๆ."""

    def __init__(self, candles: list[Candle], indicators: dict):
        self.candles = candles
        s = indicators["series"]
        self.closes = [c.close for c in candles]
        self.highs = [c.high for c in candles]
        self.lows = [c.low for c in candles]
        self.vols = [c.volume for c in candles]
        self.ema_fast = s["ema20"]
        self.ema_mid = s["ema50"]
        self.ema_slow = s["sma200"]
        self.rsi = s["rsi14"]
        self.macd = s["macd"]
        self.macd_sig = s["macd_signal"]
        self.bb_up = s["bb_upper"]
        self.bb_mid = s["bb_mid"]
        self.bb_low = s["bb_lower"]
        self.stoch_k = s["stoch_k"]
        self.stoch_d = s["stoch_d"]
        self.atr = _atr(self.highs, self.lows, self.closes)
        # rolling avg volume 20
        self.avg_vol: list[float | None] = [None] * len(candles)
        for i in range(len(candles)):
            lo = max(0, i - 19)
            w = self.vols[lo:i + 1]
            self.avg_vol[i] = sum(w) / len(w) if w else None


def _votes_at(ctx: _Ctx, i: int, enabled: set[str]) -> list[tuple[str, str, int]]:
    """คืน list ของ (school_id, view, confidence) สำหรับแท่งที่ i.

    ใช้เฉพาะข้อมูลถึงแท่ง i (ไม่มี lookahead).
    """
    votes: list[tuple[str, str, int]] = []
    price = ctx.closes[i]
    ema_f, ema_m, ema_s = ctx.ema_fast[i], ctx.ema_mid[i], ctx.ema_slow[i]
    trend_up = bool(ema_s and price > ema_s)

    # 1) trend_ema
    if "trend_ema" in enabled and ema_f and ema_m:
        if price > ema_f > ema_m:
            votes.append(("trend_ema", "up", 78 if (ema_s and price > ema_s) else 68))
        elif price < ema_f < ema_m:
            votes.append(("trend_ema", "down", 78 if (ema_s and price < ema_s) else 68))

    # 2) rsi
    if "rsi" in enabled and ctx.rsi[i] is not None:
        r = ctx.rsi[i]
        if r <= 30:
            votes.append(("rsi", "up", min(45 + int(30 - r) * 2, 65)))
        elif r >= 70:
            votes.append(("rsi", "down", min(45 + int(r - 70) * 2, 65)))
        elif r >= 55:
            votes.append(("rsi", "up", 45 + int(r - 55)))
        elif r <= 45:
            votes.append(("rsi", "down", 45 + int(45 - r)))

    # 3) macd
    if "macd" in enabled and ctx.macd[i] is not None and ctx.macd_sig[i] is not None:
        m, sig = ctx.macd[i], ctx.macd_sig[i]
        above, pos = m > sig, m > 0
        if above and pos:
            votes.append(("macd", "up", 65))
        elif not above and not pos:
            votes.append(("macd", "down", 65))
        elif above and not pos:
            votes.append(("macd", "up", 50))
        else:
            votes.append(("macd", "down", 50))

    # 4) bollinger
    if "bollinger" in enabled and ctx.bb_up[i] and ctx.bb_low[i] and ctx.bb_up[i] != ctx.bb_low[i]:
        pctb = (price - ctx.bb_low[i]) / (ctx.bb_up[i] - ctx.bb_low[i])
        if pctb <= 0.05:
            votes.append(("bollinger", "up", 45))
        elif pctb >= 0.95:
            votes.append(("bollinger", "down", 45))

    # 5) stochastic
    if "stochastic" in enabled and ctx.stoch_k[i] is not None:
        k = ctx.stoch_k[i]
        if k <= 20:
            votes.append(("stochastic", "up", 48))
        elif k >= 80:
            votes.append(("stochastic", "down", 48))
        elif ctx.stoch_d[i] is not None:
            votes.append(("stochastic", "up" if k > ctx.stoch_d[i] else "down", 35))

    # 6) support_resistance (rolling 50)
    if "support_resistance" in enabled and i >= 20:
        sup = _rolling_min(ctx.lows, i, 50)
        res = _rolling_max(ctx.highs, i, 50)
        if res != sup:
            pos = (price - sup) / (res - sup)
            if pos <= 0.15:
                votes.append(("support_resistance", "up", 58))
            elif pos >= 0.85:
                votes.append(("support_resistance", "down", 58))

    # 7) volume (เทียบ avg 20 + ทิศแท่ง)
    if "volume" in enabled and ctx.avg_vol[i] and i >= 1:
        ratio = ctx.vols[i] / ctx.avg_vol[i] if ctx.avg_vol[i] else 1
        bull = ctx.closes[i] >= ctx.candles[i].open
        if ratio >= 1.5:
            votes.append(("volume", "up" if bull else "down", 55))

    # 8) dow_theory (proxy: ความชันของ ema_mid + ตำแหน่งราคา)
    if "dow_theory" in enabled and ema_m and i >= 10 and ctx.ema_mid[i - 10]:
        rising = ema_m > ctx.ema_mid[i - 10]
        if rising and price > ema_m:
            votes.append(("dow_theory", "up", 60))
        elif (not rising) and price < ema_m:
            votes.append(("dow_theory", "down", 60))

    # 9) divergence (proxy: RSI vs price ใน 40 แท่งล่าสุด)
    if "divergence" in enabled and i >= 40 and ctx.rsi[i] is not None:
        v = _divergence_vote(ctx, i, window=40)
        if v:
            votes.append(("divergence", v[0], v[1]))

    # 10) candlestick (proxy)
    if "candlestick" in enabled:
        cv = _candle_vote(ctx.candles, i, trend_up)
        if cv:
            votes.append(("candlestick", cv[0], cv[1]))

    # 11) price_action (proxy: breakout/breakdown ของกรอบ 20 แท่ง)
    if "price_action" in enabled and i >= 20:
        hi20 = _rolling_max(ctx.highs, i - 1, 20)
        lo20 = _rolling_min(ctx.lows, i - 1, 20)
        if price > hi20:
            votes.append(("price_action", "up", 60))
        elif price < lo20:
            votes.append(("price_action", "down", 60))

    # 12) market_psychology (proxy: extreme RSI + volume spike = จุดกลับเชิงอารมณ์)
    if "market_psychology" in enabled and ctx.rsi[i] is not None and ctx.avg_vol[i]:
        r = ctx.rsi[i]
        spike = ctx.vols[i] / ctx.avg_vol[i] >= 1.8 if ctx.avg_vol[i] else False
        if r <= 25 and spike:
            votes.append(("market_psychology", "up", 55))
        elif r >= 75 and spike:
            votes.append(("market_psychology", "down", 55))

    # 13) mmc_liquidity (Market Maker Concept — Coach James style liquidity sweep)
    if "mmc_liquidity" in enabled and i >= 21:
        prior_low = min(ctx.lows[i - 20:i])     # swing low 20 แท่งก่อนหน้า (ไม่รวมแท่งปัจจุบัน)
        prior_high = max(ctx.highs[i - 20:i])
        if ctx.lows[i] < prior_low and ctx.closes[i] > prior_low:
            votes.append(("mmc_liquidity", "up", 70))     # กวาด stop ฝั่งขายแล้วเด้ง
        elif ctx.highs[i] > prior_high and ctx.closes[i] < prior_high:
            votes.append(("mmc_liquidity", "down", 70))   # กวาด stop ฝั่งซื้อแล้วย่อ

    return votes


def _divergence_vote(ctx: _Ctx, i: int, window: int = 40) -> tuple[str, int] | None:
    lo = i - window + 1
    cl = ctx.closes[lo:i + 1]
    rs = ctx.rsi[lo:i + 1]
    lookback = 3
    highs, lows = [], []
    for j in range(lookback, len(cl) - lookback):
        w = cl[j - lookback:j + lookback + 1]
        if cl[j] == max(w):
            highs.append(j)
        if cl[j] == min(w):
            lows.append(j)
    if len(lows) >= 2:
        a, b = lows[-2], lows[-1]
        if rs[a] is not None and rs[b] is not None and cl[b] < cl[a] and rs[b] > rs[a]:
            return ("up", 62)
    if len(highs) >= 2:
        a, b = highs[-2], highs[-1]
        if rs[a] is not None and rs[b] is not None and cl[b] > cl[a] and rs[b] < rs[a]:
            return ("down", 62)
    return None


def _up_probability(votes: list[tuple[str, str, int]], weights: dict[str, float]) -> tuple[int, float]:
    """คืน (up_probability 0-100, directional_weight) จาก votes ของแท่งหนึ่ง."""
    up_score = down_score = 0.0
    dir_w = 0.0
    for sid, view, conf in votes:
        w = float(weights.get(sid, 1.0))
        contrib = w * (conf / 100.0)
        if view == "up":
            up_score += contrib
            dir_w += w
        elif view == "down":
            down_score += contrib
            dir_w += w
    total = up_score + down_score
    if total <= 0:
        return 50, dir_w
    return round(up_score / total * 100), dir_w


# ---------- Backtest หลัก ----------
def run_backtest(
    candles: list[Candle],
    *,
    timeframe: str = "1d",
    entry_threshold: int = 65,
    rr_ratio: float = 1.8,
    atr_mult: float = 1.5,
    direction: str = "both",          # both | long | short
    use_trend_filter: bool = True,
    min_directional_weight: float = 2.0,
    max_hold_bars: int = 60,
    indicator_params: dict | None = None,
    weights: dict | None = None,
    enabled_schools: list[str] | None = None,
) -> dict:
    """จำลองท่าไม้ตายย้อนหลังบนชุดแท่งเทียน. คืนสถิติ + รายการเทรด + markers + chart payload."""
    n = len(candles)
    if n < 220:
        return {
            "error": "ข้อมูลย้อนหลังไม่พอสำหรับ backtest (ต้องการอย่างน้อย ~220 แท่ง)",
            "bars": n,
        }

    indicators = compute_indicators(candles, indicator_params)
    ctx = _Ctx(candles, indicators)
    wmap = {**_WEIGHTS, **(weights or {})}
    enabled = set(enabled_schools) if enabled_schools else set(BACKTEST_SCHOOLS)
    enabled &= set(BACKTEST_SCHOOLS)

    warmup = 200          # รอ SMA200/ATR/divergence พร้อม
    allow_long = direction in ("both", "long")
    allow_short = direction in ("both", "short")
    short_thr = 100 - entry_threshold

    trades: list[dict] = []
    pos = None            # None | dict(side, entry_price, entry_time, entry_i, stop, target, conf)
    equity = 100.0
    equity_curve: list[dict] = [{"time": candles[warmup].time, "equity": 100.0}]
    peak = 100.0
    max_dd = 0.0

    for i in range(warmup, n):
        c = candles[i]

        # ---- จัดการโพสิชันที่ถืออยู่ ----
        if pos is not None:
            exit_price = None
            reason = None
            if pos["side"] == "long":
                if c.low <= pos["stop"]:
                    exit_price, reason = pos["stop"], "stop"
                elif c.high >= pos["target"]:
                    exit_price, reason = pos["target"], "target"
            else:  # short
                if c.high >= pos["stop"]:
                    exit_price, reason = pos["stop"], "stop"
                elif c.low <= pos["target"]:
                    exit_price, reason = pos["target"], "target"

            held = i - pos["entry_i"]
            if exit_price is None:
                up_prob, _ = _up_probability(_votes_at(ctx, i, enabled), wmap)
                flipped = (pos["side"] == "long" and up_prob <= short_thr) or \
                          (pos["side"] == "short" and up_prob >= entry_threshold)
                if flipped:
                    exit_price, reason = c.close, "signal_flip"
                elif held >= max_hold_bars:
                    exit_price, reason = c.close, "max_hold"

            if exit_price is not None:
                if pos["side"] == "long":
                    pnl_pct = (exit_price - pos["entry_price"]) / pos["entry_price"] * 100
                    risk = pos["entry_price"] - pos["stop"]
                    r_mult = (exit_price - pos["entry_price"]) / risk if risk else 0.0
                else:
                    pnl_pct = (pos["entry_price"] - exit_price) / pos["entry_price"] * 100
                    risk = pos["stop"] - pos["entry_price"]
                    r_mult = (pos["entry_price"] - exit_price) / risk if risk else 0.0
                equity *= (1 + pnl_pct / 100)
                peak = max(peak, equity)
                max_dd = max(max_dd, (peak - equity) / peak * 100)
                trades.append({
                    "side": pos["side"],
                    "entry_time": pos["entry_time"], "entry_price": round(pos["entry_price"], 4),
                    "exit_time": c.time, "exit_price": round(exit_price, 4),
                    "pnl_pct": round(pnl_pct, 2), "r_multiple": round(r_mult, 2),
                    "win": pnl_pct > 0, "exit_reason": reason,
                    "bars_held": held, "confluence": pos["conf"],
                })
                equity_curve.append({"time": c.time, "equity": round(equity, 2)})
                pos = None

        # ---- หาจังหวะเข้าใหม่ (เมื่อว่าง) ----
        if pos is None and i < n - 2:
            votes = _votes_at(ctx, i, enabled)
            up_prob, dir_w = _up_probability(votes, wmap)
            atr_v = ctx.atr[i]
            if atr_v and dir_w >= min_directional_weight:
                ema_s = ctx.ema_slow[i]
                long_ok = allow_long and up_prob >= entry_threshold and \
                    (not use_trend_filter or (ema_s and c.close > ema_s))
                short_ok = allow_short and up_prob <= short_thr and \
                    (not use_trend_filter or (ema_s and c.close < ema_s))
                if long_ok:
                    stop = c.close - atr_v * atr_mult
                    target = c.close + atr_v * atr_mult * rr_ratio
                    pos = {"side": "long", "entry_price": c.close, "entry_time": c.time,
                           "entry_i": i, "stop": stop, "target": target, "conf": up_prob}
                elif short_ok:
                    stop = c.close + atr_v * atr_mult
                    target = c.close - atr_v * atr_mult * rr_ratio
                    pos = {"side": "short", "entry_price": c.close, "entry_time": c.time,
                           "entry_i": i, "stop": stop, "target": target, "conf": 100 - up_prob}

    stats = _summarize(trades, equity, ctx, warmup)
    by_strength = _by_strength(trades)
    markers = _build_markers(trades)

    return {
        "symbol": None,
        "timeframe": timeframe,
        "bars_tested": n - warmup,
        "period_start": candles[warmup].time,
        "period_end": candles[-1].time,
        "params": {
            "entry_threshold": entry_threshold, "rr_ratio": rr_ratio, "atr_mult": atr_mult,
            "direction": direction, "use_trend_filter": use_trend_filter,
            "max_hold_bars": max_hold_bars,
        },
        "schools_used": [{"id": sid, "name": _NAMES.get(sid, sid)} for sid in sorted(enabled)],
        "stats": stats,
        "by_strength": by_strength,
        "trades": trades,
        "markers": markers,
        "equity_curve": equity_curve,
        "chart": {
            "candles": [c.model_dump() for c in candles],
            "indicators": indicators,
        },
        "disclaimer": DISCLAIMER,
    }


def live_signal(
    candles: list[Candle],
    *,
    timeframe: str = "1d",
    entry_threshold: int = 65,
    rr_ratio: float = 1.8,
    atr_mult: float = 1.5,
    direction: str = "both",
    use_trend_filter: bool = True,
    min_directional_weight: float = 2.0,
    indicator_params: dict | None = None,
    weights: dict | None = None,
    enabled_schools: list[str] | None = None,
    account_size: float = 1000.0,
    risk_pct: float = 1.0,
) -> dict:
    """ประเมิน 'สัญญาณ ณ ปัจจุบัน' ด้วยกฎเดียวกับ run_backtest (ประเมินแท่งล่าสุด)
    เพื่อให้สัญญาณ live สอดคล้องกับผล backtest ของกลยุทธ์เดียวกัน.

    คืนสถานะ (waiting/entry), ราคา entry/SL/TP และขนาดโพสิชันตามความเสี่ยงที่กำหนด.
    """
    n = len(candles)
    if n < 60:
        return {"error": "ข้อมูลไม่พอประเมินสัญญาณ (ต้องการอย่างน้อย ~60 แท่ง)", "bars": n}

    indicators = compute_indicators(candles, indicator_params)
    ctx = _Ctx(candles, indicators)
    wmap = {**_WEIGHTS, **(weights or {})}
    enabled = set(enabled_schools) if enabled_schools else set(BACKTEST_SCHOOLS)
    enabled &= set(BACKTEST_SCHOOLS)

    i = n - 1
    c = candles[i]
    votes = _votes_at(ctx, i, enabled)
    up_prob, dir_w = _up_probability(votes, wmap)
    atr_v = ctx.atr[i]
    ema_s = ctx.ema_slow[i]
    live_entry_threshold = max(entry_threshold, 65)
    short_thr = 100 - live_entry_threshold
    allow_long = direction in ("both", "long")
    allow_short = direction in ("both", "short")

    trend_ok_long = (not use_trend_filter) or bool(ema_s and c.close > ema_s)
    trend_ok_short = (not use_trend_filter) or bool(ema_s and c.close < ema_s)
    weight_ok = dir_w >= min_directional_weight
    rr_ok = rr_ratio >= 1.5

    long_ok = allow_long and bool(atr_v) and rr_ok and weight_ok and up_prob >= live_entry_threshold and trend_ok_long
    short_ok = allow_short and bool(atr_v) and rr_ok and weight_ok and up_prob <= short_thr and trend_ok_short

    status, side = "waiting", None
    entry = sl = tp = None
    reasons: list[str] = []
    watch_plan: list[str] = []

    if not rr_ok:
        reasons.append(f"Risk:Reward {rr_ratio}:1 is too low for Live entry; use at least 1.5:1")
    if not atr_v:
        reasons.append("ATR ยังไม่พร้อม (ข้อมูลน้อยเกินไป)")
    elif long_ok:
        side, status = "long", "entry"
        entry = c.close
        sl = entry - atr_v * atr_mult
        tp = entry + atr_v * atr_mult * rr_ratio
    elif short_ok:
        side, status = "short", "entry"
        entry = c.close
        sl = entry + atr_v * atr_mult
        tp = entry - atr_v * atr_mult * rr_ratio
    else:
        # อธิบายว่าทำไมยัง 'รอสัญญาณ'
        if entry_threshold < live_entry_threshold:
            reasons.append(f"Live A+ gate requires {live_entry_threshold}% even though the backtest strategy is set to {entry_threshold}%")
        if not weight_ok:
            reasons.append(f"น้ำหนักศาสตร์ที่เห็นพ้อง {round(dir_w, 1)} < เกณฑ์ {min_directional_weight} (สัญญาณยังไม่หนักแน่นพอ)")
        if allow_long and up_prob < live_entry_threshold and not (allow_short and up_prob <= short_thr):
            reasons.append(f"ฉันทามติขาขึ้น {up_prob}% ยังไม่ถึงเกณฑ์เข้า {entry_threshold}%")
        if allow_long and up_prob >= live_entry_threshold and not trend_ok_long:
            reasons.append("มีสัญญาณขึ้น แต่ราคาต่ำกว่า EMA หลัก (ผิดเทรนด์) — Trend Filter บล็อกไว้")
        if allow_short and up_prob > short_thr and not (allow_long and up_prob >= live_entry_threshold):
            reasons.append(f"ฉันทามติขาลง {100 - up_prob}% ยังไม่ถึงเกณฑ์เข้า {entry_threshold}%")
        if allow_short and up_prob <= short_thr and not trend_ok_short:
            reasons.append("มีสัญญาณลง แต่ราคาสูงกว่า EMA หลัก — Trend Filter บล็อกไว้")
        if not reasons:
            reasons.append("ตลาดยังไม่เข้าเงื่อนไขของกลยุทธ์ — รอจังหวะถัดไป")

    if status == "waiting":
        down_prob = 100 - up_prob
        if allow_long:
            watch_plan.append(f"Long watch: need bullish consensus {live_entry_threshold}% (now {up_prob}%, gap {max(0, live_entry_threshold - up_prob)})")
            if use_trend_filter and ema_s:
                watch_plan.append(f"Long trigger area: wait for close above EMA {round(ema_s, 4)}")
        if allow_short:
            watch_plan.append(f"Short watch: need bearish consensus {live_entry_threshold}% (now {down_prob}%, gap {max(0, live_entry_threshold - down_prob)})")
            if use_trend_filter and ema_s:
                watch_plan.append(f"Short trigger area: wait for close below EMA {round(ema_s, 4)}")
        if not weight_ok:
            watch_plan.append(f"Need stronger school agreement: {round(dir_w, 2)} / {min_directional_weight}")
        if not rr_ok:
            watch_plan.append("Raise Risk:Reward to 1.5:1 or higher before using Live entry")

    trade = None
    sizing = None
    if entry is not None and sl is not None and tp is not None:
        per_unit_risk = abs(entry - sl)
        risk_amount = account_size * (risk_pct / 100.0)
        units = (risk_amount / per_unit_risk) if per_unit_risk else 0.0
        trade = {
            "side": side,
            "entry": round(entry, 4),
            "stop_loss": round(sl, 4),
            "take_profit": round(tp, 4),
            "rr_ratio": rr_ratio,
            "risk_per_unit": round(per_unit_risk, 4),
            "reward_per_unit": round(abs(tp - entry), 4),
        }
        sizing = {
            "account_size": account_size,
            "risk_pct": risk_pct,
            "risk_amount": round(risk_amount, 2),
            "reward_amount": round(risk_amount * rr_ratio, 2),
            "units": round(units, 4),
            "position_value": round(units * entry, 2),
        }

    contributing = [
        {"id": sid, "name": _NAMES.get(sid, sid), "view": view,
         "confidence": conf, "weight": round(float(wmap.get(sid, 1.0)), 2)}
        for sid, view, conf in votes
    ]

    return {
        "timeframe": timeframe,
        "as_of_time": c.time,
        "last_price": round(c.close, 4),
        "status": status,
        "side": side,
        "up_probability": up_prob,
        "down_probability": 100 - up_prob,
        "directional_weight": round(dir_w, 2),
        "min_directional_weight": min_directional_weight,
        "entry_threshold": entry_threshold,
        "live_entry_threshold": live_entry_threshold,
        "rr_ok": rr_ok,
        "atr": round(atr_v, 4) if atr_v else None,
        "ema_slow": round(ema_s, 4) if ema_s else None,
        "trade": trade,
        "sizing": sizing,
        "reasons": reasons,
        "watch_plan": watch_plan,
        "contributing": contributing,
        "params": {
            "entry_threshold": entry_threshold, "rr_ratio": rr_ratio, "atr_mult": atr_mult,
            "direction": direction, "use_trend_filter": use_trend_filter,
            "min_directional_weight": min_directional_weight,
        },
        "disclaimer": DISCLAIMER,
    }


def _summarize(trades: list[dict], final_equity: float, ctx: _Ctx, warmup: int) -> dict:
    total = len(trades)
    closes = ctx.closes
    bh = (closes[-1] / closes[warmup] - 1) * 100 if closes[warmup] else 0.0
    if total == 0:
        return {
            "total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "profit_factor": 0.0, "total_return_pct": 0.0, "max_drawdown_pct": 0.0,
            "avg_win_pct": 0.0, "avg_loss_pct": 0.0, "expectancy_r": 0.0,
            "avg_hold_bars": 0.0, "best_trade_pct": 0.0, "worst_trade_pct": 0.0,
            "buy_hold_return_pct": round(bh, 2), "long_trades": 0, "short_trades": 0,
        }
    wins = [t for t in trades if t["win"]]
    losses = [t for t in trades if not t["win"]]
    gross_win = sum(t["pnl_pct"] for t in wins)
    gross_loss = abs(sum(t["pnl_pct"] for t in losses))
    avg_win = gross_win / len(wins) if wins else 0.0
    avg_loss = gross_loss / len(losses) if losses else 0.0
    win_rate = len(wins) / total * 100
    expectancy_r = sum(t["r_multiple"] for t in trades) / total
    return {
        "total_trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 1),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else (999.0 if gross_win else 0.0),
        "total_return_pct": round(final_equity - 100, 2),
        "max_drawdown_pct": round(_max_dd_from_trades(trades), 2),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(-avg_loss, 2),
        "expectancy_r": round(expectancy_r, 2),
        "avg_hold_bars": round(sum(t["bars_held"] for t in trades) / total, 1),
        "best_trade_pct": round(max(t["pnl_pct"] for t in trades), 2),
        "worst_trade_pct": round(min(t["pnl_pct"] for t in trades), 2),
        "buy_hold_return_pct": round(bh, 2),
        "long_trades": sum(1 for t in trades if t["side"] == "long"),
        "short_trades": sum(1 for t in trades if t["side"] == "short"),
    }


def _max_dd_from_trades(trades: list[dict]) -> float:
    eq = 100.0
    peak = 100.0
    mdd = 0.0
    for t in trades:
        eq *= (1 + t["pnl_pct"] / 100)
        peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak * 100)
    return mdd


def _by_strength(trades: list[dict]) -> list[dict]:
    """แยกอัตราชนะตามความแรงของฉันทามติตอนเข้า — ตอบโจทย์ 'setup แบบไหนชนะสูงสุด'."""
    buckets = [(65, 70), (70, 75), (75, 80), (80, 85), (85, 101)]
    out = []
    for lo, hi in buckets:
        sel = [t for t in trades if lo <= t["confluence"] < hi]
        if not sel:
            continue
        wins = sum(1 for t in sel if t["win"])
        out.append({
            "range": f"{lo}–{min(hi, 100)}%",
            "trades": len(sel),
            "wins": wins,
            "win_rate": round(wins / len(sel) * 100, 1),
            "avg_pnl_pct": round(sum(t["pnl_pct"] for t in sel) / len(sel), 2),
        })
    return out


def _build_markers(trades: list[dict]) -> list[dict]:
    """สร้าง markers สำหรับ lightweight-charts (เรียงตามเวลา)."""
    markers = []
    for t in trades:
        if t["side"] == "long":
            markers.append({"time": t["entry_time"], "position": "belowBar",
                            "color": "#22c55e", "shape": "arrowUp", "text": f"L {t['confluence']}%"})
        else:
            markers.append({"time": t["entry_time"], "position": "aboveBar",
                            "color": "#ef4444", "shape": "arrowDown", "text": f"S {t['confluence']}%"})
        markers.append({
            "time": t["exit_time"],
            "position": "aboveBar" if t["side"] == "long" else "belowBar",
            "color": "#16a34a" if t["win"] else "#dc2626",
            "shape": "circle",
            "text": ("+" if t["pnl_pct"] >= 0 else "") + f"{t['pnl_pct']}%",
        })
    markers.sort(key=lambda m: m["time"])
    return markers
