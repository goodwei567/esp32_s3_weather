@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0pc"
echo ============================================
echo   ESP32-S3 气象站 · 本地监控
echo   启动后浏览器打开: http://localhost:8000
echo   关闭本窗口即停止采集
echo ============================================
echo.
python app.py
echo.
echo 程序已退出（报错信息见上方），按任意键关闭窗口。
pause >nul
