"""Optional Supabase/Postgres persistence.

The app still works without a database. Set DATABASE_URL to enable durable
storage for bot runs, events, orders, positions, and app memory.
"""
from __future__ import annotations

import json
from typing import Any


def _json(data: Any) -> str:
    return json.dumps(data or {}, ensure_ascii=False, default=str)


class DatabaseStore:
    def __init__(self, database_url: str = ""):
        self.database_url = database_url.strip()
        self.pool = None

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

    async def save_bot_run(self, run: dict) -> None:
        if not self.enabled:
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
