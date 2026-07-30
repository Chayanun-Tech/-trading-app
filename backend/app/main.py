"""FastAPI app: routes, WebSocket สตรีมราคา, alert loop, TradingView webhook, เสิร์ฟ frontend."""
from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path

import base64
import httpx

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
from app import edgar, thai_sec
from app import ai_analyst
from app import business as business_explainer
from app import macro_business
from app import trend_radar
from app import stock_history
from app import revenue_model
from app import revenue_scanner
from app import industry_peers
from app import value_scanner
from app import ema_scanner
from app import lynch_scanner
from app import sp500_history
from app import confluence
from app.engine import build_report
from app.financials import build_financials
from app.fundamentals import (fetch_yahoo_fundamentals, get_fundamentals, get_offline,
                              is_equity_symbol, save_ai_qualitative, update_offline)
from app.fundamentals_ai import analyze_fundamentals_ai
from app.indicators import compute_indicators
from app.math_model import DEFAULT_MODEL_PATH, load_model
from app.multibagger_scanner import scan_small_caps
from app.schemas import (AlertRule, AnalyzeFundamentalsRequest, AnalyzeRequest,
                         BacktestRequest, CandlesResponse, LiveSignalRequest,
                         MultiSchoolReport, ValueReport)
from app.schools import evaluate_python_schools
from app.value_engine import build_value_report
from app.value_schools import evaluate_value_schools
from app.vision import analyze_image
from app.intrinsic_value import build_iv_report

settings = get_settings()
app = FastAPI(title="AI Trade Assistant", version="0.1.0")

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


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


def _is_bitkub(symbol: str) -> bool:
    """คู่เทรด Bitkub ลงท้าย _THB (เช่น BTC_THB) → ดึงจาก Bitkub ไม่ใช่ provider หลัก."""
    return (symbol or "").upper().endswith("_THB")


def _normalize_search_term(term: str) -> str:
    return term.lower().strip().replace("-", "_").replace("/", "_")


async def _candles_for(symbol: str, timeframe: str, limit: int):
    if _is_bitkub(symbol):
        return await bitkub_client.candles(symbol, timeframe, limit)
    return await provider.get_candles(symbol, timeframe, limit)


async def _quote_for(symbol: str):
    if _is_bitkub(symbol):
        return await bitkub_client.quote(symbol)
    return await provider.get_quote(symbol)


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
    needle = _normalize_search_term(term)
    items = settings.bitkub_pairs + settings.sample_symbols
    if not needle:
        return items[:limit]

    def rank(item: dict) -> tuple[int, str]:
        symbol = _normalize_search_term(item["symbol"])
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
    needle = _normalize_search_term(needle)
    symbol = _normalize_search_term(item["symbol"])
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

    if market == "BITKUB" or item["symbol"].upper().endswith("_THB"):
        market_rank = 0
    elif item["symbol"].endswith(".BK") or market in {"TH", "SET", "SET.BK"}:
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


@app.get("/api/multibagger/scan")
async def multibagger_scan(
    min_market_cap: float = Query(50_000_000, ge=10_000_000),
    max_market_cap: float = Query(3_000_000_000, ge=50_000_000),
    min_price: float = Query(2.0, ge=0.1),
    min_dollar_volume: float = Query(1_000_000, ge=0),
    sector: str | None = None,
    limit: int = Query(20, ge=5, le=50),
    enrich_limit: int = Query(45, ge=10, le=100),
    us_only: bool = True,
):
    """Discover liquid small caps, then rank fundamentals transparently."""
    if max_market_cap <= min_market_cap:
        raise HTTPException(status_code=400, detail="max_market_cap must exceed min_market_cap")
    try:
        return await scan_small_caps(
            min_market_cap=min_market_cap,
            max_market_cap=max_market_cap,
            min_price=min_price,
            min_dollar_volume=min_dollar_volume,
            sector=sector,
            limit=limit,
            enrich_limit=enrich_limit,
            us_only=us_only,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Stock universe unavailable: {exc}") from exc


@app.get("/api/search-symbols")
async def search_symbols(q: str = "", limit: int = Query(20, ge=1, le=100)):
    """Search symbols from local presets and Yahoo Finance, with a short cache."""
    term = q.strip()
    fallback = _fallback_symbol_search(term, limit)
    norm_term = _normalize_search_term(term)
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
            combined.append((item, -1 if item.get("market") == "BITKUB" else 0))
            seen.add(item["symbol"])
    combined.sort(key=lambda pair: _rank_symbol_result(pair[0], norm_term, pair[1]))
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
    data = await _candles_for(symbol, timeframe, limit)
    if not data:
        raise HTTPException(404, f"ไม่พบข้อมูลของ {symbol}")
    ind = compute_indicators(data, _indicator_params(
        ema_fast, ema_mid, ema_slow, rsi_period, bb_period, bb_mult,
        stoch_k, stoch_d, macd_fast, macd_slow, macd_signal,
    ))
    return CandlesResponse(symbol=symbol, timeframe=timeframe, candles=data, indicators=ind)


@app.get("/api/quote")
async def quote(symbol: str):
    return await _quote_for(symbol)


@app.get("/api/quotes")
async def quotes(symbols: str):
    syms = [item.strip() for item in symbols.split(",") if item.strip()]
    if not syms:
        return []
    syms = syms[:40]
    by_symbol: dict[str, dict] = {}
    # คู่ Bitkub (_THB) ดึงทีละตัวจาก Bitkub
    bitkub_syms = [s for s in syms if _is_bitkub(s)]
    other_syms = [s for s in syms if not _is_bitkub(s)]

    async def one_bitkub(sym: str):
        try:
            return (await bitkub_client.quote(sym)).model_dump()
        except Exception as exc:  # noqa: BLE001
            return {"symbol": sym, "error": str(exc)}

    if bitkub_syms:
        for row in await asyncio.gather(*(one_bitkub(s) for s in bitkub_syms)):
            by_symbol[row["symbol"].upper()] = row

    if other_syms:
        get_quotes = getattr(provider, "get_quotes", None)
        rows = None
        if get_quotes:
            try:
                rows = await get_quotes(other_syms)
            except Exception:
                rows = None
        if rows is not None:
            for row in rows:
                by_symbol[row.symbol.upper()] = row.model_dump()
        else:
            async def one(sym: str):
                try:
                    return (await provider.get_quote(sym)).model_dump()
                except Exception as exc:  # noqa: BLE001
                    return {"symbol": sym, "error": str(exc)}
            for row in await asyncio.gather(*(one(s) for s in other_syms)):
                by_symbol[row["symbol"].upper()] = row

    return [by_symbol.get(s.upper(), {"symbol": s, "error": "quote not found"}) for s in syms]


@app.post("/api/analyze", response_model=MultiSchoolReport)
async def analyze_route(req: AnalyzeRequest):
    """โหมดข้อมูล: ดึง OHLCV → ประเมินทุกศาสตร์ → ตารางความน่าจะเป็น."""
    if req.timeframe not in settings.timeframes:
        raise HTTPException(400, f"timeframe ไม่รองรับ: {req.timeframe}")
    data = await _candles_for(req.symbol, req.timeframe, 300)
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


# ---------- สาย VI (ปัจจัยพื้นฐาน) ----------
async def _edgar_facts_safe(symbol: str):
    """ดึงงบ EDGAR แบบไม่ throw (คืน None ถ้าไม่ใช่หุ้น SEC/ล้มเหลว) — ใช้เป็นฐานที่เสถียร."""
    try:
        return await edgar.get_company_facts(symbol)
    except Exception:  # noqa: BLE001
        return None


async def _equity_price_safe(symbol: str) -> float | None:
    """ราคาปัจจุบันจาก provider (httpx, เสถียรกว่า yfinance) สำหรับคำนวณ P/E, P/B ฯลฯ."""
    try:
        q = await provider.get_quote(symbol)
        return float(q.price) if q and q.price else None
    except Exception:  # noqa: BLE001
        return None


async def _value_snapshot(symbol: str) -> dict:
    facts = await _edgar_facts_safe(symbol)
    price = await _equity_price_safe(symbol)
    snap = await get_fundamentals(symbol, facts=facts, price=price)
    if price:  # เก็บราคาปัจจุบันไว้ใน snapshot (สาย IV ต้องใช้เทียบ upside/margin of safety)
        snap = {**snap, "price": price}
    return snap


@app.get("/api/fundamentals")
async def fundamentals_route(symbol: str = Query(..., description="สัญลักษณ์หุ้น เช่น AAPL")):
    """ดึง snapshot ปัจจัยพื้นฐานดิบ (ใช้ดีบัก/แสดงเมตริก)."""
    if not is_equity_symbol(symbol):
        raise HTTPException(400, "สาย VI ใช้ได้กับหุ้นรายตัวเท่านั้น (ไม่รองรับคริปโต/forex/ดัชนี)")
    try:
        return await _value_snapshot(symbol)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"ดึงข้อมูลพื้นฐานไม่สำเร็จ: {exc}")


