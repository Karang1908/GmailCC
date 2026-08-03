@echo off
setlocal enabledelayedexpansion
REM clientmail installer -- Windows
REM Double-click this file, or run it from a terminal.
REM Safe to re-run: never overwrites config.json, drafts, or edited templates.

set "REPO=Karang1908/GmailCC"
if not "%CLIENTMAIL_REPO%"=="" set "REPO=%CLIENTMAIL_REPO%"
set "BRANCH=main"
if not "%CLIENTMAIL_BRANCH%"=="" set "BRANCH=%CLIENTMAIL_BRANCH%"

set "HOME_DIR=%USERPROFILE%\.clientmail"
if not "%CLIENTMAIL_HOME%"=="" set "HOME_DIR=%CLIENTMAIL_HOME%"
set "APP_DIR=%HOME_DIR%\app"
set "SKILLS_DIR=%USERPROFILE%\.claude\skills"

echo.
echo === Checking prerequisites ===

set "PYTHON="
for %%P in (python.exe python3.exe py.exe) do (
  if not defined PYTHON (
    for /f "delims=" %%F in ('where %%P 2^>nul') do (
      if not defined PYTHON (
        "%%F" -c "import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)" >nul 2>&1
        if !errorlevel! equ 0 set "PYTHON=%%F"
      )
    )
  )
)
if not defined PYTHON (
  echo.
  echo ERROR: Python 3.9 or newer was not found.
  echo Install it from https://python.org/downloads   ^(tick "Add python.exe to PATH"^)
  echo then run this installer again.
  echo.
  pause
  exit /b 1
)
for /f "delims=" %%V in ('"%PYTHON%" -c "import platform;print(platform.python_version())"') do set "PYVER=%%V"
echo   [ok] python %PYVER% at %PYTHON%

where git >nul 2>&1 && (echo   [ok] git found) || (echo   [!!] git not found - /client-work cannot read a repo baseline without it)

set "HAVE_CLAUDE=1"
where claude >nul 2>&1 || set "HAVE_CLAUDE=0"
if "%HAVE_CLAUDE%"=="1" (echo   [ok] claude CLI found) else (echo   [!!] claude CLI not found - I will print the command to run later)

REM --- get the source -----------------------------------------------------
set "SRC=%~dp0"
if "%SRC:~-1%"=="\" set "SRC=%SRC:~0,-1%"

if exist "%SRC%\server\clientmail" (
  echo.
  echo === Installing from local folder ===
  echo   %SRC%
) else (
  echo.
  echo === Downloading clientmail ^(%REPO%@%BRANCH%^) ===
  set "TMP_DIR=%TEMP%\clientmail-install"
  if exist "!TMP_DIR!" rmdir /s /q "!TMP_DIR!"
  mkdir "!TMP_DIR!"
  where curl >nul 2>&1
  if !errorlevel! equ 0 (
    curl -fsSL "https://codeload.github.com/%REPO%/tar.gz/refs/heads/%BRANCH%" -o "!TMP_DIR!\src.tar.gz"
  ) else (
    powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://codeload.github.com/%REPO%/tar.gz/refs/heads/%BRANCH%' -OutFile '!TMP_DIR!\src.tar.gz'"
  )
  if not exist "!TMP_DIR!\src.tar.gz" (
    echo ERROR: download failed. Check the repo name, or set CLIENTMAIL_REPO=owner/name
    pause
    exit /b 1
  )
  tar -xzf "!TMP_DIR!\src.tar.gz" -C "!TMP_DIR!"
  for /d %%D in ("!TMP_DIR!\*") do (
    if exist "%%D\server\clientmail" set "SRC=%%D"
  )
  if not exist "!SRC!\server\clientmail" (
    echo ERROR: archive did not contain server\clientmail
    pause
    exit /b 1
  )
  echo   [ok] downloaded
)

REM --- lay out files ------------------------------------------------------
echo.
echo === Installing to %HOME_DIR% ===
for %%D in ("%APP_DIR%" "%HOME_DIR%\templates" "%HOME_DIR%\drafts" "%HOME_DIR%\sessions") do (
  if not exist %%D mkdir %%D
)

if exist "%APP_DIR%\server" rmdir /s /q "%APP_DIR%\server"
xcopy "%SRC%\server" "%APP_DIR%\server" /E /I /Q /Y >nul
if exist "%SRC%\n8n"   xcopy "%SRC%\n8n"   "%APP_DIR%\n8n"   /E /I /Q /Y >nul
if exist "%SRC%\tools" xcopy "%SRC%\tools" "%APP_DIR%\tools" /E /I /Q /Y >nul
xcopy "%SRC%\templates" "%APP_DIR%\templates_stock" /E /I /Q /Y >nul
copy /Y "%SRC%\config.example.json" "%APP_DIR%\config.example.json" >nul
echo   [ok] code installed

