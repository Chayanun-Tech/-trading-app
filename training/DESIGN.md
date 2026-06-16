# Model Factory — Design Doc (โรงงานเทรนโมเดลของบอท)

> เป้าหมายของเอกสารนี้: วาง **logic ที่แท้จริง** ของระบบเทรนโมเดล ให้ละเอียดพอที่จะ
> (ก) เอาไปเป็น Prompt สั่ง AI เขียนโค้ด หรือ (ข) ลงมือสร้างเองทีละขั้นได้ทันที
>
> สถานะ: DESIGN (ยังไม่ลงโค้ด) · Anchor timeframe: **1h** (ปรับได้) · ตลาดหลัก: Bitkub spot (long-only)

---

## 0. หลักการสูงสุด (อ่านก่อนทุกอย่าง)

### 0.1 ไม่ไล่ Winrate — ไล่ Expectancy
`Expectancy = (Winrate × avg_win) − (Lossrate × avg_loss)`

Winrate 80% ที่ R:R แย่ = เจ๊ง. Winrate 55% ที่ R:R ดี = รวย.
**Winrate สูงเป็น "ผลพลอยได้" จากการเลือกไม้ดี ไม่ใช่เป้าหมายที่ตั้งตรงๆ**

### 0.2 Training–Serving Parity (กัน skew)
โค้ดสร้างฟีเจอร์ต้องเป็น **ชุดเดียว** ที่ใช้ทั้งตอนเทรนและตอนเทรดจริง
→ `training/` **import** `features_at()` จาก `backend/app/math_model.py` เท่านั้น ห้าม copy โค้ดฟีเจอร์

### 0.3 ไฟล์โมเดล = สัญญา (contract)
แอปเทรดรู้แค่ "รับ candles → คืน prob" ไม่ต้องรู้วิธีเทรน → เปลี่ยนโมเดลได้โดยไม่แตะโค้ดแอป

### 0.4 ห้าม deploy โมเดลที่ไม่ผ่าน Validation Gate
ตัวเลขทุกตัวต้องวัดบน **out-of-sample หักค่าธรรมเนียมแล้ว** เท่านั้น

---

## 1. สถาปัตยกรรมรวม (7 ขั้น)

```
① INGEST → ② FEATURE → ③ LABEL → ④ TRAIN → ⑤ VALIDATE → ⑥ REGISTER → ⑦ DEPLOY
  ดึงข้อมูล   ฟีเจอร์(ร่วม)  triple-barrier  meta-model   walk-forward    versioning    เข้าแอป
```

โฟลเดอร์เป้าหมาย:
```
trading-app/
├── backend/app/math_model.py     # โค้ดฟีเจอร์ = แหล่งความจริงเดียว (ใช้ร่วม)
├── training/
│   ├── DESIGN.md                 # เอกสารนี้
│   ├── ingest.py                 # ① ดึงข้อมูล (CCXT / yfinance / Alpha Vantage)
│   ├── labels.py                 # ③ triple-barrier labeling
│   ├── dataset.py                # ②+③ ประกอบ features+labels เป็น training set
│   ├── train_pipeline.py         # ④→⑥ เทรน + validate + register
│   ├── backtest_costs.py         # โมเดลค่าธรรมเนียม+slippage
│   └── config.yaml               # พารามิเตอร์ทั้งหมด
├── data/candles/                 # ข้อมูลดิบ (gitignore, local)
└── models/
    ├── registry/                 # ทุกเวอร์ชัน + metrics (ไม่ทับทิ้ง)
    └── math_edge_model.json      # production (ตัวที่ผ่าน gate ดีที่สุด)
```

---

## 2. ① INGEST — ชั้นข้อมูล

