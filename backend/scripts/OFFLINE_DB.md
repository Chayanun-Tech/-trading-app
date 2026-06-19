# ฐานข้อมูลงบการเงินออฟไลน์ (US ทั้งตลาด)

แนวคิด: **ดาวน์โหลดงบหุ้นมาเก็บไว้ก่อนล่วงหน้า** → เปิดแอปเรียกดูหุ้นตัวไหนก็อ่านจากฐานออฟไลน์ได้ทันที
(เร็ว ไม่ต้องรอเน็ต ไม่โดน SEC บล็อก). หุ้นที่ยังไม่มีในฐาน → กดปุ่ม **🔄 ดึงสดจาก SEC** ในแอป
เพื่อดึงมาเก็บเพิ่มทีหลัง.

ข้อมูลมาจาก **SEC EDGAR companyfacts** (งบ XBRL ย้อนหลังลึก 10–15 ปี) — ฟรี ไม่ต้องใช้คีย์.
ไฟล์เก็บที่ `data/financials/facts_<cik>.json` (รูปแบบเดียวกับที่แอปอ่าน → ใช้ได้เลย).

## เริ่มจาก 3 ดัชนีหลัก (แนะนำ: ทำก่อน --all)

```bat
REM 1) ดึงรายชื่อสมาชิก S&P500 + Nasdaq100 + Russell2000 (รวม ~2,400 ตัวไม่ซ้ำ)
backend\.venv\Scripts\python backend\scripts\fetch_index_members.py

REM 2) ดาวน์โหลดงบของทั้ง 3 ดัชนี
backend\.venv\Scripts\python backend\scripts\build_offline_db.py --all --tickers-file data\financials\index_tickers.txt
```

รายชื่อถูกเขียนไว้ที่ `data/financials/index_members.json` (แยกตามดัชนี) และ
`data/financials/index_tickers.txt` (union). Nasdaq-100 เป็น snapshot ฝังในสคริปต์
(แก้ตัวแปร `NASDAQ100` ใน fetch_index_members.py ได้เมื่อดัชนีปรับสมาชิก);
Russell 2000 เป็นรายชื่อโดยประมาณ (ดัชนีนี้ไม่มีลิสต์ฟรีที่เป็นทางการ).

## วิธีสร้างฐาน (รันในเครื่อง)

เปิด Command Prompt ที่โฟลเดอร์ `trading-app` แล้วพิมพ์:

```bat
REM ทดลองก่อน 50 ตัวแรก (เร็ว ดูว่าทำงานไหม)
backend\.venv\Scripts\python backend\scripts\build_offline_db.py --limit 50

REM ดาวน์โหลดทั้งตลาด US (~10,000+ ตัว ใช้เวลาเป็นชั่วโมง + พื้นที่หลาย GB)
backend\.venv\Scripts\python backend\scripts\build_offline_db.py --all

REM เจาะจงบางตัว
backend\.venv\Scripts\python backend\scripts\build_offline_db.py --only AAPL,MSFT,KO
```

> ตั้ง `set PYTHONUTF8=1` ก่อนถ้าเจอปัญหา path ภาษาไทย (run.bat ตั้งให้อยู่แล้ว).

### resume / รันต่อ
สคริปต์ **ข้ามตัวที่ดาวน์โหลดไว้แล้วภายใน 30 วัน** อัตโนมัติ — ถ้าหยุดกลางคันก็รันคำสั่งเดิมซ้ำได้เลย
มันจะไปต่อจากที่ค้าง. ตัวที่พลาดจะถูกบันทึกไว้ที่ `data/financials/failures.json`.

ปรับความเร็ว/ความถี่รีเฟรชได้:
- `--rate 7` = เรียก SEC 7 ครั้ง/วินาที (เพดาน SEC = 10)
- `--refresh-days 30` = ถ้ามีไฟล์ใหม่กว่า 30 วันให้ข้าม (ตั้ง 0 = ดึงใหม่ทุกตัว)

## เช็คสถานะฐาน
- ในแอป: หน้า **VI/พื้นฐาน → งบการเงิน** จะมีป้าย `📁 มีในฐาน N ตัว` และปุ่ม `🔄 ดึงสดจาก SEC`
- ผ่าน API: `GET /api/offline/status` หรือ `GET /api/offline/status?symbol=AAPL`

## พฤติกรรมการเสิร์ฟ (offline-first)
- มีไฟล์ในฐานแล้ว → เสิร์ฟทันที **ไม่สนอายุ cache** (ไม่ดึงสดเอง)
- ยังไม่มีในฐาน → ดึงสดจาก SEC ครั้งแรกให้อัตโนมัติ แล้วเก็บลงฐาน
- กดปุ่ม `🔄 ดึงสดจาก SEC` (หรือ `?refresh=true`) = บังคับดึงใหม่มาทับของเดิม
- ปิด offline-first ได้ด้วย env `EDGAR_OFFLINE_FIRST=0` (กลับไปใช้ TTL 7 วันแบบเดิม)

## หุ้นไทย (SET50 / SET100 / SETHD / mai)

หุ้นไทยไม่ได้ยื่น SEC → ใช้คนละสาย: ดึง snapshot จาก **Yahoo (.BK)** เก็บลง
`backend/app/offline_fundamentals.json` (ไฟล์เดียวกับปุ่ม "🔄 อัปเดตขึ้นเว็ป" ในแอป).

```bat
REM 1) ดึงรายชื่อสมาชิก SET50/SET100/SETHD จาก PDF ทางการของ SET
backend\.venv\Scripts\python backend\scripts\fetch_thai_indices.py

REM 2) ดาวน์โหลดพื้นฐานหุ้นไทย (รัน "ในเครื่อง" เท่านั้น — Yahoo เข้าถึงหุ้นไทยได้เฉพาะในเครื่อง)
backend\.venv\Scripts\python backend\scripts\build_thai_db.py --indices set50,set100,sethd
```

- รายชื่อเก็บที่ `data/thai/set_indices.json` (regen ได้จาก fetch_thai_indices.py)
- ดึงครบแล้ว: SET50=50, SET100=100, SETHD=30 (100% ครบ)
- **mai**: ดึงรายชื่ออัตโนมัติไม่ได้ (SET บล็อก API, ไม่มี PDF, Wikipedia เก่า). ถ้าต้องการ
  เตรียมไฟล์รายชื่อ mai เอง (1 ตัว/บรรทัด) แล้ว:
  `build_thai_db.py --tickers-file data\thai\mai.txt`

> อัปเดต URL ใน fetch_thai_indices.py เมื่อ SET ออกลิสต์รอบใหม่ (ทุกครึ่งปี).

## หมายเหตุ
- `data/financials/` และ PDF ใน `data/thai/` อยู่ใน `.gitignore` (เป็น cache/source — regen ได้)
  ส่วนข้อมูลหุ้นไทยที่ดึงมาอยู่ใน `offline_fundamentals.json` (commit ขึ้น repo/เว็ปได้)
