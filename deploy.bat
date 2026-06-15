@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ================================================
echo   AI Trade Assistant - Deploy to website
echo ================================================
echo.
echo [1/2] Pushing to Hugging Face (live website)...
git push hf HEAD:main
echo.
echo [2/2] Pushing to GitHub (source backup)...
git push origin HEAD:main
echo.
echo ================================================
echo   DONE. Wait 2-5 min for Hugging Face to build,
echo   then refresh the web with Ctrl+Shift+R.
echo ================================================
echo.
pause
