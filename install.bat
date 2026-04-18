@echo off
setlocal EnableDelayedExpansion

echo Installing Job Application AI Agent...

REM 1. Python check
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Python is not installed. Install Python 3.8+ and re-run.
    exit /b 1
)

for /f "tokens=2" %%I in ('python --version 2^>^&1') do set PYVER=%%I
for /f "tokens=1,2 delims=." %%I in ("%PYVER%") do (
    set PYMAJOR=%%I
    set PYMINOR=%%J
)
if %PYMAJOR% lss 3 goto oldpy
if %PYMAJOR%==3 if %PYMINOR% lss 8 goto oldpy
echo ==^> Python %PYVER% OK
goto pyok

:oldpy
echo [!] Python %PYVER% is too old. Install Python 3.8+ and re-run.
exit /b 1

:pyok

REM 2. Chrome check
where chrome >nul 2>&1
if %errorlevel%==0 (
    echo ==^> Google Chrome detected
) else (
    if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" (
        echo ==^> Google Chrome detected
    ) else if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" (
        echo ==^> Google Chrome detected
    ) else (
        echo [!] Google Chrome not found. Install Chrome before running the scraper.
    )
)

REM 3. Virtualenv
if not exist "venv\" (
    echo ==^> Creating virtual environment at .\venv
    python -m venv venv
)
call venv\Scripts\activate.bat

REM 4. Dependencies
echo ==^> Installing Python dependencies
python -m pip install --upgrade pip >nul
pip install -r requirements.txt
if %errorlevel% neq 0 exit /b 1

REM 5. spaCy model
if "%JOBIT_SPACY_MODEL%"=="" set JOBIT_SPACY_MODEL=en_core_web_sm
python -c "import spacy; spacy.load('%JOBIT_SPACY_MODEL%')" >nul 2>&1
if %errorlevel%==0 (
    echo ==^> spaCy model %JOBIT_SPACY_MODEL% already installed
) else (
    echo ==^> Downloading spaCy model %JOBIT_SPACY_MODEL%
    python -m spacy download %JOBIT_SPACY_MODEL%
)

REM 6. Editable install
echo ==^> Installing job-apply-ai in editable mode
pip install -e . >nul
if %errorlevel% neq 0 exit /b 1

REM 7. .env bootstrap
if not exist ".env" (
    if exist ".env.example" (
        copy .env.example .env >nul
        echo ==^> Created .env from .env.example - edit it to add your OpenAI key and overrides
    )
)

REM 8. Health check
echo ==^> Running environment check
job-apply-ai doctor
set DOCTOR_STATUS=%errorlevel%

echo.
echo Installation complete.
echo   Activate venv : venv\Scripts\activate.bat
echo   Web UI        : job-apply-ai web
echo   Help          : job-apply-ai --help
exit /b %DOCTOR_STATUS%
