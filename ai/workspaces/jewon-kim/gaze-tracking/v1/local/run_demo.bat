@echo off
REM Staged vision demo: camera placement check -> 2-point calibration -> live gaze.
REM Double-click this file, or run it from a terminal with extra flags:
REM     run_demo.bat --english
REM     run_demo.bat --video tests/fixtures/static_face_30fps.mp4
cd /d "%~dp0"
".venv\Scripts\python.exe" -m ai.tools.pipeline_demo %*
set "demo_exit=%errorlevel%"
if not "%demo_exit%"=="0" pause
exit /b %demo_exit%
