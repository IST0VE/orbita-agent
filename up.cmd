@echo off
rem Orbita in one command on Windows: double-click or run .\up.cmd
rem The logic lives in scripts\up.ps1; Bypass lifts the default script policy.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\up.ps1" %*
exit /b %ERRORLEVEL%
