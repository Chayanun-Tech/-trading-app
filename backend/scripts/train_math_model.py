"""Train the lightweight math edge model from local candle CSV datasets."""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.indicators import compute_indicators  # noqa: E402
from app.math_model import DEFAULT_MODEL_PATH, FEATURE_NAMES, features_at, sigmoid  # noqa: E402
from app.schemas import Candle  # noqa: E402


def display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix().encode("ascii", "backslashreplace").decode("ascii")


def read_candles(path: Path) -> list[Candle]:
    candles: list[Candle] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                candles.append(Candle(
                    time=int(row["time"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                ))
            except (KeyError, TypeError, ValueError):
                continue
    candles.sort(key=lambda c: c.time)
    return candles


def build_examples(
    candles: list[Candle],
    *,
    horizon: int,
    min_return: float,
    max_examples_per_file: int,
) -> list[tuple[list[float], int]]:
    examples: list[tuple[list[float], int]] = []
    last_i = len(candles) - horizon - 1
    if last_i <= 220:
        return examples
    indicators = compute_indicators(candles)
    stride = max(1, (last_i - 220) // max_examples_per_file) if max_examples_per_file > 0 else 1
    for i in range(220, last_i + 1, stride):
        f = features_at(candles, i, indicators)
        if not f:
            continue
        future_ret = (candles[i + horizon].close / candles[i].close) - 1.0
        if abs(future_ret) < min_return:
            continue
        label = 1 if future_ret > 0 else 0
        examples.append(([float(f.get(name, 0.0)) for name in FEATURE_NAMES], label))
    return examples


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def std(values: list[float]) -> float:
    if len(values) < 2:
        return 1.0
    m = mean(values)
    v = sum((x - m) ** 2 for x in values) / len(values)
    s = math.sqrt(v)
    return s if s > 1e-12 else 1.0


def standardize(examples: list[tuple[list[float], int]]) -> tuple[list[tuple[list[float], int]], dict, dict]:
    cols = list(zip(*(x for x, _ in examples)))
    means = {name: mean(list(cols[i])) for i, name in enumerate(FEATURE_NAMES)}
    scales = {name: std(list(cols[i])) for i, name in enumerate(FEATURE_NAMES)}
    out = []
    for x, y in examples:
        z = [(x[i] - means[FEATURE_NAMES[i]]) / scales[FEATURE_NAMES[i]] for i in range(len(FEATURE_NAMES))]
        out.append((z, y))
    return out, means, scales


def train_logistic(
    examples: list[tuple[list[float], int]],
    *,
    epochs: int,
    lr: float,
    l2: float,
    seed: int,
) -> tuple[list[float], float]:
    random.Random(seed).shuffle(examples)
    weights = [0.0] * len(FEATURE_NAMES)
    intercept = 0.0
    n = max(1, len(examples))
    for epoch in range(epochs):
        grad_w = [0.0] * len(weights)
        grad_b = 0.0
        loss = 0.0
        for x, y in examples:
            score = intercept + sum(w * xi for w, xi in zip(weights, x))
            p = sigmoid(score)
            err = p - y
            grad_b += err
            for j, xi in enumerate(x):
                grad_w[j] += err * xi
            loss += -(y * math.log(max(p, 1e-9)) + (1 - y) * math.log(max(1 - p, 1e-9)))
        intercept -= lr * grad_b / n
        for j in range(len(weights)):
            grad = grad_w[j] / n + l2 * weights[j]
            weights[j] -= lr * grad
        if epoch in {0, epochs - 1} or (epoch + 1) % 50 == 0:
            print(f"[train] epoch={epoch + 1} loss={loss / n:.5f}", flush=True)
    return weights, intercept


def evaluate(examples: list[tuple[list[float], int]], weights: list[float], intercept: float) -> dict:
    if not examples:
        return {"count": 0}
    preds = []
    for x, y in examples:
        p = sigmoid(intercept + sum(w * xi for w, xi in zip(weights, x)))
        preds.append((p, y))
    correct = sum(1 for p, y in preds if (p >= 0.5) == bool(y))
    positives = sum(1 for _, y in preds if y == 1)
    high_conf = [(p, y) for p, y in preds if p >= 0.55 or p <= 0.45]
    high_correct = sum(1 for p, y in high_conf if (p >= 0.5) == bool(y))
    return {
        "count": len(preds),
        "accuracy": round(correct / len(preds), 4),
        "positive_rate": round(positives / len(preds), 4),
        "avg_prob_up": round(sum(p for p, _ in preds) / len(preds), 4),
        "high_conf_count": len(high_conf),
        "high_conf_accuracy": round(high_correct / len(high_conf), 4) if high_conf else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Train math edge logistic model from candle CSV files.")
    parser.add_argument("--data-root", default=str(ROOT / "data" / "candles"))
    parser.add_argument("--patterns", nargs="+", default=["**/*.csv"])
    parser.add_argument("--out", default=str(DEFAULT_MODEL_PATH))
    parser.add_argument("--horizon", type=int, default=6, help="Future bars used for label")
    parser.add_argument("--min-return", type=float, default=0.0015, help="Ignore tiny future moves")
    parser.add_argument("--max-examples-per-file", type=int, default=5000)
    parser.add_argument("--tail-bars", type=int, default=12000, help="Use only the latest N candles per file; 0 = all")
    parser.add_argument("--epochs", type=int, default=250)
    parser.add_argument("--lr", type=float, default=0.12)
    parser.add_argument("--l2", type=float, default=0.003)
    parser.add_argument("--test-ratio", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    data_root = Path(args.data_root).resolve()
    paths: list[Path] = []
    for pattern in args.patterns:
        paths.extend(data_root.glob(pattern))
    paths = sorted({p for p in paths if p.is_file() and p.suffix.lower() == ".csv"})
    if not paths:
        raise SystemExit(f"No CSV files found under {data_root}")

    all_examples: list[tuple[list[float], int]] = []
    sources = []
    for path in paths:
        candles = read_candles(path)
        if args.tail_bars and len(candles) > args.tail_bars:
            candles = candles[-args.tail_bars:]
        examples = build_examples(
            candles,
            horizon=args.horizon,
            min_return=args.min_return,
            max_examples_per_file=args.max_examples_per_file,
        )
        all_examples.extend(examples)
        rel = display_path(path)
        sources.append({"path": rel, "candles": len(candles), "examples": len(examples)})
        print(f"[data] {rel} candles={len(candles)} examples={len(examples)}", flush=True)

    if len(all_examples) < 500:
        raise SystemExit(f"Not enough examples to train: {len(all_examples)}")

    rng = random.Random(args.seed)
    rng.shuffle(all_examples)
    split = max(1, int(len(all_examples) * (1 - args.test_ratio)))
    raw_train = all_examples[:split]
    raw_test = all_examples[split:]
    train, means, scales = standardize(raw_train)

    test = []
    for x, y in raw_test:
        z = [(x[i] - means[FEATURE_NAMES[i]]) / scales[FEATURE_NAMES[i]] for i in range(len(FEATURE_NAMES))]
        test.append((z, y))

    weights, intercept = train_logistic(train, epochs=args.epochs, lr=args.lr, l2=args.l2, seed=args.seed)
    train_metrics = evaluate(train, weights, intercept)
    test_metrics = evaluate(test, weights, intercept)

    model = {
        "model_id": f"math_edge_logistic_{int(time.time())}",
        "model_type": "standardized_logistic_regression",
        "trained_at": int(time.time()),
        "feature_names": FEATURE_NAMES,
        "means": {name: means[name] for name in FEATURE_NAMES},
        "scales": {name: scales[name] for name in FEATURE_NAMES},
        "weights": {name: weights[i] for i, name in enumerate(FEATURE_NAMES)},
        "intercept": intercept,
        "label": {
            "horizon_bars": args.horizon,
            "min_abs_return": args.min_return,
            "positive": "future close after horizon is above current close by min_return",
        },
        "metrics": {"train": train_metrics, "test": test_metrics},
        "sources": sources,
        "disclaimer": "Research model only. Use with paper trading and risk controls; historical accuracy does not guarantee future returns.",
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(model, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[saved] {display_path(out)}")
    print(json.dumps(model["metrics"], indent=2))


if __name__ == "__main__":
    main()
