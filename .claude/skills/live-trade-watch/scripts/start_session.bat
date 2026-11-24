@echo off
rem Start the live-trade-watch session WITHOUT Claude: setup scanner + news watcher (logs in .local\live\)
rem and the Trade Radar window (beeps and shows the trade card). Double-click it, or schedule it for 09:25 ET.
rem Edit WATCH / NEWS below to change the tickers. Everything stops by itself after 16:00 ET.

set WATCH=--watch SNDK=SNXX:2,SNDQ:-2 --watch NBIS=NBIZ:-2 --watch INTC=INTW:2 --watch AMZN=AMZU:2,AMZD:-1
set NEWS=--q "chip stocks" --q "SanDisk stock" --q "Nebius stock" --q "Intel stock" --q "Amazon stock" --q "AI regulation Congress"

cd /d "%~dp0..\..\..\.."
set PYTHONIOENCODING=utf-8
if not exist .local\live mkdir .local\live
start "live-trade-watch setups" /min cmd /c ".venv\Scripts\python.exe -u .claude\skills\live-trade-watch\scripts\watch_setups.py %WATCH% >> .local\live\setups.log 2>&1"
start "live-trade-watch news" /min cmd /c ".venv\Scripts\python.exe -u .claude\skills\live-trade-watch\scripts\watch_news.py %NEWS% >> .local\live\news.log 2>&1"
start "" ".venv\Scripts\pythonw.exe" ".claude\skills\live-trade-watch\scripts\radar_gui.py"
echo Started: setups and news watchers (logs in .local\live\), and the Trade Radar window.
