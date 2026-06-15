# Project Memory / Handoff

Last updated: 2026-06-14 00:20 Asia/Bangkok

## Project

Trading app at:

`C:\Users\Super Asus\Desktop\งานของชยานันต์\AI\ChayanunOperating\trading-app`

App purpose: TradingView-like AI Trade Assistant. It serves a FastAPI backend and a single-page frontend at `frontend/index.html`. The user wants US stocks, Thai stocks, crypto, gold/futures, real-time-ish prices, watchlist, customizable chart/indicator controls, and all existing "schools"/ศาสตร์ analysis must be preserved.

## How To Run

Backend working directory:

`C:\Users\Super Asus\Desktop\งานของชยานันต์\AI\ChayanunOperating\trading-app\backend`

Command:

```powershell
.\.venv\Scripts\python.exe -B -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --log-level warning
```

Current local URL:

`http://127.0.0.1:8000/`

For phone/tablet on the same Wi-Fi, run with `--host 0.0.0.0` and open `http://<PC-LAN-IP>:8000` from the device. Public deployment needs a real backend host such as Render/Railway/Fly/VPS; GitHub Pages alone is not enough because this is not a static-only app.

## Current Server State

As of the last Codex turn, a background uvicorn server was running on port `8000` with PID around `15280`.

Check:

```powershell
netstat -ano | Select-String "127.0.0.1:8000"
```

Stop if needed:

```powershell
Stop-Process -Id <PID> -Force
```

## Important Dirty Worktree Note

The repo already has many modified/untracked files from Claude Code and current Codex work. Do not revert unknown changes. Current status included:

```text
 M BLUEPRINT.md
 M README.md
 M backend/.env.example
 M backend/app/analysis.py
 M backend/app/config.py
 M backend/app/indicators.py
 M backend/app/main.py
 M backend/app/schemas.py
 M backend/requirements.txt
 M frontend/index.html
?? .claude/
?? backend/app/data/yahoo.py
?? backend/app/engine.py
?? backend/app/knowledge/
?? backend/app/knowledge_base.py
?? backend/app/llm.py
?? backend/app/schools.py
?? backend/app/vision.py
?? trading-app/
```

Remote exists:

```text
origin https://github.com/chayanun250841/-.git
```

Do not push without user confirmation because the remote repo name is unusual and the working tree contains a lot of pre-existing work.

## Latest User Requirements

The user specifically asked:

1. Make graph prices update like TradingView; current chart price looked stuck.
2. When changing timeframe, indicators must change/recalculate for that timeframe.
3. Add custom indicator/chart style controls, like TradingView.
4. Allow changing chart type and candlestick colors.
5. Preserve all existing "schools"/ศาสตร์ analysis.
6. Write ongoing work into `memory.md` so Codex and Claude Code can swap back and forth.

## What Was Implemented Recently

### Dynamic Yahoo Search

Files:

- `backend/app/main.py`
- `backend/app/data/yahoo.py`
- `frontend/index.html`

Behavior:

- `/api/search-symbols?q=...&limit=...` searches Yahoo Finance dynamically, not just local samples.
- Results are cached in backend for 10 minutes.
- Ranking was improved so relevant symbols come first:
  - `ptt` returns `PTT.BK` first.
  - `k` returns `KBANK.BK`, `KTB.BK`, `KO` before random currencies.
  - `sony` returns `SONY`, `6758.T`, etc. from Yahoo.
- Frontend search requests `limit:60`.

### Yahoo Quote / Bulk Quotes

File:

- `backend/app/data/yahoo.py`

Behavior:

- `YahooProvider.get_quotes(symbols)` tries Yahoo quote API first.
- If quote API returns empty/slow, it falls back to Yahoo chart API in parallel.
- `YahooProvider.get_quote(symbol)` now uses chart API directly for a single symbol, because this was more reliable for live badge/chart updates.

Tested outputs:

- `/api/quote?symbol=BTC-USD` returned a price.
- `/api/quotes?symbols=AAPL,KO,BTC-USD,PTT.BK` returned all prices during testing.

### Chart Live Update

File:

- `frontend/index.html`

Behavior:

- Top badge quote refreshes every `2` seconds.
- `refreshQuote()` calls `applyLiveQuote(q)`, which updates the last candle's `close/high/low` in memory and calls `candleSeries.update(...)`. This makes the chart move before a full candle reload.
- Full candle refresh timer now exists:
  - Crypto / FX / Futures / `1m` / `5m`: every `5` seconds
  - `15m`: every `10` seconds
  - Larger timeframes: every `15` seconds
- Watchlist refresh remains every `5` seconds.
- A status label in the toolstrip shows live state, e.g. `Live: every 15s` or timestamp after update.

### Timeframe + Indicators

File:

- `frontend/index.html`

Behavior:

- Timeframe select now calls `onTimeframeChange()`.
- On timeframe change:
  - Clears existing chart/indicator series.
  - Calls `loadChart({ fit:true })`.
  - Resets the auto-refresh interval based on new timeframe.
- `loadChart()` sends timeframe and all indicator params to `/api/candles`.
- Backend `/api/candles` already accepts indicator params and computes indicators for that candle/timeframe data.
- Browser test changed AAPL from `1h` to `5m`; summary/indicator values changed and title became `AAPL / 5m`.

### TradingView-ish Chart Customization

File:

- `frontend/index.html`

New controls are in the `Indicators` tab:

- Chart type:
  - `Candles`
  - `Bars`
  - `Area`
  - `Line`
  - `Baseline`
- Chart style:
  - chart background
  - text color
  - grid color
  - bull candle
  - bear candle
  - wick up
  - wick down
  - volume color
- Indicator colors:
  - EMA fast
  - EMA mid
  - EMA slow
  - Bollinger
  - RSI
  - Stoch K
  - Stoch D
  - MACD
  - Signal

Values are stored in browser `localStorage` under `chartStyle`.

Implementation details:

- `defaultChartStyle`
- `chartStyle`
- `hydrateStyleControls()`
- `createPriceSeries()`
- `applyPriceSeriesStyle()`
- `applyChartStyle()`
- `updateChartStyle(key, value)`
- `setChartType(type)`
- `renderPriceSeries()`
- `renderVolume()`

Browser test:

- Opened `http://127.0.0.1:8000/`.
- No console errors.
- Changed chart type to `Area`, then back to `Candles`.
- `chart_type` control and chart canvas stayed healthy.

## Key Frontend Functions

Important functions in `frontend/index.html`:

- `init()`
- `setupCharts()`
- `loadChart(options = {})`
- `resetChartRefreshTimer()`
- `activeChartRefreshMs()`
- `onTimeframeChange()`
- `refreshQuote()`
- `applyLiveQuote(q)`
- `refreshWatchlist()`
- `renderIndicators()`
- `renderPriceSeries()`
- `renderVolume()`
- `setChartType(type)`
- `updateChartStyle(key, value)`

## Tests/Verification Already Done

Syntax:

```powershell
.\.venv\Scripts\python.exe -B -c "import ast, pathlib; [ast.parse(p.read_text(encoding='utf-8'), filename=str(p)) for p in pathlib.Path('app').rglob('*.py')]; print('syntax ok')"
```

Passed.

FastAPI TestClient:

- HTML includes `chart_type`, `resetChartRefreshTimer`, `applyLiveQuote`.
- `/api/search-symbols?q=ptt&limit=5` returns `PTT.BK` first.
- `/api/candles?symbol=BTC-USD&timeframe=1h&limit=60` returned `200`.

Live localhost:

- `/api/quote?symbol=BTC-USD` returned a price.
- `/api/candles?symbol=BTC-USD&timeframe=1h&limit=60` returned `200`.
- Served HTML contains chart customization and live update functions.

Browser runtime:

- Opened `http://127.0.0.1:8000/`.
- Canvas rendered.
- Console errors: none.
- Status showed live update interval.
- Switching chart type worked.
- Switching timeframe to `5m` recalculated summary/indicators.

## Known Caveats

- Yahoo Finance is not a true low-latency websocket feed. It is "real-time-ish" polling from Yahoo endpoints. Crypto moves more often; Thai/US stocks may only update during market hours and depending on Yahoo latency.
- Yahoo does not offer a stable official "download all global tickers" endpoint. Current implementation uses Yahoo Search dynamically while typing and caches results.
- Current frontend is still a large single HTML file. It works, but future maintainability would improve by splitting JS/CSS.
- Some Thai text in existing Python files appears mojibake in PowerShell output, but app behavior has been working. Be careful with encodings.
- Browser `localStorage` can retain chart style from tests. The last browser test restored chart type to `candlestick`.

## Latest Addition: Schools Selection Panel (2026-06-13 23:15)

Added a new "Schools" tab in the frontend UI with:

- **Schools control panel**: List of all 19 analysis schools with checkboxes to enable/disable each one
- **Weight adjustment**: Range slider for each school (0.1x to 2.0x) to override the default weight
- **Reset defaults**: Button to reset all schools to enabled and weight=1.0
- **LocalStorage persistence**: Saves user's school selections and weights to browser storage
- **API integration**: Updated `/api/analyze` POST to accept:
  - `enabled_schools`: array of school IDs to include (null = all)
  - `weights`: override weights dict {id: weight} (null = use defaults)

Files modified:
- `frontend/index.html`: Added Schools tab, control panel UI, JavaScript functions for school management
- Backend already supports these parameters (no changes needed)

Status: Frontend UI complete and verified to send parameters. Backend endpoint works with Python schools. LLM timeout issue present (Gemini API quota/latency) - does not block the core functionality.

## Fixes 2026-06-13 23:45 (5 user requests)

1. **Analyze ERROR (500) fixed**: Gemini free tier quota for `gemini-2.5-flash-lite` is 20 req/day and got exhausted → backend threw 429 → 500. Now `analyze_data` (analysis.py) and `analyze_image` (vision.py) wrap `run_llm` in try/except. On any LLM failure they return Python-school verdicts + placeholder Claude verdicts with a friendly Thai message via new `_friendly_llm_error()`. `/api/analyze` now returns 200 always (verified: 9 python + 10 claude-placeholder, up 62 / down 38).
2. **Search → click symbol does nothing**: TWO causes fixed. (a) `renderSearchResults()` rebuilt full innerHTML on every `onmouseenter` (via setSearchIndex) → the button got destroyed/recreated mid-click so the click was eaten. Rewrote to build DOM once with `addEventListener` (mousedown preventDefault + click→selectSearchResult); hover now only toggles `.active` via new `highlightSearchIndex()` (no rebuild). (b) stale chart stayed when a symbol 404'd. `selectSymbol` now clears chart + shows "กำลังโหลด..." immediately (new `clearAllSeries()`); `loadChart` 404/error path clears series + shows clear Thai message. NOTE: old inline `onmousedown="selectSearchResult"` fully removed; `escapeJs` now unused but kept.
3. **Reset indicators button**: added "↺ Reset defaults" in Indicators panel header. `resetIndicatorSettings()` resets chartStyle to `defaultChartStyle`, period inputs to `defaultIndicatorSettings`, toggles to `defaultIndicatorToggles`, then redraws + reloads.
4. **Fibonacci tool**: added "Fibonacci" button in toolstrip. Two clicks (high then low) → `drawFibonacci()` draws price lines at levels 0/0.236/0.382/0.5/0.618/0.786/1/1.272/1.618 (0.5 & 0.618 emphasized). Uses `candleSeries.createPriceLine`, tracked in `priceLines`, removed by Clear drawings.
5. **Heikin Ashi chart type**: added option in chart_type select. `heikinAshiCandles()` computes HA OHLC; `priceData()` returns HA when chartType==="heikinashi"; createPriceSeries already treats it as candlestick; `applyLiveQuote` redraws full HA series on each tick.

