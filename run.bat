@echo off
title MarketPulse - XAUUSD / BTCUSD Live News
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  where py >nul 2>nul && (py server.py & goto :eof)
  echo Python not found. Install Python 3.9+ from https://www.python.org/downloads/ and tick "Add to PATH".
  pause
  goto :eof
)
python server.py
pause