### 2.1 แหล่งข้อมูล
| สินทรัพย์ | แหล่ง | ไลบรารี | หมายเหตุ |
|---|---|---|---|
| คริปโต (หลัก) | Binance / Bybit | **CCXT** | OHLCV 1m–1d ย้อนหลังหลายปี ต้องทำ pagination (1000 แท่ง/ครั้ง) |
| คริปโต (เร็ว) | Binance Vision | (มีสคริปต์เดิม) | ZIP รายเดือน โหลดก้อนใหญ่ได้เร็ว |
| Bitkub THB | TradingView history | (มีสคริปต์เดิม) | คู่ที่เทรดจริง — ใช้ fine-tune/test |
| ทอง/หุ้น | Yahoo | **yfinance** | `GC=F` (ทอง), `.BK` (หุ้นไทย) — 1m ได้แค่ 60 วัน, 1d ได้สิบปี |
| ปัจจัยพื้นฐาน | Alpha Vantage | REST | `OVERVIEW` → P/E, D/E, ROE (เฉพาะหุ้น, optional) |

### 2.2 CSV schema (มาตรฐานเดียว — เหมือนของเดิม)
```
symbol,timeframe,time,open,high,low,close,volume,source
```
- `time` = Unix seconds (UTC). CCXT/Binance คืน ms/µs → ต้อง normalize เป็น seconds
- เก็บที่ `data/candles/<source>/<symbol>/<timeframe>.csv`
- เขียนแบบ merge-by-timestamp (rerun ได้ ไม่ซ้ำ) — ของเดิมทำไว้แล้วใน `download_history.py`

### 2.3 ข้อกำหนด
- จัดการ **Rate limit**: sleep ระหว่าง request, retry แบบ backoff
- **Data quality**: เติม/ตรวจ gap ของเวลา, ทิ้งแท่งที่ volume=0 ทั้งชุดผิดปกติ, ตรวจ OHLC sane (high≥low ฯลฯ)
- **ครบทุก regime**: ต้องมีข้อมูลทั้งช่วง bull / bear / sideways (เช่น crypto 2019–ปัจจุบัน) ไม่งั้นโมเดล bias

---

## 3. ② FEATURE — ชั้นฟีเจอร์ (ใช้ร่วมกับแอป)

### 3.1 ฟีเจอร์ปัจจุบัน (16 ตัว — `FEATURE_NAMES` ใน `math_model.py`)
returns (1/3/12/24), volatility (12/24), range%, body%, close_pos,
ระยะจาก ema20/ema50/sma200, rsi_scaled, macd_scaled, macd_hist_scaled, volume_z20

### 3.2 ฟีเจอร์ที่ควรเพิ่ม (เพื่อยกระดับ)
| กลุ่ม | ฟีเจอร์ | เหตุผล |
|---|---|---|
| Regime | ทิศ SMA200 (slope), ADX | บอกว่าตลาดเทรนด์/sideway → โมเดลปรับพฤติกรรม |
| Volatility | ATR/price, Bollinger width | คุม sizing + บริบท |
| Time | ชั่วโมงในวัน, วันในสัปดาห์ (sin/cos) | crypto มี pattern ตามเวลา |
| Multi-TF | ทิศของ TF ที่ใหญ่กว่า (เช่น 4h ตอนเทรด 1h) | confluence |
| Fundamental (หุ้น) | P/E, D/E, ROE (Alpha Vantage) | กรองความแข็งแกร่ง (optional) |

> **กฎเหล็ก:** ฟีเจอร์ทุกตัวคำนวณจากข้อมูล **ถึงแท่ง i เท่านั้น** ห้ามแตะแท่ง i+1 ขึ้นไป (กัน lookahead)

---

## 4. ③ LABEL — Triple-Barrier (หัวใจข้อ 1)

เปลี่ยนจาก label เดิม *"6 แท่งข้างหน้าราคาขึ้นไหม"* (ไม่ตรงการเทรดจริง) เป็น:

