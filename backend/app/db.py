"""Optional Supabase/Postgres persistence.

The app still works without a database. Set DATABASE_URL to enable durable
storage for bot runs, events, orders, positions, and app memory.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any


def _json(data: Any) -> str:
    return json.dumps(data or {}, ensure_ascii=False, default=str)


def _iso(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _time_key(item: dict) -> int:
    return int(item.get("time") or item.get("started_at") or item.get("opened_at") or 0)


class DatabaseStore:
    def __init__(self, database_url: str = ""):
        self.database_url = database_url.strip()
        self.pool = None
        self.local_history_path = Path(__file__).resolve().parents[2] / "data" / "autotrade_history.json"

    @property
    def enabled(self) -> bool:
        return bool(self.database_url and self.pool)

    async def connect(self) -> None:
        if not self.database_url:
            return
        import asyncpg

        self.pool = await asyncpg.create_pool(self.database_url, min_size=1, max_size=5)

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()
            self.pool = None

    def _empty_local_history(self) -> dict:
        return {"runs": [], "events": [], "orders": [], "positions": []}

    def _load_local_history(self) -> dict:
        if not self.local_history_path.exists():
            return self._empty_local_history()
        try:
            data = json.loads(self.local_history_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._empty_local_history()
        out = self._empty_local_history()
        for key in out:
            if isinstance(data.get(key), list):
                out[key] = data[key]
        return out

    def _write_local_history(self, data: dict) -> None:
        self.local_history_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.local_history_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        tmp.replace(self.local_history_path)

    def _save_local_item(self, section: str, item: dict, *, key: str | None, upsert: bool) -> None:
        data = self._load_local_history()
        rows = data.setdefault(section, [])
        item = json.loads(json.dumps(item, ensure_ascii=False, default=str))
        if upsert and key and item.get(key):
            for i, row in enumerate(rows):
                if row.get(key) == item.get(key):
                    rows[i] = {**row, **item}
                    break
            else:
                rows.append(item)
        else:
            item.setdefault("id", int(time.time() * 1000))
            rows.append(item)

        limits = {"runs": 500, "events": 2000, "orders": 1000, "positions": 1000}
        rows.sort(key=_time_key, reverse=True)
        data[section] = rows[:limits.get(section, 1000)]
        self._write_local_history(data)

    async def list_autotrade_history(self, limit: int = 100) -> dict:
        limit = max(1, min(int(limit or 100), 500))
        if not self.enabled:
            data = self._load_local_history()
            return {
                "storage": "local_file",
                "durable": True,
                "path": str(self.local_history_path),
                "runs": sorted(data["runs"], key=_time_key, reverse=True)[:limit],
                "orders": sorted(data["orders"], key=_time_key, reverse=True)[:limit],
                "positions": sorted(data["positions"], key=_time_key, reverse=True)[:limit],
                "events": sorted(data["events"], key=_time_key, reverse=True)[:limit],
            }

        async with self.pool.acquire() as conn:
            runs = await conn.fetch(
                """
                select id::text, exchange, mode, symbol, timeframe, config, status,
                       extract(epoch from started_at)::bigint as started_at,
                       extract(epoch from stopped_at)::bigint as stopped_at,
                       realized_pnl_thb::float as realized_pnl_thb, last_error, created_at, updated_at
                from bot_runs order by started_at desc limit $1
                """,
                limit,
            )
            orders = await conn.fetch(
                """
                select id::text, run_id::text, extract(epoch from ts)::bigint as time,
                       mode, exchange, symbol, side, order_type as type,
                       price::float, qty::float, notional_thb::float, status, raw, created_at, updated_at
                from orders order by ts desc limit $1
                """,
                limit,
            )
            positions = await conn.fetch(
                """
                select id::text, run_id::text, status, mode, symbol, side,
                       entry::float, qty::float, stop_loss::float, take_profit::float,
                       extract(epoch from opened_at)::bigint as opened_at,
                       extract(epoch from closed_at)::bigint as closed_at,
                       last_price::float, unrealized_pnl_thb::float, realized_pnl_thb::float,
                       raw, created_at, updated_at
                from positions order by opened_at desc limit $1
                """,
                limit,
            )
            events = await conn.fetch(
                """
                select id, run_id::text, extract(epoch from ts)::bigint as time,
                       level, message, data, created_at
                from bot_events order by ts desc limit $1
                """,
                limit,
            )

        def clean(rows):
            return [{k: _iso(v) for k, v in dict(row).items()} for row in rows]

        return {
            "storage": "database",
            "durable": True,
            "runs": clean(runs),
            "orders": clean(orders),
            "positions": clean(positions),
            "events": clean(events),
        }

    async def save_bot_run(self, run: dict) -> None:
        if not self.enabled:
            self._save_local_item("runs", run, key="id", upsert=True)
            return
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                insert into bot_runs (
                    id, exchange, mode, symbol, timeframe, config, status,
                    started_at, stopped_at, realized_pnl_thb, last_error
                )
                values (
                    $1::uuid, $2, $3, $4, $5, $6::jsonb, $7,
                    to_timestamp($8), case when $9::bigint is null then null else to_timestamp($9) end,
                    $10, $11
                )
                on conflict (id) do update set
                    exchange = excluded.exchange,
                    mode = excluded.mode,
                    symbol = excluded.symbol,
                    timeframe = excluded.timeframe,
                    config = excluded.config,
                    status = excluded.status,
                    stopped_at = excluded.stopped_at,
                    realized_pnl_thb = excluded.realized_pnl_thb,
                    last_error = excluded.last_error,
                    updated_at = now()
                """,
                run["id"],
                run.get("exchange"),
                run.get("mode"),
                run.get("symbol"),
                run.get("timeframe"),
                _json(run.get("config")),
                run.get("status"),
                int(run.get("started_at") or 0),
                run.get("stopped_at"),
                float(run.get("realized_pnl_thb") or 0),
                run.get("last_error"),
            )

    async def save_bot_event(self, event: dict) -> None:
        if not self.enabled:
            row = {**event, "id": int(time.time() * 1000)}
            self._save_local_item("events", row, key=None, upsert=False)
            return
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                insert into bot_events (run_id, ts, level, message, data)
                values ($1::uuid, to_timestamp($2), $3, $4, $5::jsonb)
                """,
                event.get("run_id"),
                int(event.get("time") or 0),
                event.get("level"),
                event.get("message"),
                _json(event.get("data")),
            )

    async def save_order(self, order: dict, run_id: str | None = None) -> None:
        if not self.enabled:
            row = {**order, "run_id": run_id}
            self._save_local_item("orders", row, key="id", upsert=True)
            return
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                insert into orders (
                    id, run_id, ts, mode, exchange, symbol, side, order_type,
                    price, qty, notional_thb, status, raw
                )
                values (
                    $1::uuid, $2::uuid, to_timestamp($3), $4, $5, $6, $7, $8,
                    $9, $10, $11, $12, $13::jsonb
                )
                on conflict (id) do update set
                    run_id = excluded.run_id,
                    status = excluded.status,
                    price = excluded.price,
                    qty = excluded.qty,
                    notional_thb = excluded.notional_thb,
                    raw = excluded.raw,
                    updated_at = now()
                """,
                order["id"],
                run_id,
                int(order.get("time") or 0),
                order.get("mode"),
                order.get("exchange"),
                order.get("symbol"),
                order.get("side"),
                order.get("type"),
                float(order.get("price") or 0),
                float(order.get("qty") or 0),
                float(order.get("notional_thb") or 0),
                order.get("status"),
                _json(order),
            )

    async def save_position(self, position: dict, run_id: str | None = None) -> None:
        if not self.enabled:
            row = {**position, "run_id": run_id}
            self._save_local_item("positions", row, key="id", upsert=True)
            return
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                insert into positions (
                    id, run_id, status, mode, symbol, side, entry, qty,
                    stop_loss, take_profit, opened_at, closed_at,
                    last_price, unrealized_pnl_thb, realized_pnl_thb, raw
                )
                values (
                    $1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8,
                    $9, $10, to_timestamp($11),
                    case when $12::bigint is null then null else to_timestamp($12) end,
                    $13, $14, $15, $16::jsonb
                )
                on conflict (id) do update set
                    status = excluded.status,
                    last_price = excluded.last_price,
                    unrealized_pnl_thb = excluded.unrealized_pnl_thb,
                    realized_pnl_thb = excluded.realized_pnl_thb,
                    closed_at = excluded.closed_at,
                    raw = excluded.raw,
                    updated_at = now()
                """,
                position["id"],
                run_id,
                position.get("status"),
                position.get("mode"),
                position.get("symbol"),
                position.get("side"),
                float(position.get("entry") or 0),
                float(position.get("qty") or 0),
                float(position.get("stop_loss") or 0),
                float(position.get("take_profit") or 0),
                int(position.get("opened_at") or 0),
                position.get("closed_at"),
                float(position.get("last_price") or 0),
                float(position.get("unrealized_pnl_thb") or 0),
                float(position.get("realized_pnl_thb") or 0),
                _json(position),
            )

    async def upsert_memory(self, key: str, value: Any, category: str = "general") -> dict:
        if not self.enabled:
            return {"stored": False, "key": key}
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                insert into app_memory (key, category, value)
                values ($1, $2, $3::jsonb)
                on conflict (key) do update set
                    category = excluded.category,
                    value = excluded.value,
                    updated_at = now()
                returning key, category, value, updated_at
                """,
                key,
                category,
                _json(value),
            )
            return dict(row)

    async def list_memory(self, category: str | None = None, limit: int = 100) -> list[dict]:
        if not self.enabled:
            return []
        async with self.pool.acquire() as conn:
            if category:
                rows = await conn.fetch(
                    """
                    select key, category, value, updated_at
                    from app_memory
                    where category = $1
                    order by updated_at desc
                    limit $2
                    """,
                    category,
                    limit,
                )
            else:
                rows = await conn.fetch(
                    """
                    select key, category, value, updated_at
                    from app_memory
                    order by updated_at desc
                    limit $1
                    """,
                    limit,
                )
            return [dict(row) for row in rows]
