"""FastAPI app: routes, WebSocket สตรีมราคา, alert loop, TradingView webhook, เสิร์ฟ frontend."""
from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path

import base64

from fastapi import (FastAPI, File, Form, HTTPException, Query, Request, UploadFile,
                     WebSocket, WebSocketDisconnect)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from app import alerts, knowledge_base
from app.analysis import analyze_data
from app.autotrade import AutoTradeConfig, AutoTradeManager
from app.backtest import run_backtest, live_signal
from app.bitkub import BitkubClient
from app.config import get_settings
from app.data.base import DataProvider
from app.db import DatabaseStore
from app.engine import build_report
from app.indicators import compute_indicators
from app.schemas import (AlertRule, AnalyzeRequest, BacktestRequest, CandlesResponse,
                         LiveSignalRequest, MultiSchoolReport)
from app.schools import evaluate_python_schools
from app.vision import analyze_image

settings = get_settings()
app = FastAPI(title="AI Trade Assistant", version="0.1.0")

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


def get_provider() -> DataProvider:
    """เลือก provider ตามการตั้งค่า — fallback เป็น mock ถ้าตั้งค่าไม่ครบ."""
    # มี OANDA token → route ทอง/เงิน/forex ไป OANDA (ตรง TradingView เป๊ะ), ที่เหลือใช้ Yahoo
    if settings.oanda_api_token:
        try:
            from app.data.oanda import OandaProvider, RouterProvider
            from app.data.yahoo import YahooProvider
            return RouterProvider(
                OandaProvider(settings.oanda_api_token, settings.oanda_env, settings.oanda_account_id),
                YahooProvider(),
            )
        except Exception:
            from app.data.yahoo import YahooProvider
            return YahooProvider()
    if settings.data_provider == "yahoo":
        from app.data.yahoo import YahooProvider
        return YahooProvider()
    if settings.data_provider == "finnhub":
        try:
            from app.data.finnhub import FinnhubProvider
            return FinnhubProvider()
        except Exception:
            from app.data.mock import MockProvider
            return MockProvider()
    from app.data.mock import MockProvider
    return MockProvider()


provider = get_provider()
store = DatabaseStore(settings.database_url)
bitkub_client = BitkubClient(
    api_key=settings.bitkub_api_key,
    api_secret=settings.bitkub_api_secret,
)
auto_trade = AutoTradeManager(
    bitkub_client,
    real_enabled=settings.bitkub_real_trading_enabled,
    store=store,
)
_symbol_search_cache: dict[str, tuple[float, list[dict]]] = {}


class MemoryUpsert(BaseModel):
    key: str = Field(min_length=1, max_length=160)
    category: str = Field(default="general", min_length=1, max_length=80)
    value: dict


def _indicator_params(
    ema_fast: int | None = None,
    ema_mid: int | None = None,
    ema_slow: int | None = None,
    rsi_period: int | None = None,
    bb_period: int | None = None,
    bb_mult: float | None = None,
    stoch_k: int | None = None,
    stoch_d: int | None = None,
    macd_fast: int | None = None,
    macd_slow: int | None = None,
    macd_signal: int | None = None,
) -> dict:
    raw = {
        "ema_fast": ema_fast,
        "ema_mid": ema_mid,
        "ema_slow": ema_slow,
        "rsi_period": rsi_period,
        "bb_period": bb_period,
        "bb_mult": bb_mult,
        "stoch_k": stoch_k,
        "stoch_d": stoch_d,
        "macd_fast": macd_fast,
        "macd_slow": macd_slow,
        "macd_signal": macd_signal,
    }
    return {key: value for key, value in raw.items() if value is not None}


