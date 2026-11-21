@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo STAIR needs its Python environment. Follow the installation steps in README.md.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -B tools\run_student_app.py --open-browser
if errorlevel 1 (
  echo STAIR could not start. Check whether another STAIR window is already using port 8020.
  pause
)
