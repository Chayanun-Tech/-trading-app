"""Provider ราคาจริงผ่าน Yahoo Finance chart API (ไม่ต้องใช้ API key).

รองรับทั้งหุ้นสหรัฐ (AAPL) และหุ้นไทย (ใช้รูปแบบ .BK เช่น PTT.BK, KBANK.BK)
รวมถึง crypto (BTC-USD), ดัชนี (^SET.BK ฯลฯ).

หมายเหตุ: เป็น endpoint สาธารณะของ Yahoo ใช้ได้สำหรับการศึกษา/ส่วนตัว
ข้อมูล intraday ของ Yahoo มีข้อจำกัดย้อนหลัง (เช่น 1m ~7วัน, 60m ~730วัน).
Yahoo ไม่มี 4h โดยตรง — ระบบจะดึง 1h แล้วรวมเป็น 4h ให้.
"""
from __future__ import annotations

import asyncio
import time

import httpx

from app.data.base import DataProvider
from app.schemas import Candle, Quote

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AI-Trade-Assistant/1.0)"}

# timeframe -> (interval ของ yahoo, range, ต้องรวมเป็น 4h กี่แท่ง)
_MAP = {
    "1m": ("1m", "5d", 1),
    "5m": ("5m", "1mo", 1),
    "15m": ("15m", "1mo", 1),
    "1h": ("60m", "3mo", 1),
    "4h": ("60m", "1y", 4),   # ดึง 1h แล้วรวม 4 แท่ง
    "1d": ("1d", "2y", 1),
}

# timeframe -> (interval, range สูงสุดที่ yahoo ให้ย้อนหลังได้, group) สำหรับ backtest ยาว ๆ
_HISTORY_MAP = {
    "5m": ("5m", "60d", 1),
    "15m": ("15m", "60d", 1),
    "1h": ("60m", "730d", 1),
    "4h": ("60m", "730d", 4),
    "1d": ("1d", "20y", 1),    # daily ย้อนหลัง ~20 ปี (range=max จะถูก yahoo ลดเป็นรายเดือน)
}


# สัญลักษณ์ที่ Yahoo ไม่รองรับ → map ไปตัวที่ใช้ได้จริง (เช่น ทอง/เงิน spot ที่ Yahoo ไม่มี)
# ผู้ใช้พิมพ์/กด XAUUSD=X (ทองคำ) → route ไป PAXG-USD (PAX Gold = ทอง spot, 1 PAXG = 1 oz)
# ราคา PAXG เกาะ XAUUSD spot (ต่าง ~$1-3) และ Binance สตรีมเรียลไทม์ได้ — ใกล้ความจริงกว่า GC=F (futures, ต่าง ~$25)
_SYMBOL_ALIASES = {
    "XAUUSD=X": "PAXG-USD", "XAUUSD": "PAXG-USD", "XAU=X": "PAXG-USD", "GOLD=X": "PAXG-USD",
    "XAU/USD": "PAXG-USD", "GOLD": "PAXG-USD",
    "XAGUSD=X": "SI=F", "XAGUSD": "SI=F", "XAG=X": "SI=F", "SILVER": "SI=F",
}


def _normalize_symbol(symbol: str) -> str:
    """แปลงสัญลักษณ์ที่ Yahoo ไม่รองรับให้เป็นตัวที่ดึงข้อมูลได้จริง (อื่น ๆ คืนเดิม)."""
    return _SYMBOL_ALIASES.get((symbol or "").strip().upper(), (symbol or "").strip())