@app.get("/api/filings")
async def filings_route(symbol: str = Query(..., description="สัญลักษณ์หุ้น เช่น AAPL"),
                        limit: int = Query(25, ge=1, le=100)):
    """เอกสารทางการล่าสุด: SEC EDGAR สำหรับ US หรือ 56-1 One Report สำหรับหุ้นไทย."""
    if not is_equity_symbol(symbol):
        raise HTTPException(400, "ใช้ได้กับหุ้นรายตัวเท่านั้น")
    if thai_sec.is_thai_symbol(symbol):
        key = symbol.upper().strip()
        company_name = (get_offline(key) or {}).get("long_name")
        if not company_name:
            try:
                search = getattr(provider, "search_symbols")
                results = await search(key, 10)
                exact = next(
                    (row for row in results if str(row.get("symbol", "")).upper() == key),
                    None,
                )
                company_name = (exact or {}).get("name")
            except Exception:  # noqa: BLE001
                company_name = None
        try:
            filings = await thai_sec.recent_one_reports(key, company_name or "", limit)
            return {
                "symbol": key,
                "market": "TH",
                "source": "SEC Thailand iDISC",
                "source_url": thai_sec.source_url(),
                "filings": filings,
            }
        except ValueError as exc:
            raise HTTPException(404, str(exc))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"ดึง 56-1 One Report จาก SEC Thailand ไม่สำเร็จ: {exc}")
    try:
        return {
            "symbol": symbol.upper(),
            "market": "US",
            "source": "SEC EDGAR",
            "source_url": "https://www.sec.gov/search-filings",
            "filings": await edgar.recent_filings(symbol, limit),
        }
    except ValueError:
        raise HTTPException(404, "ไทม์ไลน์เอกสาร SEC รองรับเฉพาะหุ้นสหรัฐ")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"ดึงข้อมูล SEC ไม่สำเร็จ: {exc}")


@app.get("/api/business")
async def business_route(symbol: str = Query(..., description="สัญลักษณ์หุ้น เช่น AAPL"),
                         refresh: bool = Query(False, description="True = ให้ AI เรียบเรียงใหม่ทับ cache")):
    """คำอธิบายธุรกิจจาก 10-K (US) หรือ 56-1 One Report (ไทย)."""
    if not is_equity_symbol(symbol):
        raise HTTPException(400, "ใช้ได้กับหุ้นรายตัวเท่านั้น (ไม่รองรับคริปโต/forex/ดัชนี)")
    try:
        return await business_explainer.get_business_explainer(symbol, refresh=refresh)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"สร้างคำอธิบายธุรกิจไม่สำเร็จ: {exc}")


@app.get("/api/macro-analysis")
async def macro_analysis_route(symbol: str = Query(..., description="สัญลักษณ์หุ้น เช่น NVDA"),
                               refresh: bool = Query(False, description="True = ให้ AI วิเคราะห์ใหม่ทับ cache")):
    """วิเคราะห์หุ้นแบบมหภาค→ธุรกิจ (Ray Dalio style): ปัจจัยมหภาค ห่วงโซ่อุปทาน อุปสงค์
    โครงสร้างรายได้/กำไรแยกสินค้า และ sensitivity จาก 10-K (US) / 56-1 One Report (ไทย)."""
    if not is_equity_symbol(symbol):
        raise HTTPException(400, "ใช้ได้กับหุ้นรายตัวเท่านั้น (ไม่รองรับคริปโต/forex/ดัชนี)")
    try:
        return await macro_business.get_macro_analysis(symbol, refresh=refresh)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"วิเคราะห์มหภาค→ธุรกิจไม่สำเร็จ: {exc}")


