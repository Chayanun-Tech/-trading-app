"""Lightweight mathematical edge model for live bot filtering.

The model is intentionally dependency-light: a JSON logistic model trained by
`backend/scripts/train_math_model.py`. It does not replace risk management or
the existing school consensus. Instead, it adds a learned probability gate.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from app.indicators import compute_indicators
from app.schemas import Candle


MODEL_DIR = Path(__file__).resolve().parents[2] / "models"
DEFAULT_MODEL_PATH = MODEL_DIR / "math_edge_model.json"

FEATURE_NAMES = [
    "ret_1",
    "ret_3",
    "ret_12",
    "ret_24",
    "vol_12",
    "vol_24",
    "range_pct",
    "body_pct",
    "close_pos",
    "ema20_dist",
    "ema50_dist",
    "sma200_dist",
    "rsi_scaled",
    "macd_scaled",
    "macd_hist_scaled",
    "volume_z20",
]


def _safe_log_return(closes: list[float], i: int, lag: int) -> float:
    if i - lag < 0 or closes[i - lag] <= 0 or closes[i] <= 0:
        return 0.0
    return math.log(closes[i] / closes[i - lag])


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    var = sum((x - mean) ** 2 for x in values) / len(values)
    return math.sqrt(var)


def _rolling_returns(closes: list[float], end_i: int, window: int) -> list[float]:
    start = max(1, end_i - window + 1)
    out: list[float] = []
    for i in range(start, end_i + 1):
        if closes[i - 1] > 0 and closes[i] > 0:
            out.append(math.log(closes[i] / closes[i - 1]))
    return out


def _value(series: list[Any], i: int, default: float = 0.0) -> float:
    try:
        value = series[i]
    except (IndexError, TypeError):
        return default
    if value is None:
        return default
    return float(value)


def features_at(
    candles: list[Candle],
    i: int | None = None,
    indicators: dict[str, Any] | None = None,
) -> dict[str, float] | None:
    """Build model features for candle index `i` using data up to that candle."""
    if not candles:
        return None
    if i is None:
        i = len(candles) - 1
    if i < 220 or i >= len(candles):
        return None

    closes = [float(c.close) for c in candles]
    highs = [float(c.high) for c in candles]
    lows = [float(c.low) for c in candles]
    volumes = [float(c.volume) for c in candles]
    c = candles[i]
    close = float(c.close)
    if close <= 0:
        return None

    ind = indicators or compute_indicators(candles)
    series = ind.get("series", {})
    ema20 = _value(series.get("ema20", []), i)
    ema50 = _value(series.get("ema50", []), i)
    sma200 = _value(series.get("sma200", []), i)
    rsi = _value(series.get("rsi14", []), i, 50.0)
    macd = _value(series.get("macd", []), i)
    macd_sig = _value(series.get("macd_signal", []), i)

    ret12 = _rolling_returns(closes, i, 12)
    ret24 = _rolling_returns(closes, i, 24)
    vol_window = volumes[max(0, i - 19): i + 1]
    vol_mean = sum(vol_window) / len(vol_window) if vol_window else 0.0
    vol_std = _std(vol_window)
    candle_range = max(float(c.high) - float(c.low), 0.0)

    return {
        "ret_1": _safe_log_return(closes, i, 1),
        "ret_3": _safe_log_return(closes, i, 3),
        "ret_12": _safe_log_return(closes, i, 12),
        "ret_24": _safe_log_return(closes, i, 24),
        "vol_12": _std(ret12),
        "vol_24": _std(ret24),
        "range_pct": candle_range / close,
        "body_pct": (float(c.close) - float(c.open)) / close,
        "close_pos": ((float(c.close) - float(c.low)) / candle_range - 0.5) if candle_range else 0.0,
        "ema20_dist": (close - ema20) / close if ema20 else 0.0,
        "ema50_dist": (close - ema50) / close if ema50 else 0.0,
        "sma200_dist": (close - sma200) / close if sma200 else 0.0,
        "rsi_scaled": (rsi - 50.0) / 50.0,
        "macd_scaled": macd / close,
        "macd_hist_scaled": (macd - macd_sig) / close,
        "volume_z20": ((volumes[i] - vol_mean) / vol_std) if vol_std else 0.0,
    }


def sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def load_model(path: Path | str = DEFAULT_MODEL_PATH) -> dict[str, Any] | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def predict_from_features(features: dict[str, float], model: dict[str, Any]) -> dict[str, Any]:
    names = model.get("feature_names") or FEATURE_NAMES
    means = model.get("means") or {}
    scales = model.get("scales") or {}
    weights = model.get("weights") or {}
    score = float(model.get("intercept", 0.0))
    contributions: list[dict[str, float]] = []
    for name in names:
        raw = float(features.get(name, 0.0))
        scale = float(scales.get(name) or 1.0)
        z = (raw - float(means.get(name, 0.0))) / scale
        w = float(weights.get(name, 0.0))
        score += w * z
        contributions.append({"feature": name, "value": round(raw, 8), "z": round(z, 4), "weight": round(w, 4)})
    prob = sigmoid(score)
    return {
        "prob_up": round(prob, 4),
        "prob_down": round(1.0 - prob, 4),
        "score": round(score, 4),
        "model_id": model.get("model_id"),
        "trained_at": model.get("trained_at"),
        "feature_contributions": contributions,
    }


def predict(candles: list[Candle], model_path: Path | str = DEFAULT_MODEL_PATH) -> dict[str, Any] | None:
    model = load_model(model_path)
    if not model:
        return None
    features = features_at(candles)
    if not features:
        return None
    result = predict_from_features(features, model)
    result["features_ready"] = True
    result["model_path"] = str(Path(model_path))
    return result
