"""Model factory pipeline: triple-barrier dataset -> walk-forward validation ->
cost-aware deploy gate -> versioned logistic model.

Pure-Python (matches the rest of the stack; no numpy/pandas/sklearn).
Reuses the SAME feature code as the live app (``app.math_model.features_at``) to
avoid training-serving skew. See ``training/DESIGN.md``.

Example:
    .\\.venv\\Scripts\\python.exe training\\train_pipeline.py --timeframes 1h
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.indicators import compute_indicators  # noqa: E402
from app.math_model import FEATURE_NAMES, features_at, sigmoid  # noqa: E402
from app.schemas import Candle  # noqa: E402

from labels import atr_series, label_entry  # noqa: E402


DATA_ROOT = ROOT / "data" / "candles"
REGISTRY_DIR = ROOT / "models" / "registry"


@dataclass
class Example:
    time: int
    x: list[float]
    label: int
    gross_return: float


def read_candles(path: Path) -> list[Candle]:
    out: list[Candle] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            try:
                out.append(Candle(
                    time=int(row["time"]), open=float(row["open"]), high=float(row["high"]),
                    low=float(row["low"]), close=float(row["close"]), volume=float(row["volume"]),
                ))
            except (KeyError, TypeError, ValueError):
                continue
    out.sort(key=lambda c: c.time)
    return out


def primary_signal(feats: dict, name: str) -> bool:
    """Rule-based primary filter = structured long candidates (meta-labeling).
    Operates on the SAME features the model sees, so it reuses computed series."""
    if name == "none":
        return True
    rsi = feats["rsi_scaled"]              # (rsi-50)/50
    sma200 = feats["sma200_dist"]          # (close-sma200)/close
    ema20 = feats["ema20_dist"]
    macd_h = feats["macd_hist_scaled"]
    macd = feats["macd_scaled"]
    close_pos = feats["close_pos"]
    volz = feats["volume_z20"]
    if name == "trend_pullback":           # ย่อตัวในเทรนด์ขึ้น แล้วเริ่มกลับ
        return sma200 > 0 and rsi < -0.1 and macd_h > 0
    if name == "oversold":                 # RSI ต่ำมาก (mean reversion)
        return rsi < -0.30
    if name == "breakout":                 # ปิดใกล้ไฮ + โมเมนตัม + วอลุ่ม
        return ema20 > 0 and macd > 0 and close_pos > 0.25 and volz > 0.5
    if name == "trend_follow":             # เทรนด์ขึ้นชัด โมเมนตัมบวก
        return sma200 > 0 and ema20 > 0 and macd_h > 0
    raise SystemExit(f"unknown primary: {name}")


def build_examples(candles: list[Candle], *, tp, sl, hold, stride, max_per_file,
                   primary="none", tail_bars=0) -> list[Example]:
    if tail_bars and len(candles) > tail_bars:
        candles = candles[-tail_bars:]
    n = len(candles)
    if n < 260:
        return []
    indicators = compute_indicators(candles)
    atr = atr_series(candles)
    last_i = n - hold - 1
    if last_i <= 220:
        return []
    # When a primary filter is active, scan every bar (candidates are already sparse).
    if primary != "none":
        step = 1
    else:
        step = max(1, (last_i - 220) // max_per_file) if max_per_file > 0 else 1
        step = max(step, stride)
    out: list[Example] = []
    for i in range(220, last_i + 1, step):
        feats = features_at(candles, i, indicators)
        if not feats:
            continue
        if not primary_signal(feats, primary):
            continue
        outcome = label_entry(candles, i, atr, tp_atr_mult=tp, sl_atr_mult=sl, max_hold_bars=hold)
        if outcome is None:
            continue
        out.append(Example(
            time=int(candles[i].time),
            x=[float(feats.get(name, 0.0)) for name in FEATURE_NAMES],
            label=outcome.label,
            gross_return=outcome.gross_return,
        ))
        if max_per_file > 0 and len(out) >= max_per_file:
            break
    return out


def standardize_fit(rows: list[Example]):
    cols = list(zip(*(e.x for e in rows)))
    means = [statistics.fmean(c) for c in cols]
    scales = []
    for c in cols:
        s = statistics.pstdev(c) if len(c) > 1 else 1.0
        scales.append(s if s > 1e-12 else 1.0)
    return means, scales


def apply_std(x, means, scales):
    return [(x[i] - means[i]) / scales[i] for i in range(len(x))]


def train_logistic(rows, means, scales, *, epochs, lr, l2, seed):
    data = [(apply_std(e.x, means, scales), e.label) for e in rows]
    random.Random(seed).shuffle(data)
    w = [0.0] * len(FEATURE_NAMES)
    b = 0.0
    n = max(1, len(data))
    for _ in range(epochs):
        gw = [0.0] * len(w)
        gb = 0.0
        for x, y in data:
            p = sigmoid(b + sum(wi * xi for wi, xi in zip(w, x)))
            err = p - y
            gb += err
            for j, xi in enumerate(x):
                gw[j] += err * xi
        b -= lr * gb / n
        for j in range(len(w)):
            w[j] -= lr * (gw[j] / n + l2 * w[j])
    return w, b


def prob(e: Example, means, scales, w, b) -> float:
    x = apply_std(e.x, means, scales)
    return sigmoid(b + sum(wi * xi for wi, xi in zip(w, x)))


def evaluate_fold(test, means, scales, w, b, *, threshold, cost_pct) -> dict:
    cost = cost_pct / 100.0
    taken = [(prob(e, means, scales, w, b), e) for e in test]
    taken = [(p, e) for p, e in taken if p >= threshold]
    if not taken:
        return {"trades": 0, "precision": 0.0, "expectancy": 0.0, "profit_factor": 0.0}
    wins = sum(1 for _, e in taken if e.label == 1)
    nets = [e.gross_return - cost for _, e in taken]
    gains = sum(r for r in nets if r > 0)
    losses = -sum(r for r in nets if r < 0)
    return {
        "trades": len(taken),
        "precision": round(wins / len(taken), 4),
        "expectancy": round(statistics.fmean(nets), 6),
        "profit_factor": round(gains / losses, 3) if losses > 0 else float("inf"),
    }


def walk_forward(examples, *, folds, embargo_sec, thresholds, cost_pct, epochs, lr, l2, seed) -> dict:
    """Train once per fold, then evaluate across every threshold. Returns
    {threshold: [per-fold metrics]}."""
    examples.sort(key=lambda e: e.time)
    n = len(examples)
    seg = n // (folds + 1)
    by_threshold: dict[float, list[dict]] = {t: [] for t in thresholds}
    for k in range(1, folds + 1):
        test_lo = k * seg
        test_hi = (k + 1) * seg if k < folds else n
        test = examples[test_lo:test_hi]
        if not test:
            continue
        test_start_time = test[0].time
        train = [e for e in examples[:test_lo] if e.time <= test_start_time - embargo_sec]
        if len(train) < 500 or len(test) < 50:
            continue
        means, scales = standardize_fit(train)
        w, b = train_logistic(train, means, scales, epochs=epochs, lr=lr, l2=l2, seed=seed)
        for t in thresholds:
            m = evaluate_fold(test, means, scales, w, b, threshold=t, cost_pct=cost_pct)
            m["fold"] = k
            by_threshold[t].append(m)
        print(f"[fold {k}] train={len(train)} test={len(test)} done", flush=True)
    return by_threshold


def median(vals):
    vals = [v for v in vals if v != float("inf")]
    return round(statistics.median(vals), 4) if vals else 0.0


def main() -> None:
    ap = argparse.ArgumentParser(description="Walk-forward triple-barrier model trainer.")
    ap.add_argument("--data-root", default=str(DATA_ROOT))
    ap.add_argument("--timeframes", nargs="+", default=["1h"])
    ap.add_argument("--tp-atr-mult", type=float, default=1.5)
    ap.add_argument("--sl-atr-mult", type=float, default=1.0)
    ap.add_argument("--max-hold-bars", type=int, default=24)
    ap.add_argument("--primary", default="none",
                    choices=["none", "trend_pullback", "oversold", "breakout", "trend_follow"],
                    help="Primary signal filter = structured candidate entries (meta-labeling)")
    ap.add_argument("--tail-bars", type=int, default=0, help="Use only last N bars per file (0=all)")
    ap.add_argument("--threshold-scan", nargs="+", type=float,
                    default=[0.45, 0.50, 0.55, 0.60, 0.65],
                    help="Probability thresholds to evaluate (precision/trades trade-off)")
    ap.add_argument("--cost-pct", type=float, default=0.6, help="Round-trip fee+slippage %")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--embargo-bars", type=int, default=24)
    ap.add_argument("--tf-seconds", type=int, default=3600, help="Seconds per bar (for embargo)")
    ap.add_argument("--stride", type=int, default=1)
    ap.add_argument("--max-examples-per-file", type=int, default=4000)
    ap.add_argument("--epochs", type=int, default=200)
    ap.add_argument("--lr", type=float, default=0.15)
    ap.add_argument("--l2", type=float, default=0.003)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--promote", action="store_true", help="Copy to production model if gate passes")
    # Gate thresholds
    ap.add_argument("--gate-precision", type=float, default=0.65)
    ap.add_argument("--gate-profit-factor", type=float, default=1.3)
    ap.add_argument("--gate-trades", type=int, default=30)
    args = ap.parse_args()

    data_root = Path(args.data_root)
    paths = sorted(p for p in data_root.glob("**/*.csv") if p.stem in set(args.timeframes))
    if not paths:
        raise SystemExit(f"No CSVs for timeframes {args.timeframes} under {data_root}")

    examples: list[Example] = []
    sources = []
    for p in paths:
        candles = read_candles(p)
        ex = build_examples(candles, tp=args.tp_atr_mult, sl=args.sl_atr_mult,
                            hold=args.max_hold_bars, stride=args.stride,
                            max_per_file=args.max_examples_per_file,
                            primary=args.primary, tail_bars=args.tail_bars)
        examples.extend(ex)
        rel = p.relative_to(ROOT).as_posix()
        wins = sum(e.label for e in ex)
        sources.append({"path": rel, "examples": len(ex), "win_rate_raw": round(wins / len(ex), 4) if ex else 0})
        print(f"[data] {rel} examples={len(ex)} raw_win_rate={(wins/len(ex)) if ex else 0:.3f}", flush=True)

    if len(examples) < 1000:
        raise SystemExit(f"Not enough examples: {len(examples)}")

    base_win = sum(e.label for e in examples) / len(examples)
    print(f"\n[dataset] total={len(examples)} baseline_win_rate(all entries)={base_win:.3f}", flush=True)
    print(f"[config] primary={args.primary} tp={args.tp_atr_mult}xATR sl={args.sl_atr_mult}xATR "
          f"hold={args.max_hold_bars} cost={args.cost_pct}% scan={args.threshold_scan}\n", flush=True)

    embargo_sec = args.embargo_bars * args.tf_seconds
    by_threshold = walk_forward(examples, folds=args.folds, embargo_sec=embargo_sec,
                                thresholds=args.threshold_scan, cost_pct=args.cost_pct,
                                epochs=args.epochs, lr=args.lr, l2=args.l2, seed=args.seed)
    if not any(by_threshold.values()):
        raise SystemExit("No valid folds (not enough data per fold).")

    # Median across folds, per threshold
    print(f"\n=== WALK-FORWARD MEDIAN (net of {args.cost_pct}% cost) ===")
    print(f"{'thresh':>7} {'trades/fold':>12} {'precision':>10} {'expectancy':>12} {'profit_factor':>14}")
    scan = {}
    for t in args.threshold_scan:
        fs = by_threshold.get(t, [])
        if not fs:
            continue
        med = {
            "precision": median([f["precision"] for f in fs]),
            "expectancy": round(statistics.median([f["expectancy"] for f in fs]), 6),
            "profit_factor": median([f["profit_factor"] for f in fs]),
            "trades_per_fold": int(statistics.median([f["trades"] for f in fs])),
        }
        scan[t] = med
        print(f"{t:>7.2f} {med['trades_per_fold']:>12} {med['precision']:>10.4f} "
              f"{med['expectancy']:>+12.5f} {med['profit_factor']:>14}")

    # Pick best threshold that clears the gate (lowest threshold meeting all criteria)
    chosen = None
    for t in sorted(scan):
        m = scan[t]
        if (m["precision"] >= args.gate_precision and m["profit_factor"] >= args.gate_profit_factor
                and m["trades_per_fold"] >= args.gate_trades and m["expectancy"] > 0):
            chosen = t
            break
    passed = chosen is not None
    print(f"\nGATE: precision>={args.gate_precision}, pf>={args.gate_profit_factor}, "
          f"trades>={args.gate_trades}, expectancy>0")
    print(f"GATE RESULT: {'PASS @ threshold ' + str(chosen) if passed else 'FAIL (no threshold clears gate)'}\n")
    threshold = chosen if chosen is not None else args.threshold_scan[-1]

    # Train final model on ALL data for the artifact
    means, scales = standardize_fit(examples)
    w, b = train_logistic(examples, means, scales, epochs=args.epochs, lr=args.lr, l2=args.l2, seed=args.seed)
    model = {
        "model_id": f"meta_triplebarrier_logistic_{int(time.time())}",
        "model_type": "standardized_logistic_regression",
        "trained_at": int(time.time()),
        "feature_names": FEATURE_NAMES,
        "means": {n: means[i] for i, n in enumerate(FEATURE_NAMES)},
        "scales": {n: scales[i] for i, n in enumerate(FEATURE_NAMES)},
        "weights": {n: w[i] for i, n in enumerate(FEATURE_NAMES)},
        "intercept": b,
        "label": {
            "method": "triple_barrier",
            "tp_atr_mult": args.tp_atr_mult, "sl_atr_mult": args.sl_atr_mult,
            "max_hold_bars": args.max_hold_bars,
        },
        "meta": {"threshold": threshold, "primary_signal": args.primary},
        "validation": {"scheme": "walk_forward", "scan": scan, "chosen_threshold": chosen, "gate_passed": passed},
        "costs": {"round_trip_pct": args.cost_pct},
        "sources": sources,
        "disclaimer": "Research model. Paper-trade and verify before risking capital.",
    }
    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    tag = "+".join(args.timeframes)
    out = REGISTRY_DIR / f"{model['trained_at']}_{tag}_triplebarrier.json"
    out.write_text(json.dumps(model, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[saved] {out.relative_to(ROOT).as_posix()}")

    if args.promote:
        if passed:
            prod = ROOT / "models" / "math_edge_model.json"
            prod.write_text(json.dumps(model, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"[promoted] -> {prod.relative_to(ROOT).as_posix()} (gate passed)")
        else:
            print("[promote skipped] gate failed — production model unchanged.")


if __name__ == "__main__":
    main()