@app.get("/api/stock-history")
async def stock_history_route(symbol: str = Query(..., description="สัญลักษณ์หุ้น เช่น NVDA, PTT.BK"),
                             refresh: bool = Query(False, description="True = ไล่เหตุการณ์ใหม่ทับ cache")):
    """ไทม์ไลน์ประวัติศาสตร์ราคา: ตรวจจับ 'ช่วงที่ราคาขยับแรง' จากกราฟจริง แล้วให้ AI ไล่ว่า
    แต่ละช่วงเกิดจากเหตุการณ์อะไร (หนุน/กด) พร้อมกลไกและระดับความมั่นใจ — history rhymes."""
    if not is_equity_symbol(symbol):
        raise HTTPException(400, "ใช้ได้กับหุ้นรายตัวเท่านั้น (ไม่รองรับคริปโต/forex/ดัชนี)")
    try:
        return await stock_history.get_stock_history(symbol, refresh=refresh)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"ไล่ประวัติศาสตร์ราคาไม่สำเร็จ: {exc}")


@app.get("/api/trend-radar")
async def trend_radar_route(topic: str = Query("", description="ธีมที่อยากเจาะลึก เช่น 'AI agent', 'หุ่นยนต์' (ว่าง = กวาดกว้างทั้งโลก)"),
                           refresh: bool = Query(False, description="True = สแกนสัญญาณใหม่ + ให้ AI วิเคราะห์ใหม่ทับ cache")):
    """เรดาร์ดักจับเทรนด์โลก: สแกนสัญญาณต้นน้ำ (งานวิจัย/นักพัฒนา/ชุมชน/ข่าว)
    แล้วให้ AI จัดกลุ่มเป็นเทรนด์ที่กำลังโผล่ พร้อมระบุระยะของเทรนด์ อุตสาหกรรมที่จะถูกดิสรัป
    และผู้ได้-เสียประโยชน์ เพื่อช่วยจับ 'ต้นเทรนด์' ก่อนเข้ากระแสหลัก."""
    try:
        return await trend_radar.get_trend_radar(topic, refresh=refresh)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"สแกนเทรนด์ไม่สำเร็จ: {exc}")


@app.get("/api/ai-analyst")
async def ai_analyst_route(
    mode: str = Query(..., description="financial | news | compare | bull_bear | decision"),
    symbol: str = Query("", description="สัญลักษณ์หุ้น (ทุกโหมดยกเว้น compare)"),
    symbols: str = Query("", description="หลายตัวคั่นคอมมา เช่น AAPL,MSFT,GOOGL (เฉพาะ compare)"),
    horizon: str = Query("", description="ระยะเวลาลงทุน (เฉพาะ decision, optional)"),
    risk: str = Query("", description="ระดับรับความเสี่ยง (เฉพาะ decision, optional)"),
    reason: str = Query("", description="เหตุผลที่สนใจหุ้น (เฉพาะ decision, optional)"),
    refresh: bool = Query(False, description="True = วิเคราะห์ใหม่ทับ cache"),
):
    """ผู้ช่วยวิเคราะห์ AI — ดึงข้อมูลจริงออนไลน์เอง (งบ/ข่าว/พื้นฐาน/IV) แล้ววิเคราะห์ 5 โหมด."""
    try:
        return await ai_analyst.get_analysis(
            mode, symbol, symbols=symbols, horizon=horizon, risk=risk,
            reason=reason, refresh=refresh,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"ผู้ช่วยวิเคราะห์ทำงานไม่สำเร็จ: {exc}")


@app.get("/api/offline/status")
async def offline_status_route(symbol: str = Query("", description="ถ้าระบุ จะบอกว่าตัวนี้มีในฐานหรือยัง")):
    """สถานะฐานข้อมูลงบออฟไลน์: ดาวน์โหลดเก็บไว้แล้วกี่ตัว (จากทั้งตลาด US).
    ถ้าส่ง symbol มาด้วย จะบอกว่าหุ้นตัวนั้นมีงบในฐานแล้วหรือยัง (frontend ใช้ตัดสินใจโชว์ปุ่มอัปเดต)."""
    status = await edgar.offline_status()
    if symbol.strip():
        status["symbol"] = symbol.upper().strip()
        status["symbol_offline"] = await edgar.has_offline(symbol)
    return status


@app.get("/api/financials")
async def financials_route(symbol: str = Query(..., description="สัญลักษณ์หุ้น เช่น AAPL"),
                           freq: str = Query("annual", description="annual | quarterly"),
                           refresh: bool = Query(False, description="True = ดึงสดจาก SEC มาทับฐานออฟไลน์ (ปุ่มอัปเดต)")):
    """งบการเงินย้อนหลังลึก (10-15+ ปี) — อ่านจากฐานออฟไลน์ก่อน (เร็ว/ไม่กลัวเน็ตล่ม).
    ตัวที่ยังไม่มีในฐานจะดึงสดอัตโนมัติครั้งแรก; กด refresh=true เพื่อบังคับดึงใหม่ทับของเดิม."""
    if not is_equity_symbol(symbol):
        raise HTTPException(400, "ใช้ได้กับหุ้นรายตัวเท่านั้น (ไม่รองรับคริปโต/forex/ดัชนี)")
    if freq not in ("annual", "quarterly"):
        raise HTTPException(400, "freq ต้องเป็น annual หรือ quarterly")
    try:
        facts = await edgar.get_company_facts(symbol, force_refresh=refresh)
    except ValueError:
        raise HTTPException(404, "งบย้อนหลังลึก (SEC EDGAR) รองรับเฉพาะหุ้นสหรัฐ — "
                                 "หุ้นไทย/ต่างประเทศดูได้เฉพาะสรุปปัจจุบัน (เกรด + เมตริก) ด้านบน")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"ดึงข้อมูล SEC EDGAR ไม่สำเร็จ: {exc}")
    return build_financials(facts, freq)


@app.get("/api/revenue-model")
async def revenue_model_route(symbol: str = Query(..., description="สัญลักษณ์หุ้น เช่น MSFT (หุ้นสหรัฐเท่านั้น)"),
                              refresh: bool = Query(False, description="True = ดึงงบสดจาก SEC ทับ cache"),
                              granularity: str = Query("weekly", description="daily | weekly | monthly"),
                              ai: bool = Query(False, description="True = ให้ AI สรุป verdict ตาม sector (ช้ากว่า ต้องตั้งคีย์ AI)")):
    """โมเดลกราฟ 'Revenue Growth' สไตล์ TrendSpider: กล่อง YoY revenue growth รายไตรมาส
    + กราฟราคา + P/E ย้อนหลัง (ราคา ÷ EPS สะสม 4 ไตรมาส) ที่ความละเอียดที่เลือก
    + จำแนก sector และประเมินมูลค่าด้วย metric ที่เหมาะกับกลุ่มธุรกิจ."""
    try:
        return await revenue_model.get_revenue_model(symbol, refresh=refresh, granularity=granularity,
                                                     include_ai=ai)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"สร้างโมเดลรายได้ไม่สำเร็จ: {exc}")


