"""Audit annual-filing business extraction without calling an LLM.

Examples:
    python backend/scripts/audit_annual_filings.py --only AAPL,MSFT,TSM,RY
    python backend/scripts/audit_annual_filings.py --tickers-file data/financials/index_tickers.txt --limit 100
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app import edgar  # noqa: E402


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default="", help="Comma-separated symbols")
    parser.add_argument("--tickers-file", default="", help="One symbol per line")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--sample", type=int, default=0, help="Deterministic random sample size")
    parser.add_argument("--seed", type=int, default=20260621)
    parser.add_argument("--delay", type=float, default=0.15)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument(
        "--output",
        default=str(ROOT / "data" / "financials" / "annual_filing_audit.json"),
    )
    args = parser.parse_args()

    if args.only:
        symbols = [s.strip().upper() for s in args.only.split(",") if s.strip()]
    elif args.tickers_file:
        symbols = [
            line.strip().upper()
            for line in Path(args.tickers_file).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
    else:
        symbols = sorted((await edgar._load_ticker_map()).keys())
    current_symbols = set((await edgar._load_ticker_map()).keys())
    stale_symbols = sorted(set(symbols) - current_symbols)
    symbols = [symbol for symbol in symbols if symbol in current_symbols]
    if stale_symbols:
        print(f"Skipped stale/delisted tickers: {len(stale_symbols)}")
    if args.sample and args.sample < len(symbols):
        symbols = sorted(random.Random(args.seed).sample(symbols, args.sample))
    if args.limit:
        symbols = symbols[:args.limit]

    rows = []
    started = time.time()
    for index, symbol in enumerate(symbols, 1):
        try:
            context = await edgar.get_10k_context(symbol, force_refresh=args.refresh)
            row = {
                "symbol": symbol,
                "report_type": (context or {}).get("report_type"),
                "filing_date": (context or {}).get("filing_date"),
                "business_chars": len((context or {}).get("business") or ""),
                "risk_chars": len((context or {}).get("risk_factors") or ""),
                "mda_chars": len((context or {}).get("mda") or ""),
                "status": "ok" if (context or {}).get("business") else "missing_business",
            }
        except Exception as exc:  # noqa: BLE001
            row = {"symbol": symbol, "status": "error", "error": str(exc)[:240]}
        rows.append(row)
        print(
            f"[{index}/{len(symbols)}] {symbol:<9} {row['status']:<16} "
            f"{row.get('report_type') or '-':<4} business={row.get('business_chars', 0)}"
        )
        await asyncio.sleep(max(args.delay, 0))

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "total": len(rows),
        "ok": sum(row["status"] == "ok" for row in rows),
        "missing_business": sum(row["status"] == "missing_business" for row in rows),
        "errors": sum(row["status"] == "error" for row in rows),
        "elapsed_seconds": round(time.time() - started, 1),
        "stale_or_delisted_skipped": len(stale_symbols),
        "rows": rows,
    }
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, indent=2))
    print(f"Report: {output}")


if __name__ == "__main__":
    asyncio.run(main())
