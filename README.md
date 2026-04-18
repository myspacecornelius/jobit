# Job Application AI Agent

Scrape LinkedIn jobs, extract requirements, and generate tailored CVs — all from one CLI or a local web UI.

- **Job Scraping** — pull public LinkedIn job listings
- **Skill Extraction** — spaCy-based keyword matching against a curated skill ontology
- **CV Customization** — rewrite the skills section of a `.docx` template per job
- **Batch Mode** — generate one CV per job in one run
- **Web UI** — Flask-based, runs locally

## Quickstart (5 minutes)

```bash
git clone <your-fork-url>
cd Job-apply-AI-agent

# macOS / Linux
./install.sh

# Windows
install.bat
```

The installer creates a venv, installs deps, downloads the spaCy model,
copies `.env.example` → `.env`, and runs `job-apply-ai doctor` to verify everything.

Then:

```bash
source venv/bin/activate          # or: venv\Scripts\activate.bat on Windows
job-apply-ai web                  # open http://127.0.0.1:5000
```

## Prerequisites

- Python 3.8+
- Google Chrome installed (the scraper drives it via undetected-chromedriver)
- Optional: an OpenAI API key, only if you use AI-based features

## Configuration

All settings live in `.env` (copied from `.env.example` on first install).
Every key is optional — defaults are applied when unset. Highlights:

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | *(unset)* | Required only for AI features |
| `JOBIT_HOST` / `JOBIT_PORT` | `127.0.0.1` / `5000` | Web UI bind |
| `JOBIT_OUTPUT_DIR` | `./outputs` | Parent dir for jobs + CVs |
| `JOBIT_HEADLESS` | `true` | Run Chrome headless |
| `JOBIT_MAX_RETRIES` | `2` | Scraper retries on failure |
| `JOBIT_JOB_CARD_SELECTORS` | `base-card,job-search-card` | Fallback list of LinkedIn CSS classes |
| `JOBIT_SPACY_MODEL` | `en_core_web_sm` | spaCy model to use |
| `JOBIT_SPACY_AUTO_DOWNLOAD` | `false` | Auto-download the model on first run |

The scraper selectors are comma-separated fallback lists. When LinkedIn
ships a DOM change, you can usually update `.env` without editing code.
See `.env.example` for the full list.

## CLI

```bash
# Quick health check — run after install or when something misbehaves
job-apply-ai doctor

# Scrape jobs to Excel
job-apply-ai scrape --keyword "Software Engineer" --location "Berlin" --max-jobs 10

# Tailor a CV to one job description
job-apply-ai tailor --cv path/to/cv.docx --job path/to/job.txt

# Batch: one CV per row in an Excel file
job-apply-ai batch --cv path/to/cv.docx --jobs-file outputs/jobs/linkedin_jobs_YYYY-MM-DD.xlsx

# Start the web UI (honours JOBIT_HOST / JOBIT_PORT from .env)
job-apply-ai web
```

## Web UI flow

1. Upload your `.docx` CV template
2. Enter job title + location
3. Pick jobs and click "Make CV" (or "Generate All")
4. Download the tailored `.docx` (or the full ZIP)

## Project layout

```
job_apply_ai/
├── __init__.py       # auto-loads .env
├── __main__.py       # CLI entry point
├── config.py         # central config + env parsing
├── scraper/          # LinkedIn scraper (retries + fallback selectors)
├── cv_modifier/      # spaCy skill extraction + CV rewriting
├── ui/               # Flask web app
└── utils/            # logging + file helpers
outputs/              # jobs & tailored CVs land here (configurable)
```

## Troubleshooting

- **"No jobs found"** — run `job-apply-ai doctor`. If everything is OK,
  LinkedIn may have changed its DOM; override `JOBIT_JOB_CARD_SELECTORS`
  in `.env` (e.g. to `base-card,new-card-class`) and re-run.
- **"spaCy model 'en_core_web_sm' is not installed"** — run
  `python -m spacy download en_core_web_sm`, or set
  `JOBIT_SPACY_AUTO_DOWNLOAD=true` in `.env`.
- **"Could not start Chrome"** — install Google Chrome and ensure it is
  on PATH (macOS usually works out of the box from `/Applications`).

## Testing

See [TESTING_GUIDE.md](TESTING_GUIDE.md).

## License

MIT