async def _external_peer_context(symbol: str) -> dict:
    """หา sector/industry/pe/eps/ราคา ของหุ้น 'ภายนอก S&P 500' (ADR ต่างชาติ) เพื่อจับเข้ากลุ่ม
    GICS ให้ตัวเทียบอุตสาหกรรม — Yahoo (sector/industry/pe/eps) + SEC (sicDescription) + ราคาจาก provider.
    ทุกส่วน best-effort: ดึงไม่ได้ก็คืน None ไป (peers_for จะ degrade เอง)."""
    snap: dict = {}
    try:
        snap = await get_fundamentals(symbol) or {}
    except Exception:  # noqa: BLE001
        snap = {}
    sic_desc = None
    try:
        sic_desc = (await edgar.get_submissions(symbol)).get("sicDescription")
    except Exception:  # noqa: BLE001
        pass
    price = await _equity_price_safe(symbol)
    pe, eps = snap.get("pe"), snap.get("eps")
    if price is None and isinstance(pe, (int, float)) and isinstance(eps, (int, float)):
        price = pe * eps  # fallback: ราคา ≈ P/E × EPS (ถ้า provider ให้ราคาไม่ได้)

    def _pct(v):
        return v * 100 if isinstance(v, (int, float)) else None

    return {
        "ext_sector": snap.get("sector"), "ext_industry": snap.get("industry"),
        "ext_sic_desc": sic_desc, "ext_name": snap.get("long_name"),
        "ext_pe": pe, "ext_eps": eps, "ext_price": price,
        "ext_forward_pe": snap.get("forward_pe"),
        "ext_yoy_pct": _pct(snap.get("revenue_growth")),
        "ext_eps_yoy_pct": _pct(snap.get("earnings_growth")),
    }


async def _focus_forward_pe(symbol: str) -> float | None:
    """Forward P/E ระดับหุ้น (จาก Yahoo) สำหรับหุ้น focus ที่อยู่ใน S&P 500 — เอาไปโชว์คู่กับ trailing
    P/E ให้ผู้ใช้เทียบกับเว็บที่พาดหัวเป็น forward (เช่น Seeking Alpha). best-effort, ดึงไม่ได้คืน None."""
    try:
        return (await fetch_yahoo_fundamentals(symbol)).get("forward_pe")
    except Exception:  # noqa: BLE001
        return None


@app.get("/api/industry/peers")
async def industry_peers_route(symbol: str = Query(..., description="สัญลักษณ์หุ้น เช่น NVDA (S&P 500) หรือ ADR เช่น TSM")):
    """เลนส์ 'เทียบอุตสาหกรรม': PE เฉลี่ยของกลุ่ม (GICS Sub-Industry → fallback Sector) +
    ตารางคู่แข่งในอุตสาหกรรมเดียวกัน + ราคาที่ควรเป็นตาม PE กลุ่ม (EPS จริง × PE median).
    หุ้น S&P 500 อ่านจากแคชสแกน — เร็ว ไม่ยิงเน็ต. หุ้น ADR ต่างชาตินอกลิสต์ = ดึง sector/industry
    จาก Yahoo/SEC มาจับเข้ากลุ่ม GICS ของ S&P 500 แล้วเทียบให้ (จับคู่โดยประมาณ)."""
    try:
        ext = {}
        if industry_peers._to_sym(symbol) not in industry_peers.load_constituents():
            ext = await _external_peer_context(symbol)
        else:
            # หุ้น US ในลิสต์: กลุ่ม/คู่แข่งอ่านจากแคช (ไม่ยิงเน็ต) — ยิง Yahoo แค่ค่า forward P/E ของ
            # หุ้น focus ตัวเดียว (cache 30 นาที) เพื่อโชว์คู่กับ trailing ให้เทียบกับ Seeking Alpha ได้
            ext = {"ext_forward_pe": await _focus_forward_pe(symbol)}
        return industry_peers.peers_for(symbol, **ext)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"ดึงข้อมูลอุตสาหกรรมไม่สำเร็จ: {exc}")


@app.get("/api/revenue-scan/sp500")
async def revenue_scan_sp500_route(
    max_gap_pct: float = Query(-15.0, description="แสดงเฉพาะหุ้นที่ราคาต่ำกว่าราคาตามรายได้อย่างน้อยกี่ % (ค่าติดลบ)"),
    limit: int = Query(40, ge=1, le=200),
    refresh: bool = Query(False, description="True = สแกนสดใหม่ทั้ง S&P 500 (ใช้เวลาหลายนาที เหมาะกับรันในเครื่องเท่านั้น)"),
    auto: bool = Query(False, description="True = ถ้าผลเก่ากว่า 24 ชม. ให้สแกนสดใหม่เองอัตโนมัติ (เฉพาะตอนรันในเครื่อง)"),
    min_market_cap: float | None = Query(None, ge=0, description="กรอง market cap ขั้นต่ำ (USD)"),
    max_market_cap: float | None = Query(None, ge=0, description="กรอง market cap สูงสุด (USD)"),
):
    """สแกน S&P 500 หาหุ้นที่ราคาต่ำกว่า 'ราคาตามรายได้' (ตรรกะเดียวกับเส้นม่วงประในแท็บโมเดลรายได้).
    ผลสแกนแคชไว้ในไฟล์ (data_sp500_revenue_scan.json) — เว็ปอ่านแคชทันที ไม่ต้องสแกนสดบนคลาวด์."""
    if min_market_cap is not None and max_market_cap is not None and max_market_cap <= min_market_cap:
        raise HTTPException(400, "max_market_cap ต้องมากกว่า min_market_cap")
    try:
        return await revenue_scanner.scan_sp500_revenue_gap(
            max_gap_pct=max_gap_pct, limit=limit, refresh=refresh, auto=auto,
            min_market_cap=min_market_cap, max_market_cap=max_market_cap,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"สแกน S&P 500 ไม่สำเร็จ: {exc}")


