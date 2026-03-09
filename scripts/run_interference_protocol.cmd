@echo off
setlocal

where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  py -3 -m csi_capture.interference_protocol %*
  exit /b %ERRORLEVEL%
)

python -m csi_capture.interference_protocol %*
exit /b %ERRORLEVEL%
