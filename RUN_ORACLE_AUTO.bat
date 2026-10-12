@echo off
title Oracle Academy Automation Engine
color 0A

echo ============================================================
echo   Oracle Academy Automation Engine
echo   Full Multi-Course Automation (All 3 Courses)
echo ============================================================
echo.

cd /d "%~dp0"

REM Step 1: Check if Chrome is already running on debugging port 9222
powershell -NoProfile -Command "try { (Invoke-WebRequest -Uri 'http://localhost:9222/json/version' -TimeoutSec 1).StatusCode } catch { exit 1 }" >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo [STEP 1] Launching Google Chrome with remote debugging on port 9222...
    SET CHROME_EXE=
    IF EXIST "C:\Program Files\Google\Chrome\Application\chrome.exe" (
        SET CHROME_EXE=C:\Program Files\Google\Chrome\Application\chrome.exe
    ) ELSE IF EXIST "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" (
        SET CHROME_EXE=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe
    ) ELSE (
        SET CHROME_EXE=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe
    )

    IF EXIST "%CHROME_EXE%" (
        start "" "%CHROME_EXE%" --remote-debugging-port=9222 "--user-data-dir=%~dp0chrome_session" "https://academy.oracle.com"
        echo [STEP 2] Waiting for Chrome to initialize...
        timeout /t 4 /nobreak >nul
    ) ELSE (
        echo [WARNING] Chrome not found in standard paths. Launching engine directly...
    )
) ELSE (
    echo [*] Chrome is already running on port 9222.
)

echo.
echo [STEP 3] Launching Oracle Academy Autonomous Engine...
IF EXIST "%~dp0dist\OracleAuto.exe" (
    echo          Running compiled executable (dist\OracleAuto.exe)...
    "%~dp0dist\OracleAuto.exe"
) ELSE IF EXIST "%~dp0OracleAuto.exe" (
    echo          Running compiled executable (OracleAuto.exe)...
    "%~dp0OracleAuto.exe"
) ELSE (
    echo          Running via Python (oracle_runner.py)...
    python oracle_runner.py
)

echo.
echo ============================================================
echo   Automation finished. Press any key to exit.
echo ============================================================
pause