@app.post("/api/revenue-scan/sp500/publish")
async def revenue_scan_sp500_publish_route():
    """สแกนสดใหม่ทั้ง S&P 500 แล้ว commit+push ผลขึ้น GitHub/HF อัตโนมัติ (เหมือน update-offline ของ VI ไทย)
    ใช้ได้เฉพาะตอนรันในเครื่อง (ต้องมี .git + remote hf); บน HF container จะคืน pushed=false"""
    try:
        result = await revenue_scanner.scan_sp500_revenue_gap(refresh=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"สแกน S&P 500 ไม่สำเร็จ: {exc}")
    publish = _git_publish_file(
        "backend/app/data_sp500_revenue_scan.json",
        f"Update S&P 500 revenue scan ({result['success_count']}/{result['universe_count']} companies)",
    )
    return {**result, **publish}


@app.get("/api/value-scan/sp500")
async def value_scan_sp500_route(
    min_upside_pct: float = Query(20.0, description="แสดงเฉพาะหุ้นที่ราคายุติธรรมสูงกว่าราคาปัจจุบันอย่างน้อยกี่ %"),
    limit: int = Query(40, ge=1, le=200),
    profile: str | None = Query(None, description="กรองเฉพาะกลุ่มธุรกิจ (bank/reit/cyclical/... ตาม PROFILES)"),
    refresh: bool = Query(False, description="True = สแกนสดใหม่ทั้ง S&P 500 (หลายนาที เหมาะกับรันในเครื่องเท่านั้น)"),
    auto: bool = Query(False, description="True = ถ้าผลเก่ากว่า 24 ชม. ให้สแกนสดใหม่เองอัตโนมัติ (เฉพาะตอนรันในเครื่อง)"),
    min_market_cap: float | None = Query(None, ge=0, description="กรอง market cap ขั้นต่ำ (USD)"),
    max_market_cap: float | None = Query(None, ge=0, description="กรอง market cap สูงสุด (USD)"),
    exclude_extreme: bool = Query(True, description="True = ซ่อนหุ้นที่ตัวคูณ median สูงผิดปกติ (ฐานประเมินเฟ้อ)"),
):
    """สแกน S&P 500 หาหุ้นที่ราคาต่ำกว่า "ราคายุติธรรมตาม sector" (ตรรกะเดียวกับกล่องประเมินมูลค่า
    ในแท็บ 📈 โมเดลรายได้ — ธนาคารใช้ P/B, REIT ใช้ P/FFO, ทั่วไปใช้ P/E ฯลฯ).
    ผลแคชไว้ในไฟล์ (data_sp500_value_scan.json) — เว็ปอ่านแคชทันที ไม่สแกนสดบนคลาวด์."""
    if min_market_cap is not None and max_market_cap is not None and max_market_cap <= min_market_cap:
        raise HTTPException(400, "max_market_cap ต้องมากกว่า min_market_cap")
    try:
        result = await value_scanner.scan_sp500_fair_value(
            min_upside_pct=min_upside_pct, limit=limit, profile=profile, refresh=refresh, auto=auto,
            min_market_cap=min_market_cap, max_market_cap=max_market_cap,
            exclude_extreme=exclude_extreme,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"สแกนมูลค่ายุติธรรม S&P 500 ไม่สำเร็จ: {exc}")
    # แนบ PE เฉลี่ยอุตสาหกรรมต่อแถว (เทียบกับ P/E ของหุ้นเองในตาราง) — ไม่ล้มถ้า join พลาด
    try:
        result["candidates"] = industry_peers.attach_industry_pe(result.get("candidates", []))
    except Exception:  # noqa: BLE001
        pass
    return result


@app.post("/api/value-scan/sp500/publish")
async def value_scan_sp500_publish_route():
    """สแกนสดใหม่ทั้ง S&P 500 แล้ว commit+push ผลขึ้น GitHub/HF อัตโนมัติ (เหมือน revenue-scan)
    ใช้ได้เฉพาะตอนรันในเครื่อง; บน HF container จะคืน pushed=false"""
    try:
        result = await value_scanner.scan_sp500_fair_value(refresh=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"สแกนมูลค่ายุติธรรม S&P 500 ไม่สำเร็จ: {exc}")
    publish = _git_publish_file(
        "backend/app/data_sp500_value_scan.json",
        f"Update S&P 500 fair-value scan ({result['success_count']}/{result['universe_count']} companies)",
    )
    return {**result, **publish}


@app.get("/api/lynch-scan/sp500")
async def lynch_scan_sp500_route(
    category: str | None = Query(None, description="กรองเฉพาะประเภท Peter Lynch (Fast Grower/Stalwart/Slow Grower/Cyclical/Turnaround/Asset Play)"),
    sector: str | None = Query(None, description="กรองเฉพาะ GICS Sector"),
    limit: int = Query(500, ge=1, le=600),
    refresh: bool = Query(False, description="True = สแกนสดใหม่ทั้ง S&P 500 (หลายนาที เหมาะกับรันในเครื่องเท่านั้น)"),
    auto: bool = Query(False, description="True = ถ้าผลเก่ากว่า 24 ชม. ให้สแกนสดใหม่เองอัตโนมัติ (เฉพาะตอนรันในเครื่อง)"),
    min_market_cap: float | None = Query(None, ge=0, description="กรอง market cap ขั้นต่ำ (USD)"),
    max_market_cap: float | None = Query(None, ge=0, description="กรอง market cap สูงสุด (USD)"),
):
    """สแกน S&P 500 แล้วจัดกลุ่มหุ้นตาม 6 ประเภทของ Peter Lynch (ใช้ตัวจำแนกตัวเดียวกับกล่อง
    'ประเภทหุ้น' ในแท็บมูลค่า IV). ผลแคชไว้ในไฟล์ (data_sp500_lynch_scan.json) — เว็ปอ่านแคชทันที."""
    if min_market_cap is not None and max_market_cap is not None and max_market_cap <= min_market_cap:
        raise HTTPException(400, "max_market_cap ต้องมากกว่า min_market_cap")
    try:
        return await lynch_scanner.scan_sp500_lynch(
            category=category, sector=sector, limit=limit, refresh=refresh, auto=auto,
            min_market_cap=min_market_cap, max_market_cap=max_market_cap,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"สแกนจัดกลุ่ม Peter Lynch S&P 500 ไม่สำเร็จ: {exc}")