def _fallback_symbol_search(term: str, limit: int) -> list[dict]:
    needle = term.lower().strip()
    items = settings.sample_symbols
    if not needle:
        return items[:limit]

    def rank(item: dict) -> tuple[int, str]:
        symbol = item["symbol"].lower()
        name = item["name"].lower()
        market = item["market"].lower()
        if symbol == needle:
            return (0, symbol)
        if symbol.startswith(needle):
            return (1, symbol)
        if name.startswith(needle):
            return (2, symbol)
        if needle in symbol:
            return (3, symbol)
        if needle in name:
            return (4, symbol)
        if needle in market:
            return (5, symbol)
        return (99, symbol)

    matches = [item for item in items if rank(item)[0] < 99]
    return sorted(matches, key=rank)[:limit]


def _rank_symbol_result(item: dict, needle: str, source_rank: int) -> tuple[int, int, int, str]:
    symbol = item["symbol"].lower()
    name = item.get("name", "").lower()
    market = item.get("market", "").upper()

    if symbol == needle:
        text_rank = 0
    elif symbol.startswith(needle):
        text_rank = 1
    elif name.startswith(needle):
        text_rank = 2
    elif needle in symbol:
        text_rank = 3
    elif needle in name:
        text_rank = 4
    else:
        text_rank = 9

    if item["symbol"].endswith(".BK") or market in {"TH", "SET", "SET.BK"}:
        market_rank = 0
    elif market in {"NYQ", "NYS", "NAS", "NMS", "ASE", "PCX", "US"}:
        market_rank = 1
    elif market in {"CRYPTO", "CRYPTOCURRENCY", "COMMODITY", "FUTURE", "INDEX"}:
        market_rank = 2
    elif market in {"ETF", "MUTUALFUND"}:
        market_rank = 4
    elif market in {"CCY", "CURRENCY"}:
        market_rank = 5
    else:
        market_rank = 3

    return (text_rank, market_rank, source_rank, symbol)


# ---------- REST ----------
@app.get("/api/health")
async def health():
    llm_cfg = settings.resolve_llm()
    return {
        "status": "ok",
        "data_provider": provider.name,
        "database_enabled": store.enabled,
        "supabase_url": settings.supabase_url,
        "ai_enabled": bool(llm_cfg["api_key"]),
        "ai_provider": llm_cfg["provider"],
        "model": llm_cfg["model"],
        "vision": llm_cfg["vision"],
    }


@app.get("/api/db/status")
async def db_status():
    return {
        "enabled": store.enabled,
        "configured": bool(settings.database_url),
        "supabase_url": settings.supabase_url,
    }


@app.get("/api/memory")
async def list_memory(category: str | None = None, limit: int = Query(100, ge=1, le=500)):
    return await store.list_memory(category, limit)


@app.post("/api/memory")
async def upsert_memory(req: MemoryUpsert):
    return await store.upsert_memory(req.key, req.value, req.category)


@app.get("/api/symbols")
async def symbols():
    return settings.sample_symbols


@app.get("/api/search-symbols")
async def search_symbols(q: str = "", limit: int = Query(20, ge=1, le=100)):
    """Search symbols from local presets and Yahoo Finance, with a short cache."""
    term = q.strip()
    fallback = _fallback_symbol_search(term, limit)
    if not term or provider.name != "yahoo":
        return fallback

    cache_key = f"{term.lower()}:{limit}"
    cached = _symbol_search_cache.get(cache_key)
    if cached and time.time() - cached[0] < 600:
        return cached[1]

    try:
        yahoo_search = getattr(provider, "search_symbols")
        found = await yahoo_search(term, limit)
    except Exception:
        return fallback

    combined: list[tuple[dict, int]] = [(item, 1) for item in found]
    seen = {item["symbol"] for item in found}
    for item in fallback:
        if item["symbol"] not in seen:
            combined.append((item, 0))
            seen.add(item["symbol"])
    combined.sort(key=lambda pair: _rank_symbol_result(pair[0], term.lower(), pair[1]))
    result = [item for item, _ in combined[:limit]]
    _symbol_search_cache[cache_key] = (time.time(), result)
    return result


@app.get("/api/schools")
async def schools():
    """รายชื่อศาสตร์ทั้งหมดที่ใช้ประเมิน (ให้ frontend แสดงหัวตาราง/อธิบาย)."""
    return knowledge_base.get_index()