class YahooProvider(DataProvider):
    name = "yahoo"
    BASE = "https://query1.finance.yahoo.com/v8/finance/chart"
    QUOTE_BASE = "https://query1.finance.yahoo.com/v7/finance/quote"
    SEARCH_BASE = "https://query2.finance.yahoo.com/v1/finance/search"

    async def _fetch(self, symbol: str, interval: str, rng: str, timeout: float = 15.0) -> dict:
        params = {"interval": interval, "range": rng, "includePrePost": "false"}
        async with httpx.AsyncClient(timeout=timeout, headers=_HEADERS) as client:
            r = await client.get(f"{self.BASE}/{symbol}", params=params)
            r.raise_for_status()
            return r.json()

    @staticmethod
    def _resample_4h(candles: list[Candle]) -> list[Candle]:
        out: list[Candle] = []
        for i in range(0, len(candles), 4):
            grp = candles[i:i + 4]
            if not grp:
                continue
            out.append(Candle(
                time=grp[0].time, open=grp[0].open,
                high=max(c.high for c in grp), low=min(c.low for c in grp),
                close=grp[-1].close, volume=sum(c.volume for c in grp)))
        return out

    async def get_candles(self, symbol: str, timeframe: str, limit: int) -> list[Candle]:
        interval, rng, group = _MAP.get(timeframe, ("60m", "3mo", 1))
        data = await self._fetch(_normalize_symbol(symbol), interval, rng)
        result = (data.get("chart", {}).get("result") or [None])[0]
        if not result:
            return []
        ts = result.get("timestamp") or []
        q = (result.get("indicators", {}).get("quote") or [{}])[0]
        opens, highs = q.get("open", []), q.get("high", [])
        lows, closes, vols = q.get("low", []), q.get("close", []), q.get("volume", [])

        out: list[Candle] = []
        for i, t in enumerate(ts):
            o, h, low, c = opens[i], highs[i], lows[i], closes[i]
            if None in (o, h, low, c):   # ข้ามแท่งที่ข้อมูลไม่ครบ (ช่วงตลาดปิด)
                continue
            out.append(Candle(time=int(t), open=round(o, 4), high=round(h, 4),
                              low=round(low, 4), close=round(c, 4),
                              volume=float(vols[i] or 0)))
        if group > 1:
            out = self._resample_4h(out)
        return out[-limit:]

    async def get_history(self, symbol: str, timeframe: str, max_bars: int = 8000) -> list[Candle]:
        """ดึงข้อมูลย้อนหลัง 'ยาวที่สุด' สำหรับ backtest (daily = ทั้งหมดที่ yahoo มี)."""
        interval, rng, group = _HISTORY_MAP.get(timeframe, ("1d", "max", 1))
        data = await self._fetch(_normalize_symbol(symbol), interval, rng, timeout=30.0)
        result = (data.get("chart", {}).get("result") or [None])[0]
        if not result:
            return []
        ts = result.get("timestamp") or []
        q = (result.get("indicators", {}).get("quote") or [{}])[0]
        opens, highs = q.get("open", []), q.get("high", [])
        lows, closes, vols = q.get("low", []), q.get("close", []), q.get("volume", [])
        out: list[Candle] = []
        for i, t in enumerate(ts):
            o, h, low, c = opens[i], highs[i], lows[i], closes[i]
            if None in (o, h, low, c):
                continue
            out.append(Candle(time=int(t), open=round(o, 4), high=round(h, 4),
                              low=round(low, 4), close=round(c, 4),
                              volume=float(vols[i] or 0)))
        if group > 1:
            out = self._resample_4h(out)
        return out[-max_bars:]

    @staticmethod
    def _quote_from_row(row: dict, fallback_symbol: str = "") -> Quote | None:
        symbol = row.get("symbol") or fallback_symbol
        price = row.get("regularMarketPrice")
        if price is None:
            price = row.get("postMarketPrice") or row.get("preMarketPrice")
        if price is None:
            return None

        prev = row.get("regularMarketPreviousClose") or row.get("chartPreviousClose") or price
        change = row.get("regularMarketChange")
        if change is None:
            change = float(price) - float(prev)
        pct = row.get("regularMarketChangePercent")
        if pct is None:
            pct = (float(change) / float(prev) * 100) if prev else 0.0
        market_time = row.get("regularMarketTime") or row.get("postMarketTime") or int(time.time())
        return Quote(
            symbol=symbol,
            price=round(float(price), 4),
            time=int(market_time),
            change=round(float(change), 4),
            change_percent=round(float(pct), 2),
        )

    async def search_symbols(self, query: str, limit: int = 50) -> list[dict]:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(5.0, connect=1.5),
            headers=_HEADERS,
        ) as client:
            res = await client.get(
                self.SEARCH_BASE,
                params={"q": query, "quotesCount": limit, "newsCount": 0},
            )
            res.raise_for_status()
            payload = res.json()

        rows: list[dict] = []
        seen: set[str] = set()
        allowed_types = {
            "EQUITY", "ETF", "MUTUALFUND", "INDEX", "CRYPTOCURRENCY",
            "FUTURE", "CURRENCY", "OPTION",
        }
        for item in payload.get("quotes", []):
            symbol = item.get("symbol")
            quote_type = item.get("quoteType")
            if not symbol or symbol in seen:
                continue
            if quote_type and quote_type not in allowed_types:
                continue
            seen.add(symbol)
            rows.append({
                "symbol": symbol,
                "name": item.get("shortname") or item.get("longname") or item.get("name") or symbol,
                "market": item.get("exchange") or quote_type or "YAHOO",
            })
            if len(rows) >= limit:
                break
        return rows

    async def get_quotes(self, symbols: list[str]) -> list[Quote]:
        originals = [s.strip().upper() for s in symbols if s.strip()]
        if not originals:
            return []
        # map ตัวที่ Yahoo ไม่รองรับ → ตัวที่ใช้ได้ แต่คืนผลด้วยสัญลักษณ์เดิมที่ผู้ใช้ขอ
        norm_map = {o: _normalize_symbol(o).upper() for o in originals}
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(3.0, connect=1.0),
                headers=_HEADERS,
            ) as client:
                res = await client.get(self.QUOTE_BASE,
                                       params={"symbols": ",".join(sorted(set(norm_map.values())))})
                res.raise_for_status()
                payload = res.json()
            by_norm: dict[str, Quote] = {}
            for row in payload.get("quoteResponse", {}).get("result", []):
                quote = self._quote_from_row(row)
                if quote:
                    by_norm[quote.symbol.upper()] = quote
            out = []
            for orig in originals:
                q = by_norm.get(norm_map[orig])
                if q:
                    out.append(Quote(symbol=orig, price=q.price, time=q.time,
                                     change=q.change, change_percent=q.change_percent))
            if len(out) == len(originals):
                return out
        except Exception:
            pass
        # fallback: ดึงทีละตัวจาก chart API (คืน symbol เดิมอยู่แล้ว)
        results = await asyncio.gather(
            *(self._get_quote_from_chart(symbol) for symbol in originals),
            return_exceptions=True,
        )
        return [item for item in results if isinstance(item, Quote)]

    async def _get_quote_from_chart(self, symbol: str) -> Quote:
        # ดึงด้วยสัญลักษณ์ที่ใช้ได้จริง แต่คืน symbol เดิมที่ผู้ใช้ขอ (ให้ frontend จับคู่ถูก)
        data = await self._fetch(_normalize_symbol(symbol), "1d", "5d", timeout=5.0)
        result = (data.get("chart", {}).get("result") or [None])[0]
        if not result:
            return Quote(symbol=symbol, price=0.0, time=int(time.time()))
        meta = result.get("meta", {})
        price = meta.get("regularMarketPrice") or 0.0
        prev = meta.get("chartPreviousClose") or meta.get("previousClose") or price
        change = price - prev
        pct = (change / prev * 100) if prev else 0.0
        return Quote(symbol=symbol, price=round(price, 4),
                     time=int(meta.get("regularMarketTime", time.time())),
                     change=round(change, 4), change_percent=round(pct, 2))

    async def get_quote(self, symbol: str) -> Quote:
        return await self._get_quote_from_chart(symbol)
