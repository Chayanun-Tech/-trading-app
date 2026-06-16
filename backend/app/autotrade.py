"""Auto-trade manager.

Version 1 is intentionally conservative:
- Paper trading works immediately.
- Real Bitkub spot orders are gated by settings and explicit confirmation.
- Spot trading only opens long positions; short signals are treated as no-entry.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.backtest import live_signal
from app.bitkub import BitkubClient
from app.math_model import predict as predict_math_model


class AutoTradeConfig(BaseModel):
    exchange: Literal["bitkub"] = "bitkub"
    mode: Literal["paper", "real"] = "paper"
    symbol: str = "BTC_THB"
    timeframe: str = "1h"
    poll_interval_sec: int = Field(default=30, ge=5, le=3600)
    quote_budget_thb: float = Field(default=10000, gt=0)
    risk_pct: float = Field(default=5.0, gt=0, le=10)
    max_daily_loss_pct: float = Field(default=3.0, gt=0, le=50)
    max_open_positions: int = Field(default=1, ge=1, le=10)
    entry_threshold: int = Field(default=65, ge=51, le=95)
    rr_ratio: float = Field(default=1.5, ge=1.5, le=10)
    atr_mult: float = Field(default=1.5, ge=0.2, le=10)
    direction: Literal["long"] = "long"
    use_trend_filter: bool = True
    min_directional_weight: float = Field(default=2.0, ge=0.5, le=12)
    indicator_params: dict | None = None
    enabled_schools: list[str] | None = None
    weights: dict | None = None
    real_confirm_text: str | None = None
    use_trained_model: bool = True
    trained_model_min_prob: float = Field(default=0.53, ge=0.5, le=0.95)


class AutoTradeManager:
    def __init__(self, client: BitkubClient, *, real_enabled: bool = False, store: Any = None):
        self.client = client
        self.real_enabled = real_enabled
        self.store = store
        self.running = False
        self.config: AutoTradeConfig | None = None
        self.task: asyncio.Task | None = None
        self.run_id: str | None = None
        self.positions: list[dict] = []
        self.orders: list[dict] = []
        self.events: list[dict] = []
        self.latest_signal: dict | None = None
        self.last_error: str | None = None
        self.started_at: int | None = None
        self.updated_at: int | None = None
        self.last_entry_candle_time: int | None = None
        self.realized_pnl_thb = 0.0

    def _persist(self, method: str, *args) -> None:
        if not self.store:
            return
        try:
            loop = asyncio.get_running_loop()
            fn = getattr(self.store, method)
            loop.create_task(fn(*args))
        except RuntimeError:
            return

    def _run_payload(self, status: str, stopped_at: int | None = None) -> dict:
        cfg = self.config
        return {
            "id": self.run_id,
            "exchange": cfg.exchange if cfg else "bitkub",
            "mode": cfg.mode if cfg else "paper",
            "symbol": cfg.symbol if cfg else "",
            "timeframe": cfg.timeframe if cfg else "",
            "config": cfg.model_dump() if cfg else {},
            "status": status,
            "started_at": self.started_at,
            "stopped_at": stopped_at,
            "realized_pnl_thb": round(self.realized_pnl_thb, 2),
            "last_error": self.last_error,
        }

    def _event(self, level: str, message: str, data: dict | None = None) -> None:
        event = {
            "time": int(time.time()),
            "level": level,
            "message": message,
            "data": data or {},
            "run_id": self.run_id,
        }
        self.events.append(event)
        self.events = self.events[-250:]
        self._persist("save_bot_event", event)

    def status(self) -> dict:
        return {
            "running": self.running,
            "real_enabled": self.real_enabled,
            "run_id": self.run_id,
            "config": self.config.model_dump() if self.config else None,
            "positions": self.positions,
            "orders": self.orders[-100:],
            "events": self.events[-80:],
            "latest_signal": self.latest_signal,
            "last_error": self.last_error,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "realized_pnl_thb": round(self.realized_pnl_thb, 2),
        }

    async def start(self, config: AutoTradeConfig) -> dict:
        if config.mode == "real":
            if not self.real_enabled:
                raise RuntimeError("Real trading is disabled. Set BITKUB_REAL_TRADING_ENABLED=true first.")
            if config.real_confirm_text != "I UNDERSTAND REAL ORDERS":
                raise RuntimeError('Real mode requires real_confirm_text="I UNDERSTAND REAL ORDERS".')
        await self.stop()
        self.config = config
        self.run_id = str(uuid.uuid4())
        self.running = True
        self.started_at = int(time.time())
        self.updated_at = self.started_at
        self.last_error = None
        self.last_entry_candle_time = None
        self._persist("save_bot_run", self._run_payload("running"))
        self._event("info", f"Auto trade started in {config.mode} mode", config.model_dump())
        self.task = asyncio.create_task(self._loop())
        return self.status()

    async def stop(self) -> dict:
        self.running = False
        stopped_at = int(time.time())
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
        self._event("info", "Auto trade stopped")
        if self.run_id:
            self._persist("save_bot_run", self._run_payload("stopped", stopped_at))
        return self.status()

    async def tick_once(self, config: AutoTradeConfig | None = None) -> dict:
        # ถ้าส่ง config มา (กด Run 1 Tick โดยยังไม่ Start) → ตั้งค่าให้ทดสอบได้เลย
        if config is not None and not self.running:
            if config.mode == "real":
                raise RuntimeError("Run 1 Tick ใช้ทดสอบ Paper เท่านั้น — Real mode ต้องกด Start Bot")
            self.config = config
        if not self.config:
            raise RuntimeError("Auto trade is not configured")
        await self._tick()
        return self.status()

    async def _loop(self) -> None:
        while self.running:
            try:
                await self._tick()
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
                self._event("error", str(exc))
            await asyncio.sleep(self.config.poll_interval_sec if self.config else 30)

    async def _tick(self) -> None:
        assert self.config is not None
        cfg = self.config
        candles = await self.client.candles(cfg.symbol, cfg.timeframe, 400)
        if len(candles) < 60:
            raise RuntimeError(f"Not enough Bitkub candles for {cfg.symbol} {cfg.timeframe}: {len(candles)}")
        signal = live_signal(
            candles,
            timeframe=cfg.timeframe,
            entry_threshold=cfg.entry_threshold,
            rr_ratio=cfg.rr_ratio,
            atr_mult=cfg.atr_mult,
            direction="long",
            use_trend_filter=cfg.use_trend_filter,
            min_directional_weight=cfg.min_directional_weight,
            indicator_params=cfg.indicator_params,
            weights=cfg.weights,
            enabled_schools=cfg.enabled_schools,
            account_size=cfg.quote_budget_thb,
            risk_pct=cfg.risk_pct,
        )
        signal["symbol"] = cfg.symbol
        if cfg.use_trained_model:
            model_signal = predict_math_model(candles)
            if model_signal:
                signal["trained_model"] = model_signal
                model_prob = float(model_signal.get("prob_up") or 0.0)
                if signal.get("status") == "entry" and model_prob < cfg.trained_model_min_prob:
                    signal["status"] = "waiting"
                    signal["trade"] = None
                    signal.setdefault("reasons", []).append(
                        f"Trained model blocked entry: prob_up {model_prob:.2%} < "
                        f"{cfg.trained_model_min_prob:.2%}"
                    )
                    signal.setdefault("watch_plan", []).append(
                        f"Wait for trained model probability >= {cfg.trained_model_min_prob:.2%}"
                    )
                    self._event("info", "Entry blocked by trained math model", model_signal)
            else:
                signal["trained_model"] = {
                    "available": False,
                    "message": "No trained model found or not enough features; school consensus is used alone.",
                }
        self.latest_signal = signal
        self.updated_at = int(time.time())
        price = float(signal.get("last_price") or candles[-1].close)
        await self._update_positions(price)
        if signal.get("status") == "entry" and signal.get("trade"):
            await self._maybe_open_long(signal)

    def _open_positions(self) -> list[dict]:
        return [p for p in self.positions if p["status"] == "open"]

    def _daily_loss_limit_hit(self) -> bool:
        cfg = self.config
        if not cfg:
            return True
        limit = cfg.quote_budget_thb * (cfg.max_daily_loss_pct / 100)
        return self.realized_pnl_thb <= -abs(limit)

    async def _maybe_open_long(self, signal: dict) -> None:
        assert self.config is not None
        cfg = self.config
        trade = signal["trade"]
        if trade.get("side") != "long":
            self._event("info", "Short signal ignored because Bitkub spot bot only opens long positions", trade)
            return
        if self.last_entry_candle_time == signal.get("as_of_time"):
            return
        if len(self._open_positions()) >= cfg.max_open_positions:
            self._event("info", "Entry skipped: max open positions reached")
            return
        if self._daily_loss_limit_hit():
            self._event("warning", "Entry blocked: daily loss limit reached")
            return

        entry = float(trade["entry"])
        stop = float(trade["stop_loss"])
        take = float(trade["take_profit"])
        risk_per_unit = abs(entry - stop)
        risk_amount = cfg.quote_budget_thb * cfg.risk_pct / 100
        risk_qty = risk_amount / risk_per_unit if risk_per_unit else 0
        budget_qty = cfg.quote_budget_thb / entry if entry else 0
        qty = min(risk_qty, budget_qty)
        notional = qty * entry
        if qty <= 0 or notional <= 0:
            self._event("warning", "Entry skipped: invalid sizing", trade)
            return

        order = {
            "id": str(uuid.uuid4()),
            "time": int(time.time()),
            "mode": cfg.mode,
            "exchange": cfg.exchange,
            "symbol": cfg.symbol,
            "side": "buy",
            "type": "limit",
            "price": round(entry, 4),
            "qty": round(qty, 8),
            "notional_thb": round(notional, 2),
            "status": "paper_filled" if cfg.mode == "paper" else "pending",
            "signal_time": signal.get("as_of_time"),
        }

        if cfg.mode == "real":
            result = await self.client.place_bid(cfg.symbol, notional, entry, "limit")
            order["status"] = "sent"
            order["exchange_result"] = result

        self.orders.append(order)
        self._persist("save_order", order, self.run_id)
        self.positions.append({
            "id": order["id"],
            "status": "open",
            "mode": cfg.mode,
            "symbol": cfg.symbol,
            "side": "long",
            "entry": round(entry, 4),
            "qty": round(qty, 8),
            "stop_loss": round(stop, 4),
            "take_profit": round(take, 4),
            "opened_at": order["time"],
            "signal_time": signal.get("as_of_time"),
            "unrealized_pnl_thb": 0.0,
        })
        self._persist("save_position", self.positions[-1], self.run_id)
        self.last_entry_candle_time = signal.get("as_of_time")
        self._event("trade", f"Opened {cfg.mode} long {cfg.symbol}", order)

    async def _update_positions(self, price: float) -> None:
        for pos in self._open_positions():
            qty = float(pos["qty"])
            pnl = (price - float(pos["entry"])) * qty
            pos["last_price"] = round(price, 4)
            pos["unrealized_pnl_thb"] = round(pnl, 2)
            close_reason = None
            if price >= float(pos["take_profit"]):
                close_reason = "take_profit"
            elif price <= float(pos["stop_loss"]):
                close_reason = "stop_loss"
            if not close_reason:
                self._persist("save_position", pos, self.run_id)
                continue

            # Real positions must be closed with a real exchange sell before we book PnL.
            # A market ask guarantees the exit (critical for stop-loss); a failed sell leaves the
            # position open so the next tick retries instead of pretending we are flat.
            exit_status = "paper_filled"
            exchange_result = None
            if pos["mode"] == "real":
                try:
                    exchange_result = await self.client.place_ask(pos["symbol"], qty, price, "market")
                    exit_status = "sent"
                except Exception as exc:  # noqa: BLE001
                    self.last_error = str(exc)
                    self._event(
                        "error",
                        f"Real exit order failed for {pos['symbol']} ({close_reason}); will retry next tick",
                        {"reason": close_reason, "error": str(exc)},
                    )
                    self._persist("save_position", pos, self.run_id)
                    continue

            pos["status"] = "closed"
            pos["closed_at"] = int(time.time())
            pos["close_price"] = round(price, 4)
            pos["close_reason"] = close_reason
            pos["realized_pnl_thb"] = round(pnl, 2)
            self.realized_pnl_thb += pnl
            order = {
                "id": str(uuid.uuid4()),
                "time": int(time.time()),
                "mode": pos["mode"],
                "exchange": "bitkub",
                "symbol": pos["symbol"],
                "side": "sell",
                "type": "exit",
                "price": round(price, 4),
                "qty": pos["qty"],
                "notional_thb": round(price * qty, 2),
                "status": exit_status,
                "reason": close_reason,
            }
            if exchange_result is not None:
                order["exchange_result"] = exchange_result
            self.orders.append(order)
            self._persist("save_order", order, self.run_id)
            self._persist("save_position", pos, self.run_id)
            self._event("trade", f"Closed {pos['mode']} long {pos['symbol']} by {close_reason}", pos)