IMPORTANT for next session: backend changes need a server RESTART (uvicorn runs without --reload). Frontend changes need a browser HARD REFRESH (Ctrl+Shift+R) — the user's earlier screenshot showed no Schools tab due to cache.

## Fixes 2026-06-13 ~23:50 (ราคา=0 + Analyze 500)

- **Analyze 500 จริง ๆ**: เซิร์ฟเวอร์เดิม (PID 15280) ยังรันโค้ดเก่าก่อนแก้ resilient → ต้องรีสตาร์ท. หยุด 15280 (ยืนยันว่าเป็น uvicorn :8000 ก่อน) แล้วรันใหม่ผ่าน **Claude Preview** (serverId ใน launch.json `trade-assistant`). ตอนนี้ /api/analyze คืน 200 + ตาราง 19 แถว (9 python + ข้อความโควต้า). **บทเรียน: แก้ backend ต้องรีสตาร์ท uvicorn เสมอ (ไม่มี --reload).**
- **ราคาเป็น 0/ผิด (race condition)**: `refreshQuote` ใช้ `currentSymbol` ตอน template แต่ fetch ของ symbol เก่า resolve ทีหลัง → เอาราคาเก่า/0 มาแปะชื่อใหม่ (เห็นชัด: badge "GOOGL 1h 291.13" = ราคา AAPL). แก้: จำ `sym` ตอนเริ่ม, ถ้า `sym !== currentSymbol` ทิ้ง quote, และถ้า price ไม่ใช่เลขจริง/เป็น 0 แสดงแค่ชื่อ. ยืนยันในเบราว์เซอร์: สลับ GOOGL→badge 359.68 ถูกต้อง.
- **ป้าย volume "0" มุมขวาล่าง**: แท่งปัจจุบันที่ยังก่อตัว volume=0 ทำให้ histogram โชว์ label "0". แก้: `volumeSeries` ตั้ง `lastValueVisible:false, priceLineVisible:false`.
- การคลิกผลค้นหา + Analyze + ราคา ทดสอบผ่านใน Claude Preview แล้ว (ไม่มี console error).

## Groq Fallback Chain (2026-06-14 00:20)

เพิ่ม Groq เป็นตัวสำรองเมื่อ Gemini quota หมด (fallback ทำงานอัตโนมัติ):

**Files Modified:**
- `.env`: เพิ่ม `GROQ_API_KEY=<REDACTED — เก็บใน .env เท่านั้น ห้าม commit>`, `GROQ_MODEL=llama-3.3-70b-versatile`, เปลี่ยน `LLM_PROVIDER=auto`
- `config.py` `resolve_llm()`: เพิ่ม `exclude` parameter ให้ skip provider ที่ล้ม, ปรับ priority list → `("anthropic", "gemini", "groq", "openai")` เพื่อให้ groq เป็นตัวสำรอง
- `analysis.py` `run_llm()`: เพิ่ม `exclude_providers` parameter เพื่อให้ retry provider ต่อไป, `analyze_data()`: เพิ่ม loop retry — ลอง provider 1 ถ้า 429 quota ล้ม → ลอง provider ถัดไป

**ผลลัพธ์:**
- Analyze ล้มเลิก 429 หมดโควต้า → ระบบลองตัวถัดไป (Groq) อัตโนมัติ
- ตรวจสอบว่า: `resolve_llm()` load ทั้ง gemini_api_key + groq_api_key ✓, active provider = gemini (first in priority) ✓

**ข้อควรระวัง:**
- API key ที่ป้อนมาแสดงในแชท → user ต้อง regenerate ใหม่ใน Groq console เพื่อความปลอดภัย
- ถ้าอยากบังคับ Groq ตอนนี้: ตั้ง `LLM_PROVIDER=groq` ใน .env แล้วรีสตาร์ท backend

## CDC ActionZone V3 Indicator (2026-06-14)

Ported Pine Script indicator `CDC ActionZone V3 2020` (by piriya33) to JavaScript + TradingView Lightweight Charts.

**File Modified:**
- `frontend/index.html`

**How it works:**
- Toggle button "CDC Zone" added in toolstrip (after "Clear drawings")
- Button highlights blue (`.active`) when enabled
- Uses EMA12 (Fast) and EMA26 (Slow) computed from close prices via `calcEMA(values, period)`

**Zone logic (same as Pine Script):**
- Green = Bull (Fast>Slow) AND price > FastMA → Buy zone
- Blue = Bear AND price > FastMA AND price > SlowMA → Pre-Buy 2
- Aqua = Bear AND price > FastMA AND price < SlowMA → Pre-Buy 1
- Red = Bear AND price < FastMA → Sell zone
- Orange = Bull AND price < FastMA AND price < SlowMA → Pre-Sell 2
- Yellow = Bull AND price < FastMA AND price > SlowMA → Pre-Sell 1

**What gets drawn when CDC enabled:**
- EMA12 red line + EMA26 blue line on main chart (`cdcFastSeries`, `cdcSlowSeries`)
- Bar/candlestick colors overridden by zone color (calls `priceData()` then applies `CDC_COLORS` per bar)
- Buy ("B") and Sell ("S") signal circles on chart via `candleSeries.setMarkers()`

**When disabled:**
- Restores default candle colors by calling `candleSeries.setData(priceData())`
- Clears markers and CDC series data

**Key new functions:**
- `calcEMA(values, period)` — standard EMA with SMA seed
- `computeCDC(candles)` → `{ fast, slow, zones, markers }`
- `toggleCDC()` — toggles `cdcEnabled`, updates button active state, calls `renderCDC()`
- `renderCDC()` — sets EMA series data + overrides candle colors + sets markers

**Hooks into existing rendering:**
- `renderIndicators()` calls `renderCDC()` at end
- `setChartType()` calls `renderCDC()` if enabled (after chart type change)
- `clearAllSeries()` clears CDC series data + markers

**Verified in browser (preview_eval):**
- 449 candles, EMA12/26 computed correctly (e.g. EMA12≈293.02, EMA26≈293.96 for AAPL 1h)
- Last 5 zones = "red" (AAPL currently in Bear + price < EMA12 = Sell zone)
- 38 buy/sell signal markers found across 449 candles
- Button active state toggles correctly

## ท่าไม้ตาย — Signature Strategy + Backtest Engine (2026-06-14)

ระบบเทรดที่รวม "ทุกศาสตร์เชิงสูตร" เป็นคะแนนฉันทามติ แล้ว backtest ย้อนหลังยาว พร้อมแสดงสถิติ + จุดเข้า-ออกบนกราฟ.

**Files (ใหม่/แก้):**
- `backend/app/backtest.py` (ใหม่) — engine หลัก
- `backend/app/data/yahoo.py` — เพิ่ม `get_history()` + `_HISTORY_MAP` (ดึงข้อมูลย้อนหลังยาว)
- `backend/app/schemas.py` — เพิ่ม `BacktestRequest`
- `backend/app/main.py` — เพิ่ม route `POST /api/backtest`
- `frontend/index.html` — แท็บ "⚔️ ท่าไม้ตาย" + ฟังก์ชัน runBacktest/applyBacktestToChart/renderBacktestStats/reloadLive + state `backtestMode`

**กลไก backtest.py:**
- ทุกแท่งในอดีต เรียก `_votes_at(i)` ให้แต่ละศาสตร์โหวต (up/down + confidence) — ใช้เฉพาะข้อมูลถึงแท่ง i (ไม่มี lookahead)
- รวมเป็น `up_probability` (0-100) แบบถ่วงน้ำหนักเหมือน engine.aggregate (ใช้ _WEIGHTS จาก registry, override ด้วย weights ได้)
- 12 ศาสตร์ร่วม backtest (`BACKTEST_SCHOOLS`): trend_ema, rsi, macd, bollinger, stochastic, support_resistance, volume, dow_theory(proxy ความชัน ema_mid), divergence(proxy 40 แท่ง), candlestick(engulfing/hammer/shooting-star), price_action(breakout 20 แท่ง), market_psychology(RSI extreme + volume spike)
- **หมายเหตุสำคัญ:** ศาสตร์ AI/pattern (Elliott/Harmonic/Wyckoff/SMC/Gann/Fibonacci) รัน backtest ทีละพันแท่งไม่ได้ (ต้องเรียก LLM) → ใช้ proxy เชิงกฎสำหรับ candlestick/price_action/psychology และระบุใน `schools_used` เพื่อความโปร่งใส
- **Entry:** up_prob >= entry_threshold (long) หรือ <= 100-threshold (short) + directional_weight พอ + Trend Filter (long เหนือ SMA200 / short ใต้)
- **Exit:** ATR stop (entry ∓ ATR×atr_mult), target (∓ ATR×atr_mult×rr), signal flip, หรือ max_hold_bars. เข้าที่ราคาปิดแท่งสัญญาณ, ตรวจ stop ก่อน target เมื่อชนกันในแท่งเดียว (conservative)
- **สถิติ:** win_rate, profit_factor, total_return (ทบต้น), max_drawdown, expectancy_r, avg_win/loss, buy&hold เทียบ, best/worst, avg_hold
- **by_strength:** แยก win rate ตามช่วงคะแนนฉันทามติตอนเข้า (65-70/70-75/75-80/80-85/85-100) → ตอบโจทย์ "setup แบบไหนชนะสูงสุด"
- คืน `chart.candles` + `chart.indicators` (ชุดเดียวกับ /api/candles) + `markers` (พร้อมใช้กับ lightweight-charts) เพื่อให้ frontend วาดกราฟยาว + ปักจุดตรงกัน

**ข้อมูลย้อนหลัง (yahoo):** 1d=20y(~5000 แท่ง — `range=max` ถูก yahoo ลดเป็นรายเดือนจึงใช้ 20y), 1h/4h=730d, 15m/5m=60d. cap 8000 แท่ง.

