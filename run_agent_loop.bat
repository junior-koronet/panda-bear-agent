@echo off
rem Watchdog: keeps Panda Bear running. If python.exe dies (crash, kill),
rem it is restarted after 5s. Exits right away if an instance is already up.
cd /d C:\PandaBearAgent
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set PYTHONUNBUFFERED=1
:loop
curl -s -o nul -m 3 http://127.0.0.1:8000/health && exit /b 0
echo [watchdog] %date% %time% iniciando Panda Bear>> panda_bear_startup.log
python main.py >> panda_bear_startup.log 2>&1
echo [watchdog] %date% %time% Panda Bear se detuvo (code %errorlevel%), reiniciando en 5s>> panda_bear_startup.log
ping -n 6 127.0.0.1 >nul
goto loop
