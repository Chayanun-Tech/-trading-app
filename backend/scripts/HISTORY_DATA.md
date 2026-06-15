# Historical Training Data

Use `backend/scripts/download_history.py` to build local OHLCV datasets for bot training.

Output files are written to `data/candles/` and are intentionally ignored by Git.

CSV schema:

```text
symbol,timeframe,time,open,high,low,close,volume,source
```

## Bitkub THB Pairs

Small test:

```powershell
.\.venv\Scripts\python.exe backend\scripts\download_history.py --source bitkub --symbols ETH_THB --timeframes 1h --start 2026-06-01 --end 2026-06-03
```

Large download:

```powershell
.\.venv\Scripts\python.exe backend\scripts\download_history.py --source bitkub --symbols BTC_THB ETH_THB SOL_THB --timeframes 1m 5m 15m 1h --start 2024-01-01 --end 2026-06-16 --chunk-days 7
```

## Binance Global Crypto

Small test:

```powershell
.\.venv\Scripts\python.exe backend\scripts\download_history.py --source binance --symbols ETHUSDT --timeframes 1h --start 2024-01-01 --end 2024-01-31
```

Large download:

```powershell
.\.venv\Scripts\python.exe backend\scripts\download_history.py --source binance --symbols BTCUSDT ETHUSDT SOLUSDT --timeframes 1m 5m 15m 1h 4h 1d --start 2021-01-01 --end 2026-06-16
```

For training, start with Binance for broad history, then fine-tune/test on Bitkub THB pairs.

Notes:

- Binance spot public data uses microsecond timestamps from 2025 onward; the downloader normalizes both millisecond and microsecond rows to Unix seconds.
- Binance monthly ZIPs may not exist for the current in-progress month yet. Rerun the same command later; existing CSV rows are merged by timestamp and will not duplicate.
- For very large 1m downloads, run one source/symbol at a time and let it finish. The downloader can be rerun safely if interrupted.