@app.post("/api/lynch-scan/sp500/publish")
async def lynch_scan_sp500_publish_route():
    """สแกนสดใหม่ทั้ง S&P 500 แล้ว commit+push ผลขึ้น GitHub/HF อัตโนมัติ (เหมือน value-scan)
    ใช้ได้เฉพาะตอนรันในเครื่อง; บน HF container จะคืน pushed=false"""
    try:
        result = await lynch_scanner.scan_sp500_lynch(refresh=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"สแกนจัดกลุ่ม Peter Lynch S&P 500 ไม่สำเร็จ: {exc}")
    publish = _git_publish_file(
        "backend/app/data_sp500_lynch_scan.json",
        f"Update S&P 500 Peter Lynch classification scan ({result['success_count']}/{result['universe_count']} companies)",
    )
    return {**result, **publish}


@app.get("/api/sp500-history/meta")
async def sp500_history_meta_route():
    """รายการปีที่มี + coverage ต่อปีของประวัติผลตอบแทน S&P 500 (survivorship-aware)."""
    return sp500_history.meta()


@app.get("/api/sp500-history/year")
async def sp500_history_year_route(
    year: int = Query(..., description="ปีที่ต้องการดูอันดับ (เช่น 2020)"),
    band: float = Query(sp500_history.DEFAULT_BAND_PCT, ge=0, le=100,
                        description="เหนือ/ต่ำกว่าตลาดเมื่อผลตอบแทนต่างจากดัชนีเกิน ±band (percentage points)"),
):
    """อันดับหุ้น 1..N ของปีนั้น + tier (เหนือ/ใกล้/ต่ำกว่าตลาด) + จุดกราฟผลตอบแทนเรียงมาก→น้อย."""
    return sp500_history.year_ranking(year, band=band)


@app.get("/api/sp500-history/longevity")
async def sp500_history_longevity_route(
    top: int = Query(50, ge=1, le=500, description="นับหุ้นที่ติด Top X อันดับแรกของแต่ละปี"),
    years: int = Query(20, ge=1, le=40, description="ย้อนหลังกี่ปีล่าสุด"),
    band: float = Query(sp500_history.DEFAULT_BAND_PCT, ge=0, le=100),
):
    """หุ้นที่ยืนระยะติด Top X ได้กี่ปีในช่วง Y ปีล่าสุด (เรียงหาแชมป์ยืนระยะ)."""
    return sp500_history.longevity(top=top, years_back=years, band=band)


@app.get("/api/sp500-history/stock")
async def sp500_history_stock_route(
    symbol: str = Query(..., description="สัญลักษณ์หุ้น เช่น AAPL"),
    band: float = Query(sp500_history.DEFAULT_BAND_PCT, ge=0, le=100),
):
    """ไทม์ไลน์อันดับ/ผลตอบแทน/tier ของหุ้นตัวเดียวทุกปี (เห็นเส้นทางรุ่ง→ร่วง)."""
    return sp500_history.stock_timeline(symbol, band=band)


@app.post("/api/sp500-history/build")
async def sp500_history_build_route():
    """build dataset ใหม่ (ยิง Yahoo ~1,200 ตัว หลายนาที) แล้ว commit+push ขึ้น GitHub/HF อัตโนมัติ
    ใช้ได้เฉพาะตอนรันในเครื่อง; บน HF container จะคืน pushed=false"""
    try:
        result = await sp500_history.build_dataset()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"build ประวัติ S&P 500 ไม่สำเร็จ: {exc}")
    publish = _git_publish_file(
        "backend/app/data_sp500_history.json",
        f"Update S&P 500 yearly-return history ({len(result.get('years', {}))} years, universe {result.get('universe_size')})",
    )
    return {"years": len(result.get("years", {})), "universe_size": result.get("universe_size"), **publish}


@app.get("/api/ema-scan/sp500")
async def ema_scan_sp500_route(
    period: int = Query(200, description="เส้น EMA ที่จะวัดระยะห่าง (50 / 100 / 200)"),
    tolerance_pct: float = Query(3.0, gt=0, le=25, description="ถือว่า 'ใกล้เส้น' เมื่ออยู่ในโซน ±กี่ %"),
    side: str = Query("both", pattern="^(both|above|below)$", description="both = ทั้งสองฝั่ง · above = เหนือเส้น · below = ใต้เส้น"),
    trend: str = Query("any", pattern="^(any|up|down)$", description="กรองตามความชัน EMA200 (up = เส้นชี้ขึ้น)"),
    limit: int = Query(40, ge=1, le=200),
    refresh: bool = Query(False, description="True = สแกนสดใหม่ทั้ง S&P 500 (เหมาะกับรันในเครื่องเท่านั้น)"),
    auto: bool = Query(False, description="True = ถ้าผลเก่ากว่า 12 ชม. ให้สแกนสดใหม่เองอัตโนมัติ (เฉพาะตอนรันในเครื่อง)"),
    min_market_cap: float | None = Query(None, ge=0, description="กรอง market cap ขั้นต่ำ (USD)"),
    max_market_cap: float | None = Query(None, ge=0, description="กรอง market cap สูงสุด (USD)"),
):
    """สแกน S&P 500 หาหุ้นที่ราคา "ลงมาใกล้" เส้น EMA 50/100/200 (เลนส์สายเทคนิค ไม่แตะงบการเงิน).
    ผลแคชไว้ในไฟล์ (data_sp500_ema_scan.json) — เว็ปอ่านแคชทันที ไม่สแกนสดบนคลาวด์."""
    if min_market_cap is not None and max_market_cap is not None and max_market_cap <= min_market_cap:
        raise HTTPException(400, "max_market_cap ต้องมากกว่า min_market_cap")
    try:
        return await ema_scanner.scan_sp500_ema(
            period=period, tolerance_pct=tolerance_pct, side=side, trend=trend, limit=limit,
            refresh=refresh, auto=auto, min_market_cap=min_market_cap, max_market_cap=max_market_cap,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"สแกน EMA S&P 500 ไม่สำเร็จ: {exc}")


