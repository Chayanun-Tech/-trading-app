"""FastAPI app: routes, WebSocket สตรีมราคา, alert loop, TradingView webhook, เสิร์ฟ frontend."""
from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app import alerts
from app.analysis import DISCLAIMER, analyze
from app.config import get_settings
from app.data.base import DataProvider
from app.indicators import compute_indicators
from app.schemas import (AlertRule, AnalyzeRequest, AnalyzeResponse, CandlesResponse)

settings = get_settings()
app = FastAPI(title="AI Trade Assistant", version="0.1.0")

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


def get_provider() -> DataProvider:
    """เลือก provider ตามการตั้งค่า — fallback เป็น mock ถ้า finnhub ตั้งค่าไม่ครบ."""
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


# ---------- REST ----------
@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "data_provider": provider.name,
        "ai_enabled": bool(settings.anthropic_api_key),
        "model": settings.anthropic_model,
    }


@app.get("/api/symbols")
async def symbols():
    return settings.sample_symbols


@app.get("/api/candles", response_model=CandlesResponse)
async def candles(symbol: str, timeframe: str = "1h", limit: int = Query(300, le=1000)):
    if timeframe not in settings.timeframes:
        raise HTTPException(400, f"timeframe ไม่รองรับ: {timeframe}")
    data = await provider.get_candles(symbol, timeframe, limit)
    if not data:
        raise HTTPException(404, f"ไม่พบข้อมูลของ {symbol}")
    ind = compute_indicators(data)
    return CandlesResponse(symbol=symbol, timeframe=timeframe, candles=data, indicators=ind)


@app.get("/api/quote")
async def quote(symbol: str):
    return await provider.get_quote(symbol)


@app.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze_route(req: AnalyzeRequest):
    if req.timeframe not in settings.timeframes:
        raise HTTPException(400, f"timeframe ไม่รองรับ: {req.timeframe}")
    data = await provider.get_candles(req.symbol, req.timeframe, 300)
    if not data:
        raise HTTPException(404, f"ไม่พบข้อมูลของ {req.symbol}")
    ind = compute_indicators(data)
    report, source = await analyze(req.symbol, req.timeframe, data, ind, req.note)
    return AnalyzeResponse(
        symbol=req.symbol, timeframe=req.timeframe, report=report,
        source=source, disclaimer=DISCLAIMER,
    )


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
async def ws_quotes(ws: WebSocket, symbol: str = "AAPL"):
    await ws.accept()
    try:
        while True:
            q = await provider.get_quote(symbol)
            await ws.send_json(q.model_dump())
            await asyncio.sleep(3)
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
    app.state.alert_task = asyncio.create_task(_alert_loop())


@app.on_event("shutdown")
async def _shutdown():
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