```
จากจุดเข้าที่แท่ง i (ราคา = entry):
  TP = entry + (tp_atr_mult × ATR_i)        ← เส้นบน
  SL = entry − (sl_atr_mult × ATR_i)        ← เส้นล่าง
  เวลา = i + max_hold_bars                   ← เส้นขวา

เดินไปข้างหน้าทีละแท่ง ดูว่าชนเส้นไหน "ก่อน":
  ชน TP ก่อน           → label = 1 (ชนะ)
  ชน SL ก่อน           → label = 0 (แพ้)
  หมดเวลา (ไม่ชนทั้งคู่) → ตัดทิ้ง หรือ label ตามผลตอนปิด (กำหนดใน config)
```

### พารามิเตอร์ตั้งต้น (anchor 1h)
| พารามิเตอร์ | ค่าเริ่ม | หมายเหตุ |
|---|---|---|
| `tp_atr_mult` | 1.5 | ระยะ TP = 1.5×ATR |
| `sl_atr_mult` | 1.0 | ระยะ SL = 1.0×ATR → R:R ≈ 1.5 |
| `max_hold_bars` | 24 | ถือสูงสุด 24 ชม. (1h) |
| `min_atr_pct` | จาก fees | ATR ต้องใหญ่พอให้ TP คุ้มค่าธรรมเนียม (ดูข้อ 6) |

> **เหตุผลใช้ ATR ไม่ใช่ %คงที่:** ระยะปรับตามความผันผวนของแต่ละช่วง/เหรียญ → label สม่ำเสมอข้ามสินทรัพย์

---

## 5. ④ TRAIN — Meta-Labeling (หัวใจข้อ 2)

### 5.1 โครงสร้าง 2 ชั้น
```
ชั้น 1 (Primary):   สัญญาณเข้าจากกลยุทธ์เดิม (ฉันทามติศาสตร์ + เงื่อนไข live_signal)
                          ↓  ระบุ "candidate entries"
ชั้น 2 (Meta-model): ML ทำนาย P(ไม้นี้จะชนะ) จากฟีเจอร์ ณ จุดเข้า
                          ↓
              เข้าจริงเฉพาะ P(ชนะ) ≥ threshold
```

- **Training set ของ meta-model** = เฉพาะจุดที่ชั้น 1 ให้สัญญาณ (ไม่ใช่ทุกแท่ง) + label จาก triple-barrier
- **ทำไมได้ winrate สูง:** meta-model ทำหน้าที่ "ตัวกรอง precision" — ปฏิเสธไม้คุณภาพต่ำทิ้ง

### 5.2 โมเดล
- **เริ่มที่:** logistic regression (ของเดิม — โปร่งใส, เบา, อธิบายได้, deploy เป็น JSON ได้)
- **อัปเกรดถ้าจำเป็น:** gradient boosting (LightGBM/XGBoost) — แรงกว่าแต่ deploy หนักกว่า, เสี่ยง overfit มากกว่า → ใช้เมื่อ logistic ตันแล้วเท่านั้น
- **คุม overfit:** L2 regularization, ฟีเจอร์ไม่เยอะเกิน, ข้อมูลเยอะ

### 5.3 จัดการ class imbalance
ถ้าไม้ชนะ/แพ้ไม่สมดุล → ใช้ class weights หรือ threshold calibration (Platt scaling) ให้ค่า prob สื่อความน่าจะเป็นจริง

---

## 6. ⑤ VALIDATE — ด่านความจริง (สำคัญที่สุด)

### 6.1 Walk-Forward / Purged K-Fold
```
[==== train ====][test]
       [==== train ====][test]
              [==== train ====][test]   ← เลื่อนหน้าต่างไปข้างหน้า
```
- ทดสอบกับ "อนาคต" ที่โมเดลไม่เคยเห็นเสมอ
- **Embargo / Purging:** เว้นช่องว่างระหว่าง train/test = `max_hold_bars` เพื่อกัน label ของ train ไปเหลื่อมกับ test (López de Prado) → กัน leakage จาก label ที่ทับช่วงเวลากัน