@app.get("/api/candles", response_model=CandlesResponse)
async def candles(
    symbol: str,
    timeframe: str = "1h",
    limit: int = Query(300, ge=30, le=1000),
    ema_fast: int | None = Query(None, ge=1, le=500),
    ema_mid: int | None = Query(None, ge=1, le=500),
    ema_slow: int | None = Query(None, ge=1, le=1000),
    rsi_period: int | None = Query(None, ge=2, le=100),
    bb_period: int | None = Query(None, ge=2, le=200),
    bb_mult: float | None = Query(None, ge=0.1, le=10),
    stoch_k: int | None = Query(None, ge=2, le=100),
    stoch_d: int | None = Query(None, ge=1, le=50),
    macd_fast: int | None = Query(None, ge=1, le=100),
    macd_slow: int | None = Query(None, ge=2, le=200),
    macd_signal: int | None = Query(None, ge=1, le=100),
):
    if timeframe not in settings.timeframes:
        raise HTTPException(400, f"timeframe ไม่รองรับ: {timeframe}")
    data = await provider.get_candles(symbol, timeframe, limit)
    if not data:
        raise HTTPException(404, f"ไม่พบข้อมูลของ {symbol}")
    ind = compute_indicators(data, _indicator_params(
        ema_fast, ema_mid, ema_slow, rsi_period, bb_period, bb_mult,
        stoch_k, stoch_d, macd_fast, macd_slow, macd_signal,
    ))
    return CandlesResponse(symbol=symbol, timeframe=timeframe, candles=data, indicators=ind)


@app.get("/api/quote")
async def quote(symbol: str):
    return await provider.get_quote(symbol)


@app.get("/api/quotes")
async def quotes(symbols: str):
    syms = [item.strip() for item in symbols.split(",") if item.strip()]
    if not syms:
        return []
    syms = syms[:40]
    get_quotes = getattr(provider, "get_quotes", None)
    if get_quotes:
        try:
            rows = await get_quotes(syms)
            by_symbol = {row.symbol.upper(): row.model_dump() for row in rows}
            return [
                by_symbol.get(sym.upper(), {"symbol": sym, "error": "quote not found"})
                for sym in syms
            ]
        except Exception:
            pass

    async def one(sym: str):
        try:
            return (await provider.get_quote(sym)).model_dump()
        except Exception as exc:  # noqa: BLE001
            return {"symbol": sym, "error": str(exc)}

    return await asyncio.gather(*(one(sym) for sym in syms))


@app.post("/api/analyze", response_model=MultiSchoolReport)
async def analyze_route(req: AnalyzeRequest):
    """โหมดข้อมูล: ดึง OHLCV → ประเมินทุกศาสตร์ → ตารางความน่าจะเป็น."""
    if req.timeframe not in settings.timeframes:
        raise HTTPException(400, f"timeframe ไม่รองรับ: {req.timeframe}")
    data = await provider.get_candles(req.symbol, req.timeframe, 300)
    if not data:
        raise HTTPException(404, f"ไม่พบข้อมูลของ {req.symbol}")
    ind = compute_indicators(data, req.indicator_params)

    python_verdicts = evaluate_python_schools(data, ind)
    if req.enabled_schools is not None:
        python_verdicts = [v for v in python_verdicts if v["id"] in req.enabled_schools]
    ai = await analyze_data(req.symbol, req.timeframe, data, ind, req.note, req.enabled_schools)
    verdicts = python_verdicts + ai["verdicts"]

    return MultiSchoolReport(**build_report(
        verdicts, input_mode="data", ai_enabled=settings.llm_enabled(),
        symbol=req.symbol, timeframe=req.timeframe,
        psychology_summary=ai.get("psychology_summary"),
        suggested_plan=ai.get("suggested_plan"),
        weights=req.weights,
    ))


