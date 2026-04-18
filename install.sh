#!/usr/bin/env bash
# Job Application AI Agent installer (macOS / Linux).
# Idempotent — safe to re-run.

set -euo pipefail

say() { printf "\n==> %s\n" "$*"; }
warn() { printf "\n[!] %s\n" "$*" >&2; }

# 1. Python check
if ! command -v python3 >/dev/null 2>&1; then
    warn "python3 is not installed. Install Python 3.8+ and re-run."
    exit 1
fi

python_version=$(python3 -c 'import sys; print("{}.{}".format(*sys.version_info[:2]))')
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)'; then
    warn "Python ${python_version} is too old. Install Python 3.8+ and re-run."
    exit 1
fi
say "Python ${python_version} OK"

# 2. Chrome check (scraper needs it)
if command -v google-chrome >/dev/null 2>&1 || \
   command -v chrome >/dev/null 2>&1 || \
   [ -d "/Applications/Google Chrome.app" ]; then
    say "Google Chrome detected"
else
    warn "Google Chrome was not found. Install Chrome before running the scraper."
fi

# 3. Virtualenv
if [ ! -d "venv" ]; then
    say "Creating virtual environment at ./venv"
    python3 -m venv venv
fi
# shellcheck disable=SC1091
source venv/bin/activate

# 4. Dependencies
say "Installing Python dependencies"
pip install --upgrade pip >/dev/null
pip install -r requirements.txt

# 5. spaCy model
SPACY_MODEL="${JOBIT_SPACY_MODEL:-en_core_web_sm}"
if python -c "import spacy; spacy.load('${SPACY_MODEL}')" >/dev/null 2>&1; then
    say "spaCy model '${SPACY_MODEL}' already installed"
else
    say "Downloading spaCy model '${SPACY_MODEL}'"
    python -m spacy download "${SPACY_MODEL}"
fi

# 6. Editable install
say "Installing job-apply-ai in editable mode"
pip install -e . >/dev/null

# 7. .env bootstrap
if [ ! -f ".env" ] && [ -f ".env.example" ]; then
    cp .env.example .env
    say "Created .env from .env.example — edit it to add your OpenAI key and overrides"
fi

# 8. Health check
say "Running environment check"
set +e
job-apply-ai doctor
status=$?
set -e

echo
echo "Installation complete."
echo "  Activate venv : source venv/bin/activate"
echo "  Web UI        : job-apply-ai web"
echo "  Help          : job-apply-ai --help"
exit $status