**Frontend behavior:**
- กดแท็บ ⚔️ ท่าไม้ตาย → ตั้งค่า (timeframe/direction/entry%/RR/ATR/max hold/trend filter) → "▶ รัน Backtest"
- ผลลัพธ์: กราฟหลักโหลดประวัติยาว + markers (L=เขียวใต้แท่ง, S=แดงเหนือแท่ง, วงกลมเขียว/แดง=ออกกำไร/ขาดทุน), การ์ดสถิติ, ตาราง by_strength, ตารางเทรดล่าสุด 25 ไม้
- `backtestMode=true` หยุด auto-refresh ไม่ให้ทับกราฟ. กด **Reload** (reloadLive) หรือเลือก symbol/เปลี่ยน timeframe → กลับ Live
- ส่ง enabled_schools + weights + indicator_params จาก UI เดิมไปด้วย (เลือกเปิด/ปิดศาสตร์ + น้ำหนักได้)

**ทดสอบจริงในเบราว์เซอร์ (preview_eval):**
- AAPL 1d: 4832 แท่ง, 399 เทรด, WR 42.1%, PF 1.44, expectancy +0.23R, return +1205% (buy&hold +8673%), 798 markers, 12 ศาสตร์, 8.3s
- AAPL 1h: 4881 แท่ง, 375 เทรด, WR 37.1%, PF 1.13, +25.7% — render กราฟ+markers+ตาราง ครบ, Reload กลับ Live ได้
- Screenshot ยืนยัน: ลูกศร L/S + วงกลมกำไร/ขาดทุน + % บนกราฟยาว ✓

**ปรับจูนเพื่อ win rate สูงขึ้น (ผู้ใช้อยากได้โอกาสชนะสูง):**
- ลด RR (เช่น 1.0) → win rate สูงขึ้นแต่กำไร/ไม้เล็กลง
- เพิ่ม entry_threshold (เช่น 75-80) → เทรดน้อยลงแต่คัดเฉพาะ setup แรง
- ดูตาราง by_strength เพื่อหาว่า "ช่วงคะแนนไหน + ทิศไหน" ชนะสูงสุดในหุ้นนั้น แล้วเทรดเฉพาะช่วงนั้น
- Trend Filter เปิดไว้ช่วยเพิ่ม win rate (เทรดตามเทรนด์ใหญ่)

## ท่าสำเร็จรูป 60-70% Win Rate + Parameter Sweep (2026-06-14)

ผู้ใช้ขอ "ท่าที่ชนะปกติ 60-70%". ทำ parameter sweep จริง (RR×ATR×THR×DIR บน 5 หุ้น daily 20y) แล้วเจอว่า:
**RR ต่ำ (0.5) + ATR stop กว้าง (2.0-2.5) + Long-only + Trend Filter = win rate 60-67% และ expectancy เป็นบวกทุกตัว.**
(target ใกล้ = โดน take-profit บ่อย; stop กว้าง = ไม่ค่อยโดน stop; long ตามเทรนด์ใหญ่)

**ผลรายหุ้น (Preset A: RR0.5/ATR2.5/เข้า60/Long/trend-on, daily 20y):**
- AAPL 67.5% | NVDA 64.2% | GOOGL 64.4% | AMZN 61.0% | MSFT 60.4% | KO 55.7% | PTT.BK 54.1%(ขาดทุน)
- **สำคัญ:** ท่านี้ได้ 60-67% เฉพาะของที่เป็น "ขาขึ้น" (growth/tech). หุ้น sideways (KO) หรือขาลง (PTT.BK) จะตก ~54-56% เพราะ Long+Trend Filter ต้องการของขาขึ้น — สื่อสารกับผู้ใช้อย่างซื่อสัตย์แล้ว

**Presets เพิ่มในแท็บ ⚔️ ท่าไม้ตาย (ปุ่มกดทีเดียวรันได้):** `BT_PRESETS` + `applyPreset(name)` ใน index.html
- `winrate` "🎯 ชนะบ่อย 60-67%": entry60/rr0.5/atr2.5/long/trend (เน้นจำนวนไม้ชนะ ทบต้น)
- `winmax` "🎯 ชนะบ่อยสุด ~66%": entry70/rr0.5/atr2.0/long/trend (คัดฉันทามติแรง เทรดน้อยลง)
- `balanced` "⚖️ สมดุล": entry65/rr1.8/atr1.5/both/trend (WR ~40-45% แต่ไม้ชนะใหญ่)
- ทดสอบในเบราว์เซอร์: กด winrate → AAPL 1d ได้ WR 67.5% PF1.56 +1693% (by_strength: ช่วง65-70%→ชนะ69%) ✓

**บทเรียน sweep:** มี trade-off ชัด — RR ยิ่งต่ำ winrate ยิ่งสูงแต่ expectancy/ไม้ยิ่งบาง. RR0.5 คือจุดที่ยังบวกและ winrate แตะ 60-67%. ถ้าผู้ใช้อยากได้ %ชนะสูงกว่านี้ (เช่น 70%+) ต้องเพิ่ม entry_threshold + ลด RR อีก แต่ total return จะลด (ดู sweep: rr0.5/atr1.5/thr70 → 67% แต่ ret เฉลี่ยแค่ 46%).

## ท่าสไนเปอร์ (ยิงน้อย+กำไร/ไม้สูง) + min_directional_weight (2026-06-14)

ผู้ใช้ถามต่อ: "เข้าออเดอร์ไม่ถี่ แต่จังหวะโอกาสสูง ผลตอบแทนเยอะก็ได้". ทำ sweep แนวสไนเปอร์ (เกณฑ์เข้าสูง + บังคับหลายศาสตร์เห็นพ้อง + RR สูง).

**เพิ่มพารามิเตอร์ `min_directional_weight`** (น้ำหนักรวมขั้นต่ำของศาสตร์ที่เห็นพ้องตอนเข้า — สูง=ต้องหลายศาสตร์เห็นตรงกัน=เทรดน้อยลงแต่คัดกว่า):
- `backend/app/backtest.py` — มี param นี้อยู่แล้ว (default 2.0)
- `backend/app/schemas.py` BacktestRequest — เพิ่ม field `min_directional_weight` (0.5-12)
- `backend/app/main.py` — ส่งต่อเข้า run_backtest
- `frontend/index.html` — เพิ่มช่อง `bt_mdw` + ส่งใน runBacktest + presets ตั้งค่า

**ผล sweep แนวสไนเปอร์ (long, daily 20y, เฉลี่ย 7 หุ้น):**
- เกณฑ์85 + MDW5 + RR3 + ATR2: WR 38%, exp **0.43R** (สูงสุด), 72 ไม้/20ปี (~4/ปี), ret +356%, PF 1.91
- เกณฑ์85 + MDW5 + RR1.2 + ATR2.5: WR **50%**, exp 0.20R, 85 ไม้, ret +167% (ลูกผสม)
- เกณฑ์85 + MDW5 + RR1.0 + ATR2: WR **55%**, exp 0.14R, 100 ไม้, ret +90%

**Presets ใหม่ใน BT_PRESETS (รวมเป็น 5 ปุ่ม):**
- `sniper` "🎯 สไนเปอร์ (กำไร/ไม้สูงสุด)": entry85/mdw5/rr3/atr2/long/hold120 — ทดสอบ AAPL: 75 ไม้, WR 41.3%, +594%, exp **0.66R** ✓
- `snipebal` "🎯 สไนเปอร์สมดุล ~50%": entry85/mdw5/rr1.2/atr2.5/long/hold120 — ทดสอบ AAPL: 86 ไม้, WR **54.7%**, +214%, exp 0.28R ✓

**บทเรียนหลัก (สามเหลี่ยมแห่งการเทรด — เลือกได้ 2 จาก 3):**
1. **ชนะบ่อย (winrate สูง)** — ต้อง RR ต่ำ → กำไร/ไม้เล็ก
2. **กำไร/ไม้ใหญ่ (RR สูง)** — winrate จะต่ำลง (~38-41%)
3. **เทรดถี่** — vs เทรดน้อยคัดเฉพาะ A+ (MDW/threshold สูง)
→ สไนเปอร์ = เลือก "กำไร/ไม้ใหญ่ + เทรดน้อย" จึง winrate ต่ำแต่ expectancy สูงสุด (edge ต่อจังหวะคุ้มสุด). "โอกาสสูง" ในที่นี้ = expectancy/edge สูง ไม่ใช่ %ชนะดิบ. สื่อสารตรงนี้กับผู้ใช้แล้วอย่างซื่อสัตย์.

## แก้กราฟทอง XAUUSD + ศาสตร์ MMC (Coach James) + System Test (2026-06-14)

### บั๊กกราฟทอง XAUUSD=X (แก้แล้ว)
**สาเหตุ:** `XAUUSD=X` ไม่มีอยู่บน Yahoo (404). `GC=F`(ทองล่วงหน้า) และ `PAXG-USD`(ทอง spot) ใช้ได้.
**แก้:** เพิ่ม `_SYMBOL_ALIASES` + `_normalize_symbol()` ใน `backend/app/data/yahoo.py` — map XAUUSD=X/XAUUSD/XAU=X/GOLD → GC=F, XAGUSD=X → SI=F. ใช้ใน get_candles, get_history, _get_quote_from_chart, get_quotes (bulk: normalize เพื่อเรียก API แต่คืน Quote ด้วยสัญลักษณ์เดิมที่ผู้ใช้ขอ ให้ frontend จับคู่ live quote ถูก).
**ทดสอบ:** XAUUSD=X candles/quote/watchlist/backtest(5031 แท่ง) ใช้ได้, ราคาทอง 4238.8, กราฟแสดงครบในเบราว์เซอร์ ✓

### ศาสตร์ใหม่: Market Maker Concept (Liquidity Sweep) — แนวทาง Coach James SB
ศึกษาช่อง @CoachjameSB: เทรดสั้น เน้นทองคำ 10 ปี, ใช้ **MMC** (เจ้ามือกวาด stop รายย่อยที่ swing high/low แล้วสวนกลับ = liquidity sweep) + **EMA80** เป็นแนวรับ-ต้าน.
**เพิ่มเป็นศาสตร์จริงในระบบ (school ที่ 20):**
- `_index.json`: เพิ่ม `mmc_liquidity` (evaluator=python, weight 1.2, category structure)
- `knowledge/mmc_liquidity.json` (ใหม่): ความรู้ MMC/liquidity sweep/EMA80
- `schools.py` `_eval_mmc`: ตรวจ sweep บนแท่งล่าสุด (live /api/analyze) — ไส้หลุดใต้ swing low 20 แท่งแต่ปิดกลับเหนือ→buy 70; ทะลุเหนือ swing high แต่ปิดกลับใต้→sell 70
- `backtest.py`: เพิ่ม `mmc_liquidity` ใน BACKTEST_SCHOOLS (13 ศาสตร์) + detection ใน `_votes_at` (vote 13)
- ตอนนี้ live python schools = 10 ตัว, backtest = 13 ตัว, ทั้งหมดในระบบ = 20 ศาสตร์