@app.post("/api/ema-scan/sp500/publish")
async def ema_scan_sp500_publish_route():
    """สแกน EMA สดใหม่ทั้ง S&P 500 แล้ว commit+push ขึ้น GitHub/HF อัตโนมัติ (เหมือนสแกนตัวอื่น)
    ใช้ได้เฉพาะตอนรันในเครื่อง; บน HF container จะคืน pushed=false"""
    try:
        result = await ema_scanner.scan_sp500_ema(refresh=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"สแกน EMA S&P 500 ไม่สำเร็จ: {exc}")
    publish = _git_publish_file(
        "backend/app/data_sp500_ema_scan.json",
        f"Update S&P 500 EMA scan ({result['success_count']}/{result['universe_count']} companies)",
    )
    return {**result, **publish}


@app.get("/api/confluence/sp500")
async def confluence_sp500_route(
    min_agree: int = Query(2, ge=1, le=3, description="แสดงเฉพาะหุ้นที่มีอย่างน้อยกี่เลนส์เห็นตรงกัน"),
    max_gap_pct: float = Query(-15.0, description="เกณฑ์ผ่านของเลนส์รายได้ (ราคาต่ำกว่าที่รายได้บ่งชี้กี่ %)"),
    min_upside_pct: float = Query(20.0, description="เกณฑ์ผ่านของเลนส์มูลค่ายุติธรรม (upside ขั้นต่ำ)"),
    ema_zone_pct: float = Query(3.0, gt=0, le=25, description="เกณฑ์ผ่านของเลนส์ EMA (อยู่ในโซน ±กี่ % ของ EMA200)"),
    limit: int = Query(60, ge=1, le=200),
    min_market_cap: float | None = Query(None, ge=0, description="กรอง market cap ขั้นต่ำ (USD)"),
    max_market_cap: float | None = Query(None, ge=0, description="กรอง market cap สูงสุด (USD)"),
):
    """รวมผลสแกนทุกเลนส์ (รายได้ / มูลค่ายุติธรรม / EMA) เป็นตารางเดียว เรียงตามจำนวนเลนส์ที่เห็นตรงกัน.
    อ่านจากไฟล์แคชของแต่ละสแกน — ไม่สแกนใหม่เอง จึงตอบเร็วเสมอ (เลนส์ไหนยังไม่เคยสแกน จะขึ้นว่า
    'ยังไม่มีข้อมูล' ไม่ใช่ 'ไม่ผ่าน')."""
    if min_market_cap is not None and max_market_cap is not None and max_market_cap <= min_market_cap:
        raise HTTPException(400, "max_market_cap ต้องมากกว่า min_market_cap")
    try:
        return await confluence.scan_confluence(
            min_agree=min_agree, max_gap_pct=max_gap_pct, min_upside_pct=min_upside_pct,
            ema_zone_pct=ema_zone_pct, limit=limit,
            min_market_cap=min_market_cap, max_market_cap=max_market_cap,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"รวมผลสแกนไม่สำเร็จ: {exc}")


@app.post("/api/analyze-fundamentals", response_model=ValueReport)
async def analyze_fundamentals_route(req: AnalyzeFundamentalsRequest):
    """สาย VI: ดึงปัจจัยพื้นฐาน → ประเมินด้านเชิงตัวเลข (Python) + เชิงคุณภาพ (Claude) → เกรด A–F."""
    if not is_equity_symbol(req.symbol):
        raise HTTPException(400, "สาย VI ใช้ได้กับหุ้นรายตัวเท่านั้น (ไม่รองรับคริปโต/forex/ดัชนี)")
    try:
        snapshot = await _value_snapshot(req.symbol)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"ดึงข้อมูลพื้นฐานไม่สำเร็จ: {exc}")

    py_verdicts = evaluate_value_schools(snapshot)
    if req.enabled_schools is not None:
        py_verdicts = [v for v in py_verdicts if v["id"] in req.enabled_schools]
    try:  # ดึงส่วน Risk Factors/MD&A จาก 10-K จริงมา ground ให้ AI (เฉพาะหุ้น US ที่ยื่น SEC)
        doc = await edgar.get_10k_context(req.symbol)
    except Exception:  # noqa: BLE001
        doc = None
    ai = await analyze_fundamentals_ai(req.symbol, snapshot, note=req.note,
                                       doc_context=doc, only_ids=req.enabled_schools)
    verdicts = py_verdicts + ai["verdicts"]

    return ValueReport(**build_value_report(
        verdicts, snapshot, ai_enabled=settings.llm_enabled(),
        summary=ai.get("summary"), weights=req.weights,
        ai_status=ai.get("ai_status"), ai_as_of=ai.get("ai_as_of"),
    ))


@app.get("/api/intrinsic-value")
async def intrinsic_value_route(symbol: str = Query(...)):
    """คำนวณมูลค่าที่แท้จริง (IV) ด้วย DCF / Graham / Peter Lynch / DDM / P/E"""
    if not is_equity_symbol(symbol):
        raise HTTPException(400, "ใช้ได้กับหุ้นรายตัวเท่านั้น")
    try:
        snapshot = await _value_snapshot(symbol)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"ดึงข้อมูลพื้นฐานไม่สำเร็จ: {exc}")
    # ลอง SEC EDGAR สำหรับ FCF ย้อนหลัง (เฉพาะหุ้น US — ถ้าล้มเหลวใช้ snapshot อย่างเดียว)
    financials = None
    try:
        facts = await edgar.get_company_facts(symbol)
        from app.financials import build_financials
        financials = build_financials(facts, "annual")
    except Exception:  # noqa: BLE001
        pass
    return build_iv_report(snapshot, financials)


def _git_publish_file(rel: str, message: str) -> dict:
    """commit + push ไฟล์ (เช่น snapshot ออฟไลน์/ผลสแกน) ขึ้น GitHub + HF (เว็ป). ใช้ได้เฉพาะที่มี
    git/remote (เครื่องผู้ใช้). บน HF container ไม่มี .git → คืน pushed=false อย่างนุ่มนวล."""
    import subprocess
    repo = str(FRONTEND_DIR.parent)

    def run(args, timeout):
        return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=timeout)

    try:
        run(["add", rel], 30)
        c = run(["commit", "-m", message, "--", rel], 30)
        if c.returncode != 0 and "nothing to commit" in (c.stdout + c.stderr).lower():
            return {"pushed": False, "error": "ไม่มีการเปลี่ยนแปลง (ข้อมูลเดิมอยู่แล้ว)"}
        p1 = run(["push", "origin", "HEAD"], 150)
        p2 = run(["push", "hf", "HEAD:main"], 200)
        if p2.returncode == 0:
            return {"pushed": True, "error": None}
        return {"pushed": False, "error": (p2.stderr or p1.stderr or "push ล้มเหลว")[:200]}
    except Exception as exc:  # noqa: BLE001
        return {"pushed": False, "error": str(exc)[:200]}