@app.post("/api/analyze-image", response_model=MultiSchoolReport)
async def analyze_image_route(
    file: UploadFile = File(...),
    symbol: str | None = Form(None),
    timeframe: str | None = Form(None),
    note: str | None = Form(None),
):
    """โหมดภาพ: อัปโหลด screenshot กราฟ → Claude vision ประเมินทุกศาสตร์ → ตาราง."""
    media_type = file.content_type or "image/png"
    if not media_type.startswith("image/"):
        raise HTTPException(400, "กรุณาอัปโหลดไฟล์รูปภาพ")
    raw = await file.read()
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(413, "ไฟล์ใหญ่เกิน 8MB")
    image_b64 = base64.b64encode(raw).decode("ascii")

    ai = await analyze_image(image_b64, media_type, symbol, timeframe, note)

    return MultiSchoolReport(**build_report(
        ai["verdicts"], input_mode="image", ai_enabled=settings.llm_enabled(),
        symbol=symbol, timeframe=timeframe,
        psychology_summary=ai.get("psychology_summary"),
        suggested_plan=ai.get("suggested_plan"),
    ))


# ---------- Backtest (ท่าไม้ตาย) ----------
@app.post("/api/backtest")
async def backtest_route(req: BacktestRequest):
    """ท่าไม้ตาย: รวมทุกศาสตร์ (เชิงสูตร) → backtest ย้อนหลังยาว → สถิติ + จุดเข้า-ออก."""
    if req.timeframe not in settings.timeframes:
        raise HTTPException(400, f"timeframe ไม่รองรับ: {req.timeframe}")

    get_history = getattr(provider, "get_history", None)
    if get_history:
        try:
            data = await get_history(req.symbol, req.timeframe)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"ดึงข้อมูลย้อนหลังไม่สำเร็จ: {exc}")
    else:
        data = await provider.get_candles(req.symbol, req.timeframe, 2000)

    if not data:
        raise HTTPException(404, f"ไม่พบข้อมูลย้อนหลังของ {req.symbol}")

    result = run_backtest(
        data,
        timeframe=req.timeframe,
        entry_threshold=req.entry_threshold,
        rr_ratio=req.rr_ratio,
        atr_mult=req.atr_mult,
        direction=req.direction,
        use_trend_filter=req.use_trend_filter,
        min_directional_weight=req.min_directional_weight,
        max_hold_bars=req.max_hold_bars,
        indicator_params=req.indicator_params,
        weights=req.weights,
        enabled_schools=req.enabled_schools,
    )
    if result.get("error"):
        raise HTTPException(422, result["error"])
    result["symbol"] = req.symbol
    return JSONResponse(result)


@app.post("/api/live-signal")
async def live_signal_route(req: LiveSignalRequest):
    """สัญญาณ ณ ปัจจุบันของกลยุทธ์ (กฎเดียวกับ backtest) + ราคา entry/SL/TP + ขนาดโพสิชัน."""
    if req.timeframe not in settings.timeframes:
        raise HTTPException(400, f"timeframe ไม่รองรับ: {req.timeframe}")

    data = await provider.get_candles(req.symbol, req.timeframe, 400)
    if not data:
        raise HTTPException(404, f"ไม่พบข้อมูลของ {req.symbol}")

    result = live_signal(
        data,
        timeframe=req.timeframe,
        entry_threshold=req.entry_threshold,
        rr_ratio=req.rr_ratio,
        atr_mult=req.atr_mult,
        direction=req.direction,
        use_trend_filter=req.use_trend_filter,
        min_directional_weight=req.min_directional_weight,
        indicator_params=req.indicator_params,
        weights=req.weights,
        enabled_schools=req.enabled_schools,
        account_size=req.account_size,
        risk_pct=req.risk_pct,
    )
    if result.get("error"):
        raise HTTPException(422, result["error"])
    result["symbol"] = req.symbol
    return JSONResponse(result)


# ---------- Bitkub / Auto Trade ----------
@app.get("/api/bitkub/symbols")
async def bitkub_symbols():
    return await bitkub_client.symbols()


@app.get("/api/bitkub/ticker")
async def bitkub_ticker(symbol: str | None = None):
    return await bitkub_client.ticker(symbol)


@app.get("/api/autotrade/status")
async def autotrade_status():
    return auto_trade.status()