**Preset ใหม่ "🥇 Coach James MMC (ทอง)":** `coach` ใน BT_PRESETS — entry70/rr2.5/atr2.0/long/mdw2.5/hold80, tf=1h, **emaSlow=80** (Trend Filter เป็น EMA80), weights {mmc_liquidity:2.0, support_resistance:1.5}.
- ขยาย preset system: applyPreset ตั้ง bt_timeframe + ema_slow ได้, เพิ่ม `activePresetWeights` (merge เข้า weights ตอน runBacktest)
- ผลทดสอบ XAUUSD=X 1h (8000 แท่ง ~2ปี): 255 เทรด, WR 33.3%, +24.8%, exp 0.17R, PF 1.26 (ทองอินทราเดย์ผันผวน winrate ต่ำ RR สูงชดเชย)
- หมายเหตุ: ผู้ใช้ต้องเลือกสัญลักษณ์ทอง (XAUUSD=X/GC=F) ก่อนกดรัน coach preset

### Preset ทั้งหมดในแท็บ ⚔️ ท่าไม้ตาย (6 ปุ่ม)
winrate(60-67%) / winmax(~66%) / sniper(กำไร/ไม้สูง exp0.66R) / snipebal(~50%) / **coach(MMC ทอง)** / balanced

### System Test ครบ (ไม่มีบั๊ก — ผู้ใช้สั่ง "ห้ามมีบัค")
Backend endpoints (urllib ตรงไป :8000): health✓ schools(20,มี mmc)✓ gold_candles✓ gold_quote(คืน XAUUSD=X)✓ watchlist(ทอง+เงิน)✓ search✓ analyze(up77,20 verdicts,มี mmc)✓ backtest×3 presets(coach_gold 255เทรด / winrate_aapl 502เทรด WR67% / sniper_nvda 65เทรด exp0.45R)✓ — **0 FAIL**
Frontend (preview_eval): เลือกทอง→กราฟ 500 แท่งราคา 4238.8✓ coach preset รันบนทอง✓ console errors=0✓ interaction ทั้งหมด(heikinashi/line/candlestick/CDC on-off/fib draw/clear/cursor)=ok ทุกตัว✓ reloadLive กลับ Live✓ screenshot กราฟทองแสดง EMA+BB+Volume ครบ✓
JSON+Python syntax valid ✓

## Suggested Next Work

**For Auto-Trading (ถ้าผู้ใช้อยากเริ่ม):**
1. **Bitkub Provider** — สร้าง `BitkubProvider` ใน `data/` เพื่อดึงราคา + placeholder สำหรับ place-order API
2. **Paper Trade Simulator** — โมดูล "จด" ไม้เทรดโดยไม่ส่งจริง (ทดสอบ 2-4 สัปดาห์ + log P&L)
3. **Confirm Mode** — ระบบเตรียมคำสั่ง แล้วรอ user กดยืนยัน (ปลอดภัยที่สุด)
4. **Live Mode** เพิ่ม guardrails: stop-loss, max-loss/day, order-size limits, kill-switch button

**Ongoing:**
1. Monitor Gemini API quota — fallback to Groq ทำงานแล้ว (verify เมื่อ Gemini quota reset)
2. Add a visible "Live on/off" toggle and refresh interval selector like TradingView.
3. Add chart crosshair/time/price settings and grid visibility toggles.
4. Add more drawing tools: rectangle zone, ray, horizontal ray, Fibonacci retracement.
5. Add indicator presets/templates saved to localStorage.
6. Add websocket/SSE path for quotes if later using a paid data provider.
7. Improve deploy flow: GitHub branch, clean commit, Render/Railway config.
8. Consider replacing single-file frontend with Vite/React only after current feature direction stabilizes.

## Live Refresh Controls (2026-06-14)

User asked to continue Claude's interrupted work and emphasized that all progress must be written to memory.md. Implemented the visible chart auto-refresh controls in `frontend/index.html`.

What changed:
- Added `Live On` / `Live Off` / `Live Paused` button in the chart toolstrip.
- Added refresh interval selector: `Auto`, `2s`, `5s`, `10s`, `15s`, `30s`, `1m`.
- Added `autoRefreshEnabled` and `refreshIntervalValue` state persisted in `localStorage`.
- `Auto` still uses the app's existing smart cadence: crypto/forex/futures and 1m/5m refresh at 5s, 15m at 10s, others at 15s.
- Manual interval overrides the smart cadence.
- Turning Live off clears the chart timer and shows `Live: off`.
- Turning Live on restarts the timer and performs a silent refresh.
- Backtest mode now clears the live timer, sets the UI to `Live Paused`, and prevents the live timer from overwriting backtest charts.
- Manual Reload still returns from backtest to live chart mode, then respects the user's Live on/off and interval settings.

Verification:
- Python syntax check passed for all `backend/app/**/*.py`.
- Confirmed `frontend/index.html` contains `autoRefreshBtn`, `refreshInterval`, `toggleAutoRefresh`, and refresh helper functions.
- In-app browser at `http://127.0.0.1:8000/` shows the new controls.
- Browser test: clicked `Live On` -> state became `Live Off`, interval set to `5000`, status became `Live: off`.
- Restored browser state to `Live On`, interval `5000`, status `Live: every 5s`.

## Chart Grid / Crosshair Controls (2026-06-14)

Continued the TradingView-like customization work in `frontend/index.html`.

What changed:
- Added `Show grid` checkbox in the Indicators -> Chart style panel.
- Added `Crosshair mode` selector with `Normal`, `Magnet`, and `Hidden`.
- Added `Vertical crosshair` and `Horizontal crosshair` toggles.
- Extended `chartStyle` localStorage state with:
  - `gridVisible`
  - `crosshairMode`
  - `crosshairVertVisible`
  - `crosshairHorzVisible`
- `chartBaseOptions()` now applies grid visibility and crosshair settings to the main chart, RSI chart, and MACD chart.
- `hydrateStyleControls()` now supports checkbox style controls, not only color/select inputs.
- Reset defaults now resets these new chart settings together with the existing candle/background/indicator colors.

Verification:
- Server HTML at `http://127.0.0.1:8000/` contains the new controls.
- In-app browser: opened `Indicators` tab and confirmed `Show grid`, `Crosshair mode`, and `Vertical crosshair` are visible.
- Browser test: set `Crosshair mode = hidden` and `Show grid = false`; DOM state updated correctly and console errors = 0.
- Restored browser state to `Crosshair mode = normal`, `Show grid = true`, `Live On`, interval `5000`.

## Backtest -> Go Live Position Workflow (2026-06-14)

User described the target workflow:
1. Select an asset to trade.
2. Select a strategy.
3. Run backtest.
4. If satisfied, press a run/live button.
5. The chart should show the strategy backtest and the current live entry signal.
6. Live signal status should show `waiting` vs `entry`, including entry price, TP, SL.
7. When entry exists, show a TradingView-like position box on the chart with reward/risk proportions so the user can switch to a trade app and place the order manually.
8. If trading on a different timeframe, changing timeframe should automatically update the live strategy and signal for that timeframe.

What already existed before this pass:
- `/api/backtest` and `/api/live-signal` backend endpoints.
- Strategy panel with Backtest and `Go Live`.
- Live status card with waiting/entry, entry/TP/SL, RR, sizing, and copy-trade-plan button.
- Chart price lines for TP / Entry / SL.

What changed in `frontend/index.html`:
- Added `#positionOverlay` inside `.chart-wrap`.
- Added CSS for a TradingView-like position overlay:
  - green reward zone
  - red risk zone
  - entry line
  - floating labels for Entry / Reward / Risk
- Added `activeLiveTrade` state.
- Added helper functions:
  - `clearLivePositionOverlay()`
  - `clearLivePosition()`
  - `updateLivePositionOverlay()`
- `drawLivePosition(trade)` now:
  - clears old live position
  - creates TP/Entry/SL price lines
  - stores active trade
  - draws the chart overlay using `candleSeries.priceToCoordinate()`
- `renderPriceSeries()` and chart resize now call `updateLivePositionOverlay()` so the overlay stays aligned when the chart redraws/resizes.
- Waiting/error live states now clear both price lines and overlay, preventing stale trade boxes.
- `selectSymbol()` now clears old live position immediately when changing symbol while live mode is on, then refreshes live signal for the new symbol.
- `onTimeframeChange()` now syncs `liveStrategyParams.timeframe` and `#bt_timeframe` to the selected chart timeframe, clears the old position, and refreshes the live signal.
- `goLive()` clears any old live position before starting.
- `applyBacktestToChart()` stops live mode first if a new backtest is run, preventing live signal overlays from overwriting backtest view.
- Added `1m` as a selectable strategy/backtest timeframe. Note: Yahoo may not provide enough 1m bars for backtest; live signal can still use it if enough current candles are available.

Verification:
- Python syntax check passed.
- Browser DOM at `http://127.0.0.1:8000/` contains `positionOverlay`, `Live` controls, and `1m` in `#bt_timeframe`.
- Quick 1m backtest failed with expected validation: not enough historical bars (~220 required). This is a data limitation, not a UI break.
- Quick 5m backtest on AAPL with relaxed test params passed: 4481 bars, 1249 trades, Go Live button appeared.
- Pressed Go Live from the backtest result:
  - chart switched to `AAPL / 5m`
  - live signal returned `entry LONG`
  - live card showed entry, TP, SL, RR, sizing
  - `#positionOverlay` became visible and showed LONG TP / SL / Entry / Reward / Risk
  - console errors = 0
- Changed chart timeframe from `5m` to `15m` while live mode was active:
  - `#bt_timeframe` synced to `15m`
  - old overlay cleared immediately
  - live signal recomputed as `AAPL / 15m`
  - live card returned `entry SHORT`
  - overlay redrew with SHORT TP / SL / Entry / Reward / Risk
  - console errors = 0
- Stopped test live mode afterward so the UI does not stay on the relaxed test strategy.

## Live A+ Entry Gate / Waiting Plan (2026-06-14)

User noticed Entry/TP/SL showed too often and clarified the desired behavior:
- Only show Entry/TP/SL when there is a high-probability entry moment.
- Show an arrow marker at the entry candle when an entry exists.
- If there is no entry yet, show what to watch next: which side is forming, what probability/weight is missing, and what price/EMA condition matters.
- Live trade RR should normally be greater than risk, e.g. 1.5:1, 2:1 or better.

What changed:
- `backend/app/backtest.py` live-signal logic now has an A+ gate for Live only:
  - `live_entry_threshold = max(entry_threshold, 65)`
  - `rr_ok = rr_ratio >= 1.5`
  - Live entry requires ATR, RR gate, school weight gate, trend filter gate, and A+ probability gate.