def _git_publish_offline(symbol: str) -> dict:
    return _git_publish_file(
        "backend/app/offline_fundamentals.json",
        f"Update offline fundamentals: {symbol.upper()}",
    )


@app.post("/api/fundamentals/update-offline")
async def update_offline_route(symbol: str = Query(..., description="สัญลักษณ์หุ้น เช่น KBANK.BK")):
    """ดึงข้อมูลพื้นฐานสด → เซฟ snapshot ออฟไลน์ → push ขึ้นเว็ปอัตโนมัติ.
    ใช้ได้เฉพาะตอนรันในเครื่อง (Yahoo เข้าถึงได้); บนเว็ปจะแจ้งว่าอัปเดตไม่ได้."""
    if not is_equity_symbol(symbol):
        raise HTTPException(400, "ใช้ได้กับหุ้นรายตัวเท่านั้น")
    try:
        snap = await update_offline(symbol)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, "อัปเดตได้เฉพาะตอนรันในเครื่อง (เว็ปดึง Yahoo สำหรับหุ้นไทยไม่ได้): "
                                 + str(exc)[:120])
    # คำนวณ + cache คำวิเคราะห์ AI เชิงคุณภาพ ลง snapshot ด้วย (จะได้ไปโชว์บนเว็ปแม้ quota หมด)
    ai_cached = False
    try:
        try:
            doc = await edgar.get_10k_context(symbol)
        except Exception:  # noqa: BLE001
            doc = None
        ai = await analyze_fundamentals_ai(symbol, {**snap, "_source": "yahoo"}, doc_context=doc)
        if ai.get("ai_status") == "live" and ai.get("verdicts"):
            save_ai_qualitative(symbol, ai["verdicts"], ai.get("summary"))
            ai_cached = True
    except Exception:  # noqa: BLE001
        pass
    pub = await asyncio.to_thread(_git_publish_offline, symbol)
    return {
        "updated": True,
        "symbol": symbol.upper(),
        "long_name": snap.get("long_name"),
        "as_of": snap.get("fetched_at"),
        "ai_cached": ai_cached,
        "published": pub.get("pushed"),
        "publish_detail": pub.get("error") or "push ขึ้นเว็ปแล้ว (เว็ป rebuild ~3-8 นาที)",
    }


# ---------- Backtest (ท่าไม้ตาย) ----------
@app.post("/api/backtest")
async def backtest_route(req: BacktestRequest):
    """ท่าไม้ตาย: รวมทุกศาสตร์ (เชิงสูตร) → backtest ย้อนหลังยาว → สถิติ + จุดเข้า-ออก."""
    if req.timeframe not in settings.timeframes:
        raise HTTPException(400, f"timeframe ไม่รองรับ: {req.timeframe}")

    get_history = getattr(provider, "get_history", None)
    if _is_bitkub(req.symbol):
        data = await bitkub_client.candles(req.symbol, req.timeframe, 2000)
    elif get_history:
        try:
            data = await get_history(req.symbol, req.timeframe)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"ดึงข้อมูลย้อนหลังไม่สำเร็จ: {exc}")
    else:
        data = await _candles_for(req.symbol, req.timeframe, 2000)

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

    data = await _candles_for(req.symbol, req.timeframe, 400)
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


@app.get("/api/autotrade/history")
async def autotrade_history(limit: int = Query(100, ge=1, le=500)):
    return await store.list_autotrade_history(limit)


@app.get("/api/model/status")
async def model_status():
    model = load_model()
    if not model:
        return {"available": False, "path": str(DEFAULT_MODEL_PATH)}
    return {
        "available": True,
        "path": str(DEFAULT_MODEL_PATH),
        "model_id": model.get("model_id"),
        "trained_at": model.get("trained_at"),
        "model_type": model.get("model_type"),
        "label": model.get("label"),
        "metrics": model.get("metrics"),
        "sources_count": len(model.get("sources") or []),
    }


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
async def autotrade_tick(req: AutoTradeConfig | None = None):
    try:
        return await auto_trade.tick_once(req)
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
            q = await _quote_for(symbol)
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
                data = await _candles_for(sym, "1h", 60)
                if not data:
                    continue
                ind = compute_indicators(data)
                rsi_val = ind["summary"].get("rsi14")
                fired = alerts.evaluate(sym, data[-1].close, rsi_val, settings.alert_notify_email)
                for result in fired:
                    print("[ALERT]", result.alert.message)
                    if result.notify_email and settings.gmail_user and settings.gmail_app_password:
                        subject = f"🔔 Alert: {result.alert.message}"
                        body = (
                            f"การแจ้งเตือนจาก AI Trade Assistant\n\n"
                            f"หุ้น: {result.alert.symbol}\n"
                            f"เงื่อนไข: {result.alert.kind} {result.alert.value}\n"
                            f"ค่าปัจจุบัน: {result.alert.observed}\n\n"
                            f"⚠️ นี่คือการแจ้งเตือนอัตโนมัติ — ไม่ใช่คำแนะนำลงทุน"
                        )
                        asyncio.create_task(_send_alert_email_async(result.notify_email, subject, body))
        except Exception as e:  # noqa: BLE001
            print("alert loop error:", e)
        await asyncio.sleep(10)


async def _send_alert_email_async(to: str, subject: str, body: str) -> None:
    from app.email_notify import send_alert_email
    loop = asyncio.get_event_loop()
    ok = await loop.run_in_executor(
        None,
        lambda: send_alert_email(
            to_email=to, subject=subject, body=body,
            from_user=settings.gmail_user,
            app_password=settings.gmail_app_password,
        ),
    )
    if ok:
        print(f"[EMAIL] sent alert to {to}")
    else:
        print(f"[EMAIL] failed to send to {to}")


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
        return FileResponse(idx, headers=NO_CACHE_HEADERS)
    return {"message": "AI Trade Assistant API. ดู /docs สำหรับ API"}


@app.get("/config.js")
async def frontend_config():
    cfg = FRONTEND_DIR / "config.js"
    if cfg.exists():
        return FileResponse(cfg, media_type="application/javascript", headers=NO_CACHE_HEADERS)
    return JSONResponse({}, media_type="application/javascript", headers=NO_CACHE_HEADERS)
