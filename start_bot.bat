@echo off
REM ==== Activar entorno virtual ====
cd /d C:\Users\ilang\OneDrive\RuntimeServices\TradingBots\Project

REM Activa el entorno virtual (.venv), ajusta si usas otro nombre
call .venv\Scripts\activate.bat

REM Ejecuta el bot
python notification_bot.py

REM Mantener ventana abierta si falla
pause