- Backtest logic was restored to use the original `entry_threshold`; A+ gate does not alter historical backtest behavior.
- Live response now includes:
  - `live_entry_threshold`
  - `rr_ok`
  - `watch_plan`
- Waiting state now includes watch-plan items such as:
  - Long/Short consensus needed vs current consensus
  - EMA trigger area when trend filter is enabled
  - school agreement gap
  - RR warning if below 1.5
- `frontend/index.html` waiting UI now shows:
  - side to watch
  - current strongest probability vs A+ gate
  - school weight status
  - RR Gate pass/fail
  - watch plan list
- `frontend/index.html` entry UI now calls `drawLivePosition(t, d)` so the live signal metadata can be used.
- `drawLivePosition(trade, signal)` now adds an `ENTRY` arrow marker on the signal candle only when status is `entry`.
- `clearLivePosition()` clears live markers when live mode has no entry, so old arrows/position boxes do not remain.

Verification:
- Python syntax passed.
- FastAPI TestClient live-signal test:
  - AAPL 15m with relaxed `entry_threshold=51`, `rr_ratio=0.5`, `min_directional_weight=0.5` returned `status=waiting`, `trade=False`, `rr_ok=False`, and watch plan.
  - Same setup with `rr_ratio=1.8` returned `status=entry` only because probability passed A+ gate (`down_probability=67`, gate=65) and RR passed.
- Browser reload at `http://127.0.0.1:8000/` confirmed:
  - page loads
  - `RR Gate` UI exists
  - `ENTRY` marker code exists
  - console errors = 0

## In-Chart Live Status Overlay (2026-06-14)

User wanted a visible status box inside the chart because when there is no entry signal yet, it is hard to know whether the system is active and what it is waiting for.

What changed in `frontend/index.html`:
- Added `#chartLiveStatus` inside `.chart-wrap`, layered above the chart but with `pointer-events:none` so it does not block drawing/crosshair interactions.
- Added `.chart-live-status` CSS with responsive sizing for desktop, tablet, and phone.
- Added `latestLiveSignal` state.
- Added `renderChartLiveStatus(state = latestLiveSignal)` to show:
  - `System Active` when the chart is live but strategy monitor is off.
  - `Strategy Monitor Active` while Go Live is fetching/reading a signal.
  - `Waiting A+ Setup` when there is no high-probability entry yet.
  - `LONG Entry` or `SHORT Entry` with probability, Entry, TP, and SL when live signal returns `entry`.
  - `Monitor Error` when `/api/live-signal` fails.
  - `Backtest Mode` when viewing historical backtest results.
- Wired the overlay into:
  - app init
  - chart refresh timer changes
  - symbol changes
  - timeframe changes
  - Reload back to Live
  - Go Live / Stop Live
  - live-signal success/error
  - backtest chart mode
- Added try/catch around `refreshAlerts()` and `refreshTriggered()` so transient alert endpoint issues do not create noisy console errors or distract while trading.

Verification:
- JS parser check passed by extracting the inline scripts from `frontend/index.html` and compiling them with bundled Node.js.
- Browser check at `http://127.0.0.1:8000/` confirmed:
  - `#chartLiveStatus` exists.
  - It is visible (`display:block`) with class `chart-live-status active`.
  - Text shown on chart: `System Active`, `Chart live 5s`, `AAPL / 1h`, `Strategy monitor Off`.
  - Alerts panel shows `None` after the try/catch update.

Bot-trade roadmap note:
- User asked if a trade bot can be built. Yes, but recommended sequence is:
  1. Finish signal/backtest/position-plan accuracy first.
  2. Add paper trading mode with order logs and simulated fills.
  3. Add broker/exchange connector layer later, e.g. Bitkub/Binance, after checking current official API docs.
  4. Before real-money mode, add API-key encryption, max-risk limits, daily loss limit, kill switch, order confirmation mode, audit logs, retry/idempotency, and fail-safe behavior if data/API is stale.

## Auto Trade Bot - Bitkub Paper Mode (2026-06-14)

User asked to continue and build an auto trade / bot trade system for crypto on Bitkub, all the way until usable, and to ask for extra info only when needed.

Implemented backend:
- Added `backend/app/bitkub.py`
  - Public Bitkub client.
  - `symbols()`, `ticker()`, `quote()`, and `candles()` using Bitkub public endpoints.
  - TradingView history endpoint is used for OHLCV candles.
  - Secure signing helpers and guarded methods for:
    - `place_bid()`
    - `place_ask()`
    - `open_orders()`
  - Secure order calls exist but are not reachable unless the bot layer allows real mode.
- Added `backend/app/autotrade.py`
  - `AutoTradeConfig` pydantic model.
  - `AutoTradeManager` in-memory bot state.
  - Bot loop:
    - fetches Bitkub candles
    - calls existing `live_signal()` so bot, Go Live, and backtest share the same strategy logic
    - opens only long spot positions because Bitkub spot cannot open true shorts
    - ignores short entries for spot
    - creates paper orders/positions
    - updates paper positions and closes them on TP/SL
    - tracks orders, positions, events, latest signal, errors, realized P/L
  - Risk guards:
    - paper first
    - real mode disabled by default
    - real mode requires `BITKUB_REAL_TRADING_ENABLED=true`
    - real mode also requires `real_confirm_text="I UNDERSTAND REAL ORDERS"`
    - max open positions
    - daily loss limit
    - no duplicate entry on the same signal candle
    - order notional is capped by `quote_budget_thb`
- Updated `backend/app/config.py`
  - `BITKUB_API_KEY`
  - `BITKUB_API_SECRET`
  - `BITKUB_REAL_TRADING_ENABLED`
- Updated `backend/.env.example` with Bitkub settings and warning comments.
- Updated `backend/app/main.py`
  - created `bitkub_client`
  - created `auto_trade`
  - added endpoints:
    - `GET /api/bitkub/symbols`
    - `GET /api/bitkub/ticker?symbol=BTC_THB`
    - `GET /api/autotrade/status`
    - `POST /api/autotrade/start`
    - `POST /api/autotrade/stop`
    - `POST /api/autotrade/tick`
  - shutdown now stops the auto-trade task cleanly.

Implemented frontend:
- Added `Auto Trade` tab in `frontend/index.html`.
- Added `#autoPanel` with controls:
  - mode: paper/real
  - Bitkub pair
  - timeframe
  - budget THB
  - risk per trade
  - max daily loss
  - poll interval
  - A+ entry threshold
  - RR
  - ATR stop multiplier
  - school weight gate
  - real confirm text
  - trend filter
- Added JS functions:
  - `autoTradeConfigFromForm()`
  - `startAutoTrade()`
  - `stopAutoTrade()`
  - `tickAutoTrade()`
  - `refreshAutoTrade()`
  - `renderAutoTrade()`
- App init now calls `refreshAutoTrade()` and polls status every 5 seconds.

Verification:
- Frontend JS parser check passed with bundled Node.
- Backend Python compile passed for:
  - `app/bitkub.py`
  - `app/autotrade.py`
  - `app/main.py`
  - `app/config.py`
- Server restarted successfully on `http://127.0.0.1:8000`.
- `GET /api/autotrade/status` works.
- Started paper bot with:
  - `symbol=BTC_THB`
  - `timeframe=1h`
  - `budget=10000 THB`
  - `risk=1%`
  - `RR=1.8`
  - `entry_threshold=65`
- Bot fetched Bitkub BTC_THB candles and got a live `entry long` signal:
  - probability `66% / 65%`
  - entry `2117167.39`
  - TP `2135126.0968`
  - SL `2107190.3307`
- Paper order opened:
  - buy BTC_THB
  - qty `0.00472329`
  - notional `10000 THB`
  - status `paper_filled`
- Bot was stopped after verification so no background loop remains active from the test.
- Browser UI check:
  - `Auto Trade` tab exists and opens.
  - UI shows bot stopped, paper mode, BTC_THB, latest signal, position, order, and event log.

Remaining before real-money trading:
- User must provide Bitkub API key and secret via local `.env` only. Do not paste keys into chat.
- User must explicitly set `BITKUB_REAL_TRADING_ENABLED=true`.
- User must type exact confirmation text in UI/API: `I UNDERSTAND REAL ORDERS`.
- Recommended next improvement before real mode:
  - persist bot state/orders to disk or SQLite
  - add fee/slippage model
  - add manual close button
  - add Bitkub balance read endpoint
  - add market health checks and stale-data guard
  - review secure-order symbol format against the current Bitkub account/API behavior with a tiny test order only after user confirms.

## Bitkub Top Tier Pair Presets (2026-06-14)

User wanted the auto trade bot to support Ethereum, Solana, and other top-tier crypto pairs besides Bitcoin.

Checked `GET /api/bitkub/symbols` from the live backend and confirmed active/non-frozen THB pairs:
- `BTC_THB`
- `ETH_THB`
- `SOL_THB`
- `XRP_THB`
- `BNB_THB`
- `ADA_THB`
- `DOGE_THB`
- `LINK_THB`
- `DOT_THB`
- `AVAX_THB`
- `ATOM_THB`
- `ARB_THB`
- `OP_THB`
- `NEAR_THB`
- `UNI_THB`
- `AAVE_THB`
- `USDT_THB`

Not added as recommended top buttons because Bitkub returned stopped/frozen:
- `MATIC_THB`
- `LTC_THB`

Frontend changes in `frontend/index.html`:
- Added `bitkubTopPairs` array.
- Added `datalist#botPairList` to the Auto Trade pair input.
- Added quick buttons container `#botTopPairs`.
- Added `hydrateBotPairs()` to render pair buttons and datalist options.
- Added `setBotSymbol(symbol)` to change the bot pair from a button click.
- `init()` now calls `hydrateBotPairs()`.

Verification:
- Frontend JS parser check passed.
- Browser check at `http://127.0.0.1:8000/`:
  - Auto Trade tab shows 17 Bitkub pair buttons.
  - First buttons: BTC, ETH, SOL, XRP, BNB, ADA, DOGE, LINK.
  - Datalist includes `ETH_THB`, `SOL_THB`, `BNB_THB`, `AVAX_THB`.

## FIX: on-chart Strategy Monitor box ไม่อัปเดต + goLive race (2026-06-14, Claude รับช่วงต่อจาก Codex)

