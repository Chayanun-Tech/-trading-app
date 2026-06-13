# AI Trade Assistant (MVP)

แอปช่วยเทรดที่ใช้ **AI วิเคราะห์กราฟ** ตามเทมเพลตนักวิเคราะห์มืออาชีพ + ระบบ **แจ้งเตือน realtime**
ขอบเขตเวอร์ชันนี้: **วิเคราะห์ + แจ้งเตือนเท่านั้น** (ไม่ส่งคำสั่งเทรดอัตโนมัติ)

> 📐 ดูพิมพ์เขียวระบบ ความเป็นไปได้ และ roadmap ทั้งหมดที่ [`BLUEPRINT.md`](./BLUEPRINT.md)

## ✨ คุณสมบัติ
- ดึง OHLCV → คำนวณอินดิเคเตอร์เอง (EMA, SMA, RSI, MACD, Market Structure, แนวรับ/แนวต้าน)
- กราฟแท่งเทียน + EMA ด้วย **TradingView Lightweight Charts** (ฟรี)
- ปุ่ม **วิเคราะห์ด้วย AI** → ให้ Claude (`claude-opus-4-8`) ออกรายงาน 8 หัวข้อตามเทมเพลต
- ระบบ **แจ้งเตือน** (ราคา/RSI ทะลุระดับ) + endpoint รับ **TradingView Webhook**
- **รันได้ทันทีโดยไม่ต้องมี API key** (ใช้ provider จำลองข้อมูล)

## 🚀 วิธีรัน

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # (ทางเลือก) ใส่คีย์จริงในไฟล์ .env
uvicorn app.main:app --reload --port 8000
```

เปิดเบราว์เซอร์ที่ **http://localhost:8000** — เอกสาร API ที่ **http://localhost:8000/docs**

## 🔑 การตั้งค่า (ไฟล์ `.env`)

| ตัวแปร | ค่า | ผล |
|---|---|---|
| `DATA_PROVIDER` | `mock` (ค่าเริ่มต้น) / `finnhub` | แหล่งข้อมูลราคา |
| `FINNHUB_API_KEY` | คีย์จาก finnhub.io | เปิดข้อมูลหุ้นสหรัฐจริง |
| `ANTHROPIC_API_KEY` | คีย์จาก console.anthropic.com | เปิดการวิเคราะห์ด้วย Claude เต็มรูปแบบ |
| `ANTHROPIC_MODEL` | `claude-opus-4-8` | รุ่นโมเดล |
| `TRADINGVIEW_WEBHOOK_SECRET` | ค่าสุ่ม | ยืนยัน webhook จาก TradingView |

> ถ้าไม่ใส่ `ANTHROPIC_API_KEY` ระบบจะใช้สรุปแบบกฎ (rule-based) แทน และแจ้งให้ตั้งค่า

## 🔗 เชื่อม TradingView (สัญญาณอัตโนมัติ)
TradingView ไม่มี API ดึงผลวิเคราะห์ — ใช้ **Webhook Alert** แทน:
ใน Pine Script ตั้ง Alert → URL = `http://<server>/webhook/tradingview` → message เป็น JSON เช่น
```json
{"secret":"<TRADINGVIEW_WEBHOOK_SECRET>","symbol":"AAPL","signal":"buy"}
```

## ⚠️ ข้อจำกัด
- ข้อมูล `mock` เป็นการสังเคราะห์ ไม่ใช่ราคาจริง — ใช้สาธิตระบบเท่านั้น
- หุ้นไทย realtime ต้องเพิ่ม provider ที่ต่อกับโบรก/ผู้ให้ข้อมูลที่มีสิทธิ์
- Alert เก็บใน memory (หายเมื่อรีสตาร์ท) — โปรดักชันต้องใช้ DB
- ไม่มีระบบ auth — อย่านำขึ้นเซิร์ฟเวอร์สาธารณะโดยไม่เพิ่มระบบยืนยันตัวตน
- **รายงานเป็นข้อมูลประกอบการตัดสินใจ ไม่ใช่คำแนะนำลงทุน ไม่การันตีกำไร**
