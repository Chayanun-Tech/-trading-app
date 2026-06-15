"""Download large OHLCV history datasets for bot training.

Sources:
- bitkub: public TradingView history endpoint for *_THB pairs.
- binance: public monthly spot kline ZIP files from data.binance.vision.

Output CSV schema:
symbol,timeframe,time,open,high,low,close,volume,source
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import time
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

import httpx


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "data" / "candles"
CSV_HEADER = ["symbol", "timeframe", "time", "open", "high", "low", "close", "volume", "source"]

BITKUB_RESOLUTION = {
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "1h": "60",
    "4h": "240",
    "1d": "1D",
}
TIMEFRAME_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}
BINANCE_INTERVALS = set(TIMEFRAME_SECONDS)


@dataclass(frozen=True)
class CandleRow:
    symbol: str
    timeframe: str
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    source: str

    def as_csv_row(self) -> list[str]:
        return [
            self.symbol,
            self.timeframe,
            str(self.time),
            f"{self.open:.12g}",
            f"{self.high:.12g}",
            f"{self.low:.12g}",
            f"{self.close:.12g}",
            f"{self.volume:.12g}",
            self.source,
        ]


def parse_day(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def day_start_ts(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())


def month_starts(start: date, end: date) -> Iterable[date]:
    cur = date(start.year, start.month, 1)
    stop = date(end.year, end.month, 1)
    while cur <= stop:
        yield cur
        if cur.month == 12:
            cur = date(cur.year + 1, 1, 1)
        else:
            cur = date(cur.year, cur.month + 1, 1)


def normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper().replace("/", "_").replace("-", "_")


def binance_epoch_to_seconds(value: str) -> int:
    raw = int(value)
    # Binance spot public data changed from milliseconds to microseconds in 2025.
    if raw >= 1_000_000_000_000_000:
        return raw // 1_000_000
    return raw // 1000


def output_path(out_dir: Path, source: str, symbol: str, timeframe: str) -> Path:
    return out_dir / source / normalize_symbol(symbol) / f"{timeframe}.csv"


def display_path(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix().encode("ascii", "backslashreplace").decode("ascii")


def read_existing(path: Path) -> dict[int, CandleRow]:
    if not path.exists():
        return {}
    rows: dict[int, CandleRow] = {}
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                item = CandleRow(
                    symbol=row["symbol"],
                    timeframe=row["timeframe"],
                    time=int(row["time"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                    source=row.get("source") or "unknown",
                )
            except (KeyError, TypeError, ValueError):
                continue
            rows[item.time] = item
    return rows


def write_merged(path: Path, new_rows: list[CandleRow]) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = read_existing(path)
    before = len(merged)
    for row in new_rows:
        merged[row.time] = row
    ordered = [merged[t] for t in sorted(merged)]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_HEADER)
        writer.writerows(row.as_csv_row() for row in ordered)

    meta = {
        "path": str(path),
        "rows_before": before,
        "rows_added_or_updated": len(new_rows),
        "rows_total": len(ordered),
        "first_time": ordered[0].time if ordered else None,
        "last_time": ordered[-1].time if ordered else None,
        "updated_at": int(time.time()),
    }
    meta_path = path.with_suffix(".meta.json")
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return meta


async def fetch_bitkub(
    client: httpx.AsyncClient,
    symbol: str,
    timeframe: str,
    start: date,
    end: date,
    chunk_days: int,
) -> list[CandleRow]:
    if timeframe not in BITKUB_RESOLUTION:
        raise ValueError(f"Bitkub timeframe not supported: {timeframe}")
    norm = normalize_symbol(symbol)
    start_ts = day_start_ts(start)
    end_ts = day_start_ts(end) + 86399
    chunk_seconds = max(chunk_days, 1) * 86400
    rows: dict[int, CandleRow] = {}
    cur = start_ts
    while cur <= end_ts:
        to_ts = min(cur + chunk_seconds - 1, end_ts)
        params = {
            "symbol": norm,
            "resolution": BITKUB_RESOLUTION[timeframe],
            "from": cur,
            "to": to_ts,
        }
        res = await client.get("https://api.bitkub.com/tradingview/history", params=params)
        res.raise_for_status()
        payload = res.json()
        if payload.get("s") not in {"ok", "no_data"}:
            raise RuntimeError(f"Bitkub returned {payload}")
        times = payload.get("t") or []
        opens = payload.get("o") or []
        highs = payload.get("h") or []
        lows = payload.get("l") or []
        closes = payload.get("c") or []
        volumes = payload.get("v") or [0] * len(times)
        for i, ts in enumerate(times):
            try:
                row = CandleRow(
                    symbol=norm,
                    timeframe=timeframe,
                    time=int(ts),
                    open=float(opens[i]),
                    high=float(highs[i]),
                    low=float(lows[i]),
                    close=float(closes[i]),
                    volume=float(volumes[i] or 0),
                    source="bitkub",
                )
            except (IndexError, TypeError, ValueError):
                continue
            rows[row.time] = row
        print(f"[bitkub] {norm} {timeframe} {datetime.utcfromtimestamp(cur).date()} -> {datetime.utcfromtimestamp(to_ts).date()} rows={len(times)}")
        cur = to_ts + 1
        await asyncio.sleep(0.15)
    return [rows[t] for t in sorted(rows)]


async def fetch_binance_month(
    client: httpx.AsyncClient,
    symbol: str,
    timeframe: str,
    month: date,
    start_ts: int,
    end_ts: int,
) -> list[CandleRow]:
    if timeframe not in BINANCE_INTERVALS:
        raise ValueError(f"Binance interval not supported: {timeframe}")
    norm = symbol.strip().upper().replace("-", "").replace("_", "")
    ym = month.strftime("%Y-%m")
    url = (
        "https://data.binance.vision/data/spot/monthly/klines/"
        f"{norm}/{timeframe}/{norm}-{timeframe}-{ym}.zip"
    )
    res = await client.get(url)
    if res.status_code == 404:
        print(f"[binance] missing {norm} {timeframe} {ym}")
        return []
    res.raise_for_status()
    rows: list[CandleRow] = []
    with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
        csv_name = next((name for name in zf.namelist() if name.endswith(".csv")), None)
        if not csv_name:
            return []
        with zf.open(csv_name) as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8")
            reader = csv.reader(text)
            for record in reader:
                if not record or not record[0].isdigit():
                    continue
                ts = binance_epoch_to_seconds(record[0])
                if ts < start_ts or ts > end_ts:
                    continue
                try:
                    rows.append(CandleRow(
                        symbol=norm,
                        timeframe=timeframe,
                        time=ts,
                        open=float(record[1]),
                        high=float(record[2]),
                        low=float(record[3]),
                        close=float(record[4]),
                        volume=float(record[5]),
                        source="binance",
                    ))
                except (IndexError, TypeError, ValueError):
                    continue
    print(f"[binance] {norm} {timeframe} {ym} rows={len(rows)}")
    return rows


async def fetch_binance(
    client: httpx.AsyncClient,
    symbol: str,
    timeframe: str,
    start: date,
    end: date,
) -> list[CandleRow]:
    start_ts = day_start_ts(start)
    end_ts = day_start_ts(end) + 86399
    rows: dict[int, CandleRow] = {}
    for month in month_starts(start, end):
        for row in await fetch_binance_month(client, symbol, timeframe, month, start_ts, end_ts):
            rows[row.time] = row
        await asyncio.sleep(0.05)
    return [rows[t] for t in sorted(rows)]


async def run(args: argparse.Namespace) -> None:
    start = parse_day(args.start)
    end = parse_day(args.end)
    if end < start:
        raise SystemExit("--end must be on or after --start")
    source = args.source.lower()
    out_dir = Path(args.out_dir)
    timeout = httpx.Timeout(args.timeout, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for raw_symbol in args.symbols:
            symbol = normalize_symbol(raw_symbol) if source == "bitkub" else raw_symbol.strip().upper()
            for timeframe in args.timeframes:
                if source == "bitkub":
                    rows = await fetch_bitkub(client, symbol, timeframe, start, end, args.chunk_days)
                elif source == "binance":
                    rows = await fetch_binance(client, symbol, timeframe, start, end)
                else:
                    raise SystemExit(f"Unsupported source: {source}")
                path = output_path(out_dir, source, symbol, timeframe)
                meta = write_merged(path, rows)
                print(
                    f"[saved] {display_path(path)} "
                    f"total={meta['rows_total']} "
                    f"added_or_updated={meta['rows_added_or_updated']}"
                )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download OHLCV history for training datasets.")
    parser.add_argument("--source", choices=["bitkub", "binance"], required=True)
    parser.add_argument("--symbols", nargs="+", required=True, help="Examples: ETH_THB BTC_THB or ETHUSDT BTCUSDT")
    parser.add_argument("--timeframes", nargs="+", default=["1h"], choices=sorted(TIMEFRAME_SECONDS))
    parser.add_argument("--start", required=True, help="UTC date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="UTC date YYYY-MM-DD")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    parser.add_argument("--chunk-days", type=int, default=14, help="Bitkub request window size in days")
    parser.add_argument("--timeout", type=float, default=60.0)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
