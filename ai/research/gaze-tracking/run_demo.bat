@echo off
REM Staged vision demo: set-up check -> head-fixed calibration (lens, screen centre, video bottom) -> live gaze.
REM Double-click this file, or run it from a terminal with extra flags:
REM     run_demo.bat --english
REM     run_demo.bat --video tests/fixtures/local/static_face_30fps.mp4
cd /d "%~dp0"
".venv\Scripts\python.exe" tools\pipeline_demo.py %*
set "demo_exit=%errorlevel%"
if not "%demo_exit%"=="0" pause
exit /b %demo_exit%