set /a NEW_T=0
set /a KEPT_T=0
for %%F in ("%SRC%\templates\*") do (
  if exist "%HOME_DIR%\templates\%%~nxF" (
    set /a KEPT_T+=1
  ) else (
    copy /Y "%%F" "%HOME_DIR%\templates\%%~nxF" >nul
    set /a NEW_T+=1
  )
)
echo   [ok] templates: !NEW_T! added, !KEPT_T! left as you had them

set "FRESH_CONFIG=0"
if exist "%HOME_DIR%\config.json" (
  echo   [ok] config.json already present - untouched
) else (
  copy /Y "%SRC%\config.example.json" "%HOME_DIR%\config.json" >nul
  "%PYTHON%" -c "import json,secrets,sys;p=sys.argv[1];c=json.load(open(p));c['webhook_secret']=secrets.token_urlsafe(32);json.dump(c,open(p,'w'),indent=2)" "%HOME_DIR%\config.json"
  echo   [ok] config.json created with a generated webhook_secret
  set "FRESH_CONFIG=1"
)

REM --- cli shim -----------------------------------------------------------
set "SHIM=%HOME_DIR%\clientmail.cmd"
> "%SHIM%" echo @echo off
>>"%SHIM%" echo "%PYTHON%" "%APP_DIR%\server\clientmail_cli.py" %%*
echo   [ok] clientmail command -^> %SHIM%

echo %PATH% | find /i "%HOME_DIR%" >nul
if errorlevel 1 (
  echo   [!!] Add %HOME_DIR% to your PATH to use 'clientmail' from anywhere:
  echo        setx PATH "%%PATH%%;%HOME_DIR%"
)

REM --- claude code skills -------------------------------------------------
echo.
echo === Installing Claude Code skills ===
for %%S in (gmailsum client-work) do (
  if not exist "%SKILLS_DIR%\%%S" mkdir "%SKILLS_DIR%\%%S"
  copy /Y "%SRC%\skills\%%S\SKILL.md" "%SKILLS_DIR%\%%S\SKILL.md" >nul
  echo   [ok] /%%S
)
REM /client-update was folded into /gmailsum; two skills matching "email the
REM client" would make Claude pick between them at random.
if exist "%SKILLS_DIR%\client-update" (
  rmdir /s /q "%SKILLS_DIR%\client-update"
  echo   [ok] removed /client-update ^(superseded by /gmailsum^)
)

REM --- register the mcp server --------------------------------------------
echo.
echo === Registering the MCP server ===
set "MCP_CMD=claude mcp add clientmail -s user -- "%PYTHON%" "%APP_DIR%\server\clientmail_server.py""
if "%HAVE_CLAUDE%"=="1" (
  claude mcp remove clientmail -s user >nul 2>&1
  claude mcp add clientmail -s user -- "%PYTHON%" "%APP_DIR%\server\clientmail_server.py" >nul 2>&1
  if !errorlevel! equ 0 (
    echo   [ok] registered as 'clientmail' ^(user scope^)
  ) else (
    echo   [!!] automatic registration failed. Run this yourself:
    echo        !MCP_CMD!
  )
) else (
  echo   [!!] run this once the claude CLI is installed:
  echo        !MCP_CMD!
)

echo.
echo ============================================================
echo  Installed.
echo ============================================================
echo.
echo  One-time setup left - about 5 minutes:
echo.
echo    1. Set up n8n ^(import the workflow, connect Gmail^):
echo       %APP_DIR%\SETUP.md
echo       workflow to import: %APP_DIR%\n8n\clientmail-send.workflow.json
echo.
echo    2. Put your n8n webhook URL + secret in:
echo       %HOME_DIR%\config.json
if "%FRESH_CONFIG%"=="1" echo       ^(a webhook_secret was generated for you - copy it into n8n^)
echo.
echo    3. Check it:            clientmail check --ping
echo    4. Mail yourself first: clientmail test-email you@example.com
echo.
echo  Note: allowed_recipients starts locked to one address, so the first
echo  send to a real client is refused until you add them. That is deliberate.
echo.
echo  Then in any repo, after doing some work:  /gmailsum
echo.
pause