**บั๊กที่ Codex แก้ค้างไว้ตอนโดน usage limit (รับงานต่อ):**
- `renderChartLiveStatus(state = latestLiveSignal)` วาดกล่อง on-chart (#chartLiveStatus "Strategy Monitor") โดยอ่าน global `latestLiveSignal`. แต่ `refreshLiveSignal()/renderLiveSignal()` (ที่ได้ข้อมูลสัญญาณจริง) **ไม่เคยเซ็ต `latestLiveSignal` และไม่เรียก `renderChartLiveStatus()`** → กล่องบนกราฟค้างที่ placeholder "กำลังอ่านสัญญาณ" ตลอด (สาขา `!state`) แม้ panel (#liveSignalBox) อัปเดตถูก.
- **race ใน goLive():** ยิง `loadChart()` + `refreshLiveSignal()` พร้อมกัน 2 request หนัก (uvicorn worker เดียว, compute_indicators เป็น sync CPU งานบล็อก) → บางครั้ง live-signal มาช้า/overlay วาดด้วย `priceToCoordinate` ก่อนกราฟโหลดเสร็จ → กล่อง/overlay ไม่ขึ้น.

**แก้ใน `frontend/index.html`:**
- `renderLiveSignal(d)`: เพิ่ม `latestLiveSignal = d` (ต้นฟังก์ชัน) + `renderChartLiveStatus(d)` (ท้ายฟังก์ชัน) → กล่อง on-chart sync กับสัญญาณจริงทุกครั้ง.
- `refreshLiveSignal()` error/catch: เซ็ต `latestLiveSignal = {status:"error",message}` + เรียก `renderChartLiveStatus()`.
- `stopLive()`: เซ็ต `latestLiveSignal=null` + `renderChartLiveStatus()` (กลับสถานะ System Active).
- `goLive()`: ทำเป็น `async` แล้ว **`await loadChart({fit:true})` ก่อน `await refreshLiveSignal()`** — ตัด race, ให้ priceToCoordinate ใช้ได้ตอนวาด position overlay. (ปุ่ม onclick="goLive()" เรียกแบบ fire-and-forget ได้ปกติ)

**ทดสอบจริง (preview_eval + screenshot, server :8000 รีสตาร์ทใหม่):**
- Backend live-signal: waiting/entry/RR-gate ผ่านครบ (urllib ตรง). สแกน 12 symbol×2 tf เจอ entry จริง เช่น BTC-USD 1d→short (SL 67911>entry 64475>TP 57602, sizing risk$45=1.5%/reward$90=×2) — ตรงกับ screenshot ที่ user เคยเห็น.
- goLive บน BTC-USD 1d → **กล่อง on-chart "SHORT Entry 73% + Entry/TP/SL"** (border แดง entry-short) + **position overlay แบบ TradingView (SHORT TP zone เขียว / Risk 3436)** + 3 price lines + panel "⚡ เข้าออเดอร์ SHORT" ครบ. screenshot ยืนยัน.
- สลับไป AAPL (waiting) ขณะ live → ทั้ง 2 กล่องเปลี่ยนเป็น "Waiting A+ Setup" + watch plan (จับตา Short 59%/65%, RR Gate OK), ล้าง position เก่า BTC หมด (overlay hidden, 0 lines).
- console errors = 0. Python syntax ok.

**สถานะ: workflow Backtest→Go Live→สัญญาณเข้า(กล่องสถานะ+กรอบ position บนกราฟ)→สลับแอป Trade เข้าออเดอร์ตามที่แสดง = ใช้งานได้จริงครบ.**

## Auto Trade Testing Defaults (2026-06-14)

User wants the experimental/paper testing bot to accept:
- Risk per trade: `5%`
- Minimum/target Risk:Reward: `1.5`

Changes made:
- `frontend/index.html`
  - Auto Trade `Risk / trade %` input default changed to `5`.
  - Auto Trade `Risk:Reward` input default changed to `1.5`.
  - `autoTradeConfigFromForm()` fallback values changed to `risk_pct: 5` and `rr_ratio: 1.5`.
- `backend/app/autotrade.py`
  - `AutoTradeConfig.risk_pct` default changed to `5.0`.
  - `AutoTradeConfig.rr_ratio` default changed to `1.5`.

Safety notes:
- This is for testing/paper mode defaults.
- Real Bitkub trading is still gated by `BITKUB_REAL_TRADING_ENABLED=true` and exact confirmation text.
- Position notional is still capped by `quote_budget_thb`; even with 5% risk, the bot should not open a paper position above the configured THB budget.

Verification:
- Frontend script parse passed with bundled Node.
- Backend `app/autotrade.py` compile passed with backend virtualenv Python.
- `rg` confirmed the updated values in both frontend and backend.

## Auto Bot On-Chart Realtime Status (2026-06-14)

User said Auto Trade only showed Backtest status on the chart after pressing Start Bot, and wanted a realtime "AI/bot is trading now" status box directly on the chart.

Frontend changes in `frontend/index.html`:
- Added `latestAutoTradeState` global state.
- Added cyan `autobot` style for `#chartLiveStatus`, with a small pulsing dot.
- Added `renderAutoBotChartStatus(bot)` to render Auto Trade state directly in the on-chart status box.
- `renderChartLiveStatus()` now prioritizes a running Auto Trade bot over Backtest mode, so the chart shows `Auto Bot Running` while the bot is active even if the user had just viewed a backtest.
- `startAutoTrade()` now immediately creates a temporary running state and calls `renderChartLiveStatus()` before the API returns, so the chart does not look silent while waiting for the first bot tick.
- `renderAutoTrade(s)` now stores the latest bot status, refreshes the on-chart box, and updates the chart toolbar text to `Auto Bot: SYMBOL TF · status`.
- `tickAutoTrade()` adds a temporary "Running manual tick" event to the on-chart box while the API request is in flight.
- `refreshAutoTrade()` keeps the chart box updated every frontend poll and shows a status-unavailable message if the bot status endpoint fails.

What the chart box shows while bot is running:
- Mode: `PAPER` or `REAL`
- Pair and timeframe, e.g. `BTC_THB / 1h`
- AI state: starting, waiting for A+ setup, or entry found
- Current probability versus entry threshold
- Open position count
- Risk and RR, e.g. `5% · 1:1.5`
- Latest order status
- Watch plan, latest event, or Entry/TP/SL if available

Verification:
- Frontend script parse passed with bundled Node: `scripts ok 2`.
- Browser verification at `http://127.0.0.1:8000/`:
  - Reloaded the app, opened Auto Trade tab, pressed Start Bot in paper mode.
  - On-chart box immediately changed to `Auto Bot Running`.
  - Initial state showed `PAPER`, `BTC_THB / 1h`, `รอ tick แรก`, and `Risk/RR 5% · 1:1.5`.
  - After polling, the box updated with live bot status: `Signal entry`, probability `84% / 65%`, `1 open` position, and latest order `buy paper_filled`.
  - Stopped the test bot via `/api/autotrade/stop`; API confirmed `running:false`.

## Realtime + Smoother Chart Stream (2026-06-14)

User wanted a chart that is more realtime and smoother than the existing Yahoo/REST polling behavior.

API research:
- Crypto can be streamed immediately through public WebSocket market streams.
- Binance official Spot WebSocket supports `<symbol>@trade` with realtime trade updates, which is suitable for `BTC-USD`, `ETH-USD`, `SOL-USD`, and other `*-USD` crypto chart symbols by mapping them to `*USDT`.
- Bitkub official WebSocket supports `market.ticker.<symbol>` and orderbook streams, but Bitkub's `market.trade.<symbol>` stream has a published shutdown/migration notice dated 2026-05-18. Use ticker/orderbook/private websocket carefully if adding Bitkub-native chart stream later.
- Twelve Data has WebSocket support, but the 2026 trial/basic docs describe limited credits/symbol access and full WebSocket requiring higher tiers. Good candidate for paid US stocks/forex/commodities.
- Finnhub supports realtime quote/trade APIs and websocket, but current app only has Finnhub REST provider; full realtime stock streaming should be added via a backend proxy when the user provides `FINNHUB_API_KEY`.
- Polygon/Massive supports tick-level websocket stocks, but real realtime stock access is paid.

Frontend changes in `frontend/index.html`:
- Added realtime stream globals: `realtimeSocket`, `realtimeStreamKey`, `realtimeReconnectTimer`, `realtimeAnimationFrame`.
- Added `binanceStreamSymbol(symbol)` mapping for common crypto symbols:
  - `BTC-USD`, `ETH-USD`, `SOL-USD`, `BNB-USD`, `XRP-USD`, `DOGE-USD`, `ADA-USD`, `AVAX-USD`, `LINK-USD`, `DOT-USD`, `LTC-USD`, `UNI-USD`, `AAVE-USD`, `NEAR-USD`, `ATOM-USD`, `OP-USD`, `ARB-USD`.
- Added `startRealtimeStream()`:
  - For crypto `*-USD`, opens Binance public websocket: `wss://stream.binance.com:9443/ws/<symbol>@trade`.
  - For other symbols, opens backend quote websocket: `/ws/quotes?symbol=...&interval=1`.
  - Auto-reconnects on close/error and falls back to quote websocket when Binance stream errors.
- Added `applyRealtimePrice()` and `animateRealtimeCandle()`:
  - Updates only the current candle intrabar instead of reloading the whole chart.
  - Builds/rolls a new candle when the timeframe bucket changes.
  - Uses requestAnimationFrame easing so price transitions look smoother.
  - Updates summary Price and active badge from realtime ticks.
- `refreshQuote()` now runs every `1000ms` but skips REST polling while a websocket stream is open.
- `loadChart()` starts realtime streaming after a successful chart load.
- `selectSymbol()` and `onTimeframeChange()` close the old realtime stream before loading the new symbol/timeframe.
- `applyBacktestToChart()` closes realtime stream so live ticks do not overwrite backtest charts.

Backend changes in `backend/app/main.py`:
- `/ws/quotes` now accepts `interval` query param and clamps it between `0.5` and `30` seconds.
- Default backend quote websocket interval changed from 3s to 1s.

Verification:
- Frontend script parse passed with bundled Node: `scripts ok 2`.
- Backend `app/main.py` compile passed with backend virtualenv Python.
- Browser verification at `http://127.0.0.1:8000/`:
  - Reloaded app, clicked `BTC-USD Crypto`.
  - Chart status updated to `Binance tick: <time>`.
  - Active badge and summary price updated from Binance tick stream, e.g. `BTC-USD 1h 64333.04 0.05%`.
  - No full chart reload was needed for the tick update.

Important follow-up:
- Restart backend to apply the new `/ws/quotes?interval=1` backend behavior for non-crypto symbols.
- For US stocks/gold/forex true realtime, add a paid/trial provider key and backend WebSocket proxy (recommended next: Finnhub if user wants US stock focus, Twelve Data if user wants one provider for stocks/forex/crypto/commodities).

## Chart Timezone Display Fix (2026-06-14)

User reported chart time did not match real local time. Screenshot showed Windows clock `7:45 PM` Asia/Bangkok while chart x-axis showed around `12:45`, exactly 7 hours behind.

Root cause:
- Candle timestamps are correct Unix epoch seconds.
- Lightweight Charts was formatting axis labels in UTC/default behavior, so Thailand time appeared as UTC.

Frontend changes in `frontend/index.html`:
- Added `CHART_TIME_ZONE = "Asia/Bangkok"`.
- Added Intl formatters for chart time/date labels.
- Added:
  - `chartTimeToDate(time)`
  - `formatChartTick(time)`
  - `formatChartTooltipTime(time)`
  - `applyChartTimeFormat()`
- `chartBaseOptions()` now uses `localization.timeFormatter` and `timeScale.tickMarkFormatter`.
- Time formatting is reapplied after setup, timeframe changes, and chart reloads.

Important:
- Do not shift candle timestamps themselves. Keep data in Unix epoch; only format display labels as Bangkok time.

Verification:
- Frontend script parse passed with bundled Node: `scripts ok 2`.

## Deployment / Database Recommendation (2026-06-14)

User wants the app online, durable, and able to keep all commands/configs/results over time.

Recommendation:
- Use GitHub for source control.
- Use GitHub Pages only for static frontend hosting if needed.
- Do not rely on GitHub Pages for the full app because GitHub Pages only serves static HTML/CSS/JS and cannot run FastAPI, background bot loops, secrets, or a database.
- Recommended database: Supabase Postgres.
  - Reasons: managed Postgres, dashboard, backups, auth, realtime, storage, Row Level Security.
  - Good fit for storing user commands, strategy configs, backtest results, bot events, paper/real orders, watchlists, alerts, and app memory.
- Recommended backend host:
  - Render or Railway for FastAPI + WebSocket backend.
  - Backend needs environment variables for API keys and database URL.
- For future real auto trade:
  - Backend should be deployed as a private service with secrets in host env vars.
  - Bot execution should run server-side only, never in GitHub Pages frontend.

Suggested durable tables:
- `users`
- `commands`
- `watchlists`
- `strategies`
- `backtests`
- `bot_runs`
- `bot_events`
- `orders`
- `positions`
- `alerts`
- `app_memory`

Next implementation steps:
1. Add SQLAlchemy/SQLModel + async Postgres driver.
2. Add `DATABASE_URL` env var.
3. Create migration/schema for the tables above.
4. Persist AutoTradeManager events/orders/positions instead of in-memory only.
5. Persist user commands and settings to `app_memory`.
6. Add deploy config for Render/Railway.
7. Configure frontend `API_BASE_URL` so GitHub Pages can call the hosted backend.

## Supabase Persistence Scaffolding (2026-06-14)

User provided Supabase project URL:
- `https://xiblqetehrnprycbkwyp.supabase.co`

Implemented backend scaffolding so the app can persist durable data once `DATABASE_URL` is set.

Files added:
- `backend/app/db.py`
  - Optional `DatabaseStore`.
  - No database connection is attempted unless `DATABASE_URL` is configured.
  - Uses `asyncpg` lazily inside `connect()` so the app can still compile/run without the dependency installed until DB is enabled.
  - Methods:
    - `connect()`
    - `close()`
    - `save_bot_run()`
    - `save_bot_event()`
    - `save_order()`
    - `save_position()`
    - `upsert_memory()`
    - `list_memory()`
- `backend/db/schema.sql`
  - Supabase/Postgres schema for:
    - `bot_runs`
    - `bot_events`
    - `orders`
    - `positions`
    - `strategies`
    - `backtests`
    - `watchlists`
    - `alerts`
    - `app_memory`
- `backend/db/SUPABASE_SETUP.md`
  - Step-by-step setup guide.
  - User should run `schema.sql` in Supabase SQL Editor, then put the real connection string in `backend/.env`.

Files updated:
- `backend/app/config.py`
  - Added `supabase_url`, defaulting to `https://xiblqetehrnprycbkwyp.supabase.co`.
  - Added `database_url` from `DATABASE_URL`.
- `backend/app/main.py`
  - Instantiates `store = DatabaseStore(settings.database_url)`.
  - Passes `store` into `AutoTradeManager`.
  - Startup connects to DB if `DATABASE_URL` is set; failure logs but does not crash app.
  - Shutdown closes store.
  - `/api/health` now returns `database_enabled` and `supabase_url`.
  - Added `GET /api/db/status`.
  - Added `GET /api/memory`.
  - Added `POST /api/memory`.
- `backend/app/autotrade.py`
  - Added optional `store`.
  - Added `run_id` for bot runs.
  - Persists bot run start/stop, events, orders, and positions in the background when DB is enabled.
  - If DB is not enabled, behavior remains in-memory as before.
- `backend/requirements.txt`
  - Added `asyncpg>=0.29`.
- `backend/.env.example`
  - Added `SUPABASE_URL`.
  - Added `DATABASE_URL` placeholder and examples.

Security notes:
- The Supabase project URL is public and safe to keep in config.
- Do not paste or commit Supabase DB password, service role key, Bitkub key, or any real secret.
- Put real values only in `backend/.env` or cloud host environment variables.

Verification:
- Backend compile passed:
  - `python -m py_compile app/db.py app/autotrade.py app/main.py app/config.py`
- Frontend script parse passed:
  - `scripts ok 2`

Next required manual setup:
1. Open Supabase dashboard for the provided project.
2. Run `backend/db/schema.sql` in SQL Editor.
3. Copy the Postgres connection string from Supabase.
4. Put it in `backend/.env` as `DATABASE_URL=...`.
5. Install updated backend requirements.
6. Restart backend.
7. Check `GET /api/db/status` should show `enabled: true`.

## Deployment Completion Layer (2026-06-14)

User asked to "do everything completely" after providing Supabase publishable/anon keys.

Important distinction:
- Supabase project URL and publishable/anon key are not enough to create tables or connect backend persistence directly.
- Backend durable persistence still requires `DATABASE_URL`, which contains the DB password and must be entered in `backend/.env` or Render environment variables, not pasted into chat.
- Chrome extension was unavailable, so Codex could not operate the already-logged-in Supabase dashboard tab directly.

Completed in repo:
- `frontend/index.html`
  - Added `<script src="./config.js"></script>`.
  - `const API` now reads `window.API_BASE_URL` and trims trailing slash.
  - WebSocket URL builder now derives from `API_BASE_URL` when frontend is hosted separately on GitHub Pages.
- `frontend/config.js`
  - Local default: `window.API_BASE_URL = ""`.
- `frontend/config.example.js`
  - Example for GitHub Pages: `window.API_BASE_URL = "https://YOUR-RENDER-SERVICE.onrender.com";`.
- `render.yaml`
  - Render Blueprint config for FastAPI backend.
  - Uses `backend` as rootDir.
  - Starts with `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
  - Declares env vars including `DATABASE_URL`, AI keys, Bitkub keys, and Supabase URL.
- `.github/workflows/pages.yml`
  - GitHub Actions workflow to deploy `frontend/` to GitHub Pages.
  - Reads repository variable `API_BASE_URL` and writes `frontend/config.js` during deploy.
- `DEPLOYMENT.md`
  - End-to-end deployment steps:
    - Supabase schema
    - Render backend
    - GitHub Pages frontend
    - GitHub repo variable `API_BASE_URL`
- `backend/scripts/setup_db.py`
  - Runs `backend/db/schema.sql` against `DATABASE_URL`.
  - Useful after the user adds the Supabase connection string locally.
- `backend/db/SUPABASE_SETUP.md`
  - Updated with the local setup script.
- `backend/app/config.py`
  - Added `supabase_anon_key` from `SUPABASE_ANON_KEY`.
- `backend/.env.example`
  - Added `SUPABASE_ANON_KEY`.
- `backend/.env` (gitignored local only)
  - Added `SUPABASE_URL`.
  - Added `SUPABASE_ANON_KEY` using the public publishable key the user provided.
  - Left `DATABASE_URL=` empty because DB password is required and must be entered locally/cloud-side.

Verification:
- Backend compile passed:
  - `python -m py_compile app/db.py app/autotrade.py app/main.py app/config.py scripts/setup_db.py`
- Frontend script parse passed:
  - `scripts ok 3`

Remaining manual secret step:
- User must copy Supabase Postgres connection string from Project Settings -> Database and paste into `backend/.env` / Render env var as `DATABASE_URL`.
- After that, run:
  - `cd backend`
  - `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`
  - `.\.venv\Scripts\python.exe scripts\setup_db.py`
  - restart backend

## Supabase Connection Attempt (2026-06-14)

User pasted a direct Supabase Postgres URL containing the database password. Treat it as exposed and recommend rotating/resetting the DB password after setup.

Actions completed:
- Added the URL to local `backend/.env`.
- Installed `asyncpg`.
- Ran backend compile successfully.
- Ran `scripts/setup_db.py`.

Results:
- Direct host `db.xiblqetehrnprycbkwyp.supabase.co:5432` failed locally with `getaddrinfo failed`.
- DNS check showed the direct host resolves only to an IPv6 `AAAA` record on this machine:
  - `2406:da18:e5c:b700:d667:f1ae:ed93:2dd5`
- This likely means local network/Python cannot use the Supabase direct IPv6 path.
- Tried common Supabase transaction pooler regions using username `postgres.xiblqetehrnprycbkwyp`; each returned `tenant/user ... not found`.
- Reverted local `DATABASE_URL` to the direct Supabase host the user supplied, rather than leaving an incorrect guessed pooler host.

Current blocker:
- Need the exact Transaction pooler connection string copied from Supabase Dashboard -> Connect -> Direct -> Transaction pooler / URI.
- User can provide the pooler URI with `[YOUR-PASSWORD]` placeholder only; Codex can reuse the local password already placed in `backend/.env`, but because the password was exposed in chat it should be rotated soon.

Alternative:
- User can run `backend/db/schema.sql` manually in Supabase SQL Editor. This avoids local DB connectivity issues and immediately creates tables.

## Deployment Prep + Push (2026-06-15, Claude)

ผู้ใช้ขอ deploy แอป (พูดว่า "ขึ้น supabase" แต่จริงๆ Supabase = DB เท่านั้น; สถาปัตยกรรม = Supabase(Postgres) + Render(FastAPI) + GitHub Pages(frontend)).

**ทำให้แล้ว (อัตโนมัติในเครื่อง):**
- ตรวจ schema.sql ↔ db.py = ตรงกัน (9 ตาราง, pgcrypto) ✓
- แก้ `.github/workflows/pages.yml` ให้ trigger บน branch จริง `claude/trading-app-design-1pmpxo` (เดิมตั้งแค่ `main` ที่ไม่มี → Pages จะไม่ build)
- **Security: พบ Groq API key เต็มใน memory.md บรรทัด ~317 → redact ทิ้งแล้ว** (key นี้ไม่เคยอยู่ใน git history เพราะ memory.md เพิ่งถูก commit ครั้งแรก)
- ตั้ง git identity (local repo): chayanun250841@gmail.com
- commit `5cba538` (47 ไฟล์, deploy config + ฟีเจอร์ทั้งหมด) + **push ขึ้น origin สำเร็จ** (github.com/chayanun250841/-.git, default branch)
- `.env` gitignored ปลอดภัย, นับ secret scan = clean

**ทำไม่ได้ (ต้องใช้ login ของผู้ใช้ — กฎห้าม):** รัน schema.sql ใน Supabase SQL Editor / สร้าง Render Blueprint + ใส่ secret / เปิด GitHub Pages + ตั้ง var API_BASE_URL.

**ค้าง/ข้อควรระวัง:**
- โฟลเดอร์ซ้ำ `trading-app/` (nested, separate .git, commit 252da36 เดียวกับ MVP เก่า) — auto-mode บล็อกการลบ; ไม่ได้ commit ขึ้น git; ผู้ใช้ลบเองได้
- repo name = "-" → Pages URL จะเป็น https://chayanun250841.github.io/-/ (แปลกแต่น่าจะใช้ได้)
- gh CLI ไม่ได้ติดตั้ง → มอนิเตอร์ workflow ผ่าน CLI ไม่ได้

## Deploy Progress — หยุดกลางทาง รอทำต่อ (2026-06-15)

**เป้าหมาย:** เอาแอปขึ้นเว็บสาธารณะ = Supabase(DB) + Render(FastAPI) + GitHub Pages(frontend)

### ทำไปแล้ว
- ✅ **ขั้น ① Supabase เสร็จ** — รัน schema.sql ใน SQL Editor สร้าง 9 ตารางแล้ว (project: xiblqetehrnprycbkwyp.supabase.co). ผู้ใช้ได้ connection string แบบ **Session pooler** (port 5432) เก็บไว้แล้ว (ใช้ Session pooler เพราะ Render วิ่ง IPv4, Direct connection ของ Supabase เป็น IPv6 ต่อไม่ติด)
- ✅ commit `5cba538` (47 ไฟล์ deploy config + ฟีเจอร์) push ขึ้น repo เก่า `chayanun250841/-` แล้ว
- ✅ git remote ในเครื่องตั้งใหม่แล้ว: `origin` → `https://github.com/Chayanun-Tech/trading-app.git`, เก็บของเก่าเป็น `old-chayanun250841` → `chayanun250841/-`
- ✅ git identity (local): chayanun250841@gmail.com
- ✅ Render GitHub app ติดตั้งบนบัญชี **Chayanun-Tech** แล้ว

### ⛔ ติดอยู่ตรงนี้ (จุดที่ต้องแก้ก่อนไปต่อ)
**push ไป repo ใหม่ไม่ผ่าน** — `git push -u origin HEAD:main` ขึ้น **"Repository not found"** (`https://github.com/Chayanun-Tech/trading-app.git`)
สาเหตุที่เป็นไปได้ (ยังไม่ยืนยัน — รอ user ส่ง URL จริงของ repo มา):
1. ชื่อ/owner ของ repo ไม่ตรงกับที่เดา (อาจไม่ใช่ `Chayanun-Tech/trading-app`)
2. repo เป็น Private + git ในเครื่องล็อกอินค้างเป็นบัญชี `chayanun250841` (จาก push ครั้งก่อน) → มองไม่เห็น repo ของ Chayanun-Tech → 404
**คำสั่ง push ที่ถูกต้องคือ:** `git push -u origin HEAD:main` (ต้องมี `HEAD:` เพราะ local branch ชื่อ `claude/trading-app-design-1pmpxo` ไม่ใช่ main)

### ขั้นตอนทำต่อเมื่อเปิดเครื่อง (ลำดับ)
1. **เคลียร์ปม push:** ขอ URL จริงของ repo จาก address bar → set-url origin ให้ตรง → ถ้าเป็น credential เก่า (chayanun250841) ค้าง ให้ล้างใน Windows Credential Manager (Control Panel → Credential Manager → Windows Credentials → ลบ git:https://github.com) แล้ว push ใหม่ ล็อกอินเป็น Chayanun-Tech. repo ต้องเป็น **Public** (สำหรับ GitHub Pages ฟรี)
2. `git push -u origin HEAD:main`
3. **② Render:** dashboard.render.com → New → Blueprint → เลือก repo → ใส่ secret: `DATABASE_URL`(Session pooler string), `GEMINI_API_KEY`, `GROQ_API_KEY` (**regenerate Groq key ใหม่ก่อน** เพราะตัวเก่าเคยโผล่ในแชท) → Deploy → เช็ก `/api/health` + `/api/db/status`
4. **③ GitHub Pages:** repo → Settings → Pages → Source = GitHub Actions; แล้ว Settings → Secrets and variables → Actions → Variables → เพิ่ม `API_BASE_URL` = URL ของ Render. (workflow pages.yml trigger บน main + claude branch อยู่แล้ว)
5. ได้เว็บจริง: frontend `https://chayanun-tech.github.io/trading-app/`, backend `https://<ชื่อ>.onrender.com`

### หมายเหตุ
- โฟลเดอร์ซ้ำ `trading-app/` (nested, ของเก่า) ยังอยู่ — auto-mode บล็อกการลบ; ไม่กระทบ git (ไม่ได้ commit); user ลบเองได้
- ไฟล์ที่แก้ตอน deploy prep (pages.yml, memory.md redacted) commit แล้วใน 5cba538
- ⚠️ ความปลอดภัย: Groq API key ตัวเดิมหลุดในแชท → **ต้อง regenerate** ที่ Groq console ก่อนใช้ production

## เจอชื่อ repo ที่ถูกต้อง + ปลดปม push (2026-06-15, Claude/Opus)

**ปม "Repository not found" ที่ค้างครั้งก่อน = แก้ต้นเหตุได้แล้ว:**
- repo ปลายทางจริงชื่อ **`Chayanun-Tech/-trading-app`** (มีขีด `-` นำหน้า!) ไม่ใช่ `Chayanun-Tech/trading-app` ที่เดาไว้ → เลยขึ้น 404 มาตลอด
- ยืนยันด้วย `gh repo list Chayanun-Tech`: เจอ `Chayanun-Tech/-trading-app` (public, สร้าง 2026-06-15)
- gh CLI ติดตั้งแล้ว (เครื่องนี้มี `C:\Program Files\GitHub CLI\gh.exe`), login = บัญชี `chayanun250841` (เป็นสมาชิก org Chayanun-Tech, scope: repo/workflow/gist/read:org)
- **แก้ remote แล้ว:** `git remote set-url origin https://github.com/Chayanun-Tech/-trading-app.git` → `git ls-remote origin` ผ่าน (repo ว่าง พร้อมรับ push)

**สถานะ branch ปัจจุบัน:** local `claude/trading-app-design-1pmpxo`, commit `5cba538` (HEAD), working tree มี `M memory.md` + untracked `trading-app/` (โฟลเดอร์ซ้ำของเก่า)

### ⛔ ติด: push ขึ้น main โดน HARD BLOCK ของ auto-mode
- `git push -u origin HEAD:main` ถูก auto-mode classifier บล็อกแบบ hard block (มองว่าเป็นการ push โค้ดทั้ง repo ขึ้น public external repo = data exfiltration ที่แม้ user อนุมัติก็ปลดไม่ได้ในโหมดนี้)
- **ทางแก้: ผู้ใช้ต้องรัน push เองในเทอร์มินัลปกติ (นอก Claude):**
  ```powershell
  git push -u origin HEAD:main
  ```
  (รันในรากโปรเจกต์ trading-app; ถ้าถาม login/404 ให้ใช้บัญชีที่เข้าถึง Chayanun-Tech ได้)

### ขั้นตอนทำต่อหลัง push สำเร็จ (เหมือนเดิม)
1. **Render:** dashboard.render.com → New → Blueprint → เลือก repo `-trading-app` → secret: `DATABASE_URL`(Session pooler), `GEMINI_API_KEY`, `GROQ_API_KEY`(regenerate ก่อน) → Deploy → เช็ก `/api/health` + `/api/db/status`
2. **GitHub Pages:** Settings → Pages → Source=GitHub Actions; Settings → Secrets/variables → Actions → Variables → `API_BASE_URL` = URL Render
3. ได้เว็บจริง: frontend `https://chayanun-tech.github.io/-trading-app/`, backend `https://<ชื่อ>.onrender.com`
   - หมายเหตุ: repo มีขีดนำหน้า → Pages URL จะมี `/-trading-app/`

## ✅ PUSH ขึ้น main สำเร็จ (2026-06-15, Claude/Opus)

**ปมสิทธิ์/บัญชีจบแล้ว — push ผ่าน:**
- ต้นเหตุ 403: repo `Chayanun-Tech/-trading-app` สร้างด้วยบัญชี **`chayanunju@scphpl.ac.th`** (เจ้าของ org) แต่เครื่อง login git ค้างเป็น **`chayanun250841`** (ผู้ใช้ใช้บัญชีนี้กับโปรเจกต์อื่นด้วย จึงไม่อยากสลับ credential)
- **วิธีแก้ที่ใช้ (ดีสุด ไม่ต้องแตะ Credential Manager):** เจ้าของ (chayanunju) เพิ่ม `chayanun250841` เป็น **Collaborator (Write)** ที่ repo Settings → Collaborators → Add people → chayanun250841 ยอมรับ invitation → push ด้วย credential เดิม
- **push สำเร็จ:** `git push -u origin HEAD:main` → 81 objects, `[new branch] HEAD -> main`, local `claude/trading-app-design-1pmpxo` track `origin/main` แล้ว
- repo URL: https://github.com/Chayanun-Tech/-trading-app (public, branch main มีโค้ดครบ commit 5cba538)

**บทเรียนสำหรับรอบหน้า:**
- เครื่องนี้ git/gh login ค้างเป็น `chayanun250841` (ใช้กับโปรเจกต์อื่น — อย่าไปลบ/สลับ credential)
- repo นี้เป็นของ org Chayanun-Tech (เจ้าของ = chayanunju@scphpl.ac.th); chayanun250841 มีสิทธิ์ Write ในฐานะ collaborator แล้ว

### ⏭️ เหลือทำต่อ (ผู้ใช้ทำผ่าน browser)
1. **Render:** dashboard.render.com → New → Blueprint → เลือก repo `Chayanun-Tech/-trading-app` → secret: `DATABASE_URL`(Session pooler), `GEMINI_API_KEY`, `GROQ_API_KEY`(regenerate ก่อน!) → Deploy → เช็ก `/api/health` + `/api/db/status`
2. **GitHub Pages:** repo Settings → Pages → Source=GitHub Actions; Settings → Secrets and variables → Actions → Variables → `API_BASE_URL` = URL ของ Render
3. ผลลัพธ์: frontend `https://chayanun-tech.github.io/-trading-app/`, backend `https://<ชื่อ>.onrender.com`
