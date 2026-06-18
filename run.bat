@echo off
REM ===== AI Trade Assistant — รันในเครื่อง (หุ้นไทยได้ครบผ่าน Yahoo, ฟรี) =====
REM ดับเบิลคลิกไฟล์นี้ได้เลย ครั้งแรกจะติดตั้ง dependency ให้อัตโนมัติ
setlocal
cd /d "%~dp0backend"

if not exist ".venv\Scripts\python.exe" (
  echo [setup] กำลังสร้าง virtual environment ครั้งแรก...
  python -m venv .venv
  if errorlevel 1 (
    echo [error] ไม่พบ Python — ติดตั้ง Python 3.11+ จาก python.org ก่อน
    pause
    exit /b 1
  )
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

set PYTHONUTF8=1
echo.
echo ====================================================
echo   AI Trade Assistant กำลังเปิดที่  http://localhost:8000
echo   (หุ้นไทย เช่น KBANK.BK / PTT.BK ใช้งานได้เต็มในเครื่อง)
echo   ปิดเซิร์ฟเวอร์: กด Ctrl+C ในหน้าต่างนี้
echo ====================================================
echo.
start "" http://localhost:8000
".venv\Scripts\python.exe" -m uvicorn app.main:app --port 8000
endlocal