@app.post("/api/autotrade/start")
async def autotrade_start(req: AutoTradeConfig):
    try:
        return await auto_trade.start(req)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/autotrade/stop")
async def autotrade_stop():
    return await auto_trade.stop()


@app.post("/api/autotrade/tick")
async def autotrade_tick():
    try:
        return await auto_trade.tick_once()
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))


# ---------- Alerts ----------
@app.get("/api/alerts")
async def get_alerts():
    return alerts.list_rules()


@app.post("/api/alerts")
async def create_alert(rule: AlertRule):
    return alerts.add_rule(rule)


@app.delete("/api/alerts/{rule_id}")
async def remove_alert(rule_id: str):
    if not alerts.delete_rule(rule_id):
        raise HTTPException(404, "ไม่พบกฎนี้")
    return {"deleted": rule_id}


@app.get("/api/alerts/triggered")
async def triggered():
    return alerts.list_triggered()


# ---------- TradingView Webhook ----------
@app.post("/webhook/tradingview")
async def tradingview_webhook(request: Request):
    """รับ Alert จาก Pine Script. ใส่ field 'secret' ใน payload ให้ตรงกับ env เพื่อยืนยัน."""
    try:
        payload = await request.json()
    except Exception:
        payload = {"raw": (await request.body()).decode("utf-8", "ignore")}
    if isinstance(payload, dict) and payload.get("secret") != settings.tradingview_webhook_secret:
        raise HTTPException(401, "secret ไม่ถูกต้อง")
    # โปรดักชัน: บันทึกลง DB / ส่งต่อเข้า alert pipeline
    print("[TradingView webhook]", payload)
    return JSONResponse({"received": True})


# ---------- WebSocket สตรีมราคา ----------
@app.websocket("/ws/quotes")
async def ws_quotes(ws: WebSocket, symbol: str = "AAPL", interval: float = 1.0):
    await ws.accept()
    delay = min(max(interval, 0.5), 30.0)
    try:
        while True:
            q = await provider.get_quote(symbol)
            await ws.send_json(q.model_dump())
            await asyncio.sleep(delay)
    except WebSocketDisconnect:
        return
    except Exception:
        with contextlib.suppress(Exception):
            await ws.close()


# ---------- Background alert loop ----------
async def _alert_loop():
    """ตรวจราคาของ symbol ที่มีกฎทุก ๆ 10 วินาที แล้วทริกเกอร์ alert."""
    while True:
        try:
            for sym in alerts.symbols_with_rules():
                data = await provider.get_candles(sym, "1h", 60)
                if not data:
                    continue
                ind = compute_indicators(data)
                rsi_val = ind["summary"].get("rsi14")
                fired = alerts.evaluate(sym, data[-1].close, rsi_val)
                for f in fired:
                    print("[ALERT]", f.message)
        except Exception as e:  # noqa: BLE001
            print("alert loop error:", e)
        await asyncio.sleep(10)


@app.on_event("startup")
async def _startup():
    try:
        await store.connect()
        if store.enabled:
            print("[DB] connected")
        elif settings.database_url:
            print("[DB] DATABASE_URL configured but store is not enabled")
    except Exception as exc:  # noqa: BLE001
        print("[DB] connection failed:", exc)
    app.state.alert_task = asyncio.create_task(_alert_loop())


@app.on_event("shutdown")
async def _shutdown():
    await auto_trade.stop()
    await store.close()
    task = getattr(app.state, "alert_task", None)
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


# ---------- เสิร์ฟ frontend ----------
@app.get("/")
async def index():
    idx = FRONTEND_DIR / "index.html"
    if idx.exists():
        return FileResponse(idx)
    return {"message": "AI Trade Assistant API. ดู /docs สำหรับ API"}


@app.get("/config.js")
async def frontend_config():
    cfg = FRONTEND_DIR / "config.js"
    if cfg.exists():
        return FileResponse(cfg, media_type="application/javascript")
    return JSONResponse({}, media_type="application/javascript")