### 6.2 หักต้นทุนจริง (ขาดไม่ได้)
```
ต้นทุนต่อรอบ = fee_in + fee_out + slippage
Bitkub: ~0.25% × 2 = 0.50% + slippage
→ TP ต้องกินเกิน 0.50% ถึงจะกำไรสุทธิ
→ min_atr_pct ใน label ต้องสูงพอ (เช่น TP_net = tp_atr_mult×ATR − 0.50% > 0)
```

### 6.3 Metrics ที่วัด (ทุกตัวบน out-of-sample, net of fees)
- **Precision (winrate)** ← เป้าหมายหลัก
- **Expectancy** ต่อไม้
- **จำนวนเทรด** (ต้อง ≥ 30 ต่อ fold ไม่งั้นสถิติไม่น่าเชื่อ)
- **Profit Factor** = กำไรรวม / ขาดทุนรวม
- **Max Drawdown**
- **Sharpe / Sortino** (optional)

### 6.4 Validation Gate (เกณฑ์ DEPLOY)
```
✅ ผ่านก็ต่อเมื่อ (median ข้าม fold, net of fees):
   • Precision        ≥ 0.65
   • Expectancy       > 0 ชัดเจน
   • Profit Factor    ≥ 1.3
   • #trades/fold     ≥ 30
   • Max Drawdown     ≤ เพดานที่ตั้ง
❌ ตกข้อใดข้อหนึ่ง → ไม่ deploy, กลับไปปรับ
```

> Winrate 70–80% = ปรับ threshold ของ meta-model ให้สูงขึ้น (แลกกับ #trades ลดลง)
> ต้องเช็กว่า #trades ยังพอ + expectancy ยังบวก

---

## 7. ⑥ REGISTER — Versioning

ทุกครั้งที่เทรน เขียนไฟล์ใหม่ลง `models/registry/` (ไม่ทับ):
```
models/registry/<trained_at>_<symbols>_<tf>_<modeltype>.json
```
ในไฟล์ต้องมี (ต่อยอด schema เดิม):
- `feature_names`, `means`, `scales`, `weights`, `intercept`
- `label`: {method: "triple_barrier", tp_atr_mult, sl_atr_mult, max_hold_bars}
- `meta`: {primary_signal_desc, threshold}
- `validation`: metrics ต่อ fold + median + ผ่าน gate ไหม
- `costs`: {fee, slippage}
- `sources`, `trained_at`, `data_range`

**Promotion:** ตัวที่ผ่าน gate + เด่นสุด → copy เป็น `models/math_edge_model.json` (production)
ถ้าตัวใหม่แย่ลง → ย้อนกลับได้ทันทีจาก registry

---

## 8. ⑦ DEPLOY — เข้าแอป

### 8.1 🐞 บั๊กที่ต้องแก้ก่อน (ค้นพบแล้ว)
`Dockerfile` ไม่ได้ copy `models/` → บนเครื่อง HF ไม่มีไฟล์โมเดล → UI ขึ้น "missing"
**แก้:** เพิ่ม `COPY models ./models` ใน Dockerfile

### 8.2 Contract กับแอป
- `predict()` ใน `math_model.py` โหลด `models/math_edge_model.json` อยู่แล้ว
- บอทใช้เป็น gate ที่ `autotrade.py` (`trained_model_min_prob`)
- **ถ้าเปลี่ยนชุดฟีเจอร์/label** ต้องอัปเดต `FEATURE_NAMES` + logic ฝั่ง predict ให้ตรงกัน (จุดนี้คือที่ skew ชอบเกิด — ระวัง)

---

## 9. ⚠️ Anti-Patterns (กับดักที่ฆ่าระบบ)

1. **Lookahead/Leakage** — ฟีเจอร์/label แตะข้อมูลอนาคต → ผลสวยหลอกๆ
2. **Overfitting** — โมเดลซับซ้อน + ข้อมูลน้อย → เก่งในอดีต พังของจริง
3. **ลืมหักค่าธรรมเนียม** — โดยเฉพาะ TF เล็ก (1m/5m บน Bitkub แทบเป็นไปไม่ได้)
4. **Survivorship/Regime bias** — เทรนจากตลาดขาขึ้นล้วน → พังตอนขาลง
5. **ไล่ winrate จน #trades เหลือ 3 ไม้** — สถิติเชื่อไม่ได้ + ไม่คุ้มเวลา
6. **เทรน TF ไม่ตรงที่เทรด** — ต้องตรงกัน

---

## 10. พารามิเตอร์ตั้งต้นทั้งหมด (config.yaml — anchor 1h)
```yaml
market: bitkub_spot
direction: long
timeframe: 1h
symbols_train: [BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT, ...]   # Binance (กว้าง)
symbols_finetune: [BTC_THB, ETH_THB, SOL_THB]                       # Bitkub (ตรงตลาดจริง)
data_range: {start: 2019-01-01, end: today}

label:
  method: triple_barrier
  tp_atr_mult: 1.5
  sl_atr_mult: 1.0
  max_hold_bars: 24

meta_model:
  type: logistic            # → lightgbm ถ้าตัน
  threshold: 0.65           # ดันขึ้นเพื่อเพิ่ม winrate (แลก #trades)

validation:
  scheme: walk_forward
  folds: 5
  embargo_bars: 24          # = max_hold_bars
  costs: {fee_pct: 0.25, slippage_pct: 0.05}   # ต่อฝั่ง

gate:
  min_precision: 0.65
  min_profit_factor: 1.3
  min_trades_per_fold: 30
  require_positive_expectancy: true
```

---

## 11. แผนลงมือ (Phased)

| เฟส | งาน | ผลลัพธ์ |
|---|---|---|
| **0** | แก้ Dockerfile (`COPY models`) + push HF | โมเดลปัจจุบันเลิก "missing" |
| **1** | สร้าง `training/` + ย้ายสคริปต์ + `ingest.py` (CCXT/yfinance) | โหลดข้อมูลหลายแหล่งได้ |
| **2** | `labels.py` triple-barrier + `dataset.py` | training set แบบใหม่ |
| **3** | `train_pipeline.py` + walk-forward + costs + gate + registry | เทรน+ตรวจ+เก็บเวอร์ชัน จบในคำสั่งเดียว |
| **4** | (ถ้าจำเป็น) meta-labeling 2 ชั้น + อัปเกรดโมเดล | ดัน precision |
| **5** | โหลดข้อมูลจริงเยอะ → เทรน → ผ่าน gate → deploy | บอทฉลาดขึ้นจริง |

---

## 12. Prompt สำเร็จรูปสำหรับสั่ง Code Generation

> "อ่าน `training/DESIGN.md` แล้วลงมือเฟส [N]:
> สร้างไฟล์ `training/<file>.py` ตามสเปกในเอกสาร โดย **import โค้ดฟีเจอร์จาก `backend/app/math_model.py`** (ห้ามเขียนฟีเจอร์ซ้ำ),
> ใช้ schema CSV เดิม, ทำ triple-barrier labeling ตามข้อ 4, validation แบบ walk-forward + embargo + หักค่าธรรมเนียมตามข้อ 6,
> เขียนผลเป็น versioned JSON ลง `models/registry/` ตามข้อ 7, และผ่าน Validation Gate ตามข้อ 6.4 ก่อน promote เป็น production.
> เขียนให้รันบน Windows + .venv ได้, dependency เบาที่สุดเท่าที่ทำได้, มี logging ชัดเจน"

---

_เอกสารนี้คือ "logic ที่แท้จริง": ไม่ไล่ winrate — สร้างตัวประเมิน P(ชนะ) ที่แม่น + เลือกเข้าเฉพาะไม้มั่นใจสูง + มีด่าน validation ที่ซื่อสัตย์ → winrate สูงจะตามมาอย่างยั่งยืน_
