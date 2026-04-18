"""Command-line entry point for the Job Application AI Agent."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import List

from job_apply_ai.config import get_config
from job_apply_ai.utils.helpers import ensure_directory_exists
from job_apply_ai.utils.logging import get_logger

logger = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Job Application AI Agent")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    cfg = get_config()

    web = subparsers.add_parser("web", help="Start the web interface")
    web.add_argument("--host", default=cfg.web.host, help="Host to bind to (default: $JOBIT_HOST)")
    web.add_argument("--port", type=int, default=cfg.web.port, help="Port (default: $JOBIT_PORT)")
    web.add_argument("--debug", action="store_true", help="Run in debug mode")

    scrape = subparsers.add_parser("scrape", help="Scrape job listings")
    scrape.add_argument("--keyword", required=True, help="Job title or keyword")
    scrape.add_argument("--location", required=True, help="Location to search in")
    scrape.add_argument("--output", help="Output file path (Excel)")
    scrape.add_argument("--max-jobs", type=int, default=10, help="Maximum jobs to scrape")

    tailor = subparsers.add_parser("tailor", help="Tailor CV for a job")
    tailor.add_argument("--cv", required=True, help="Path to CV template (.docx)")
    tailor.add_argument("--job", help="Path to job description text file")
    tailor.add_argument("--jobs-file", help="Path to Excel file with job listings")
    tailor.add_argument("--output-dir", help="Directory for the tailored CVs")
    tailor.add_argument("--output", help="Output path for single-job tailoring")

    batch = subparsers.add_parser("batch", help="Generate CVs for all jobs in an Excel file")
    batch.add_argument("--cv", required=True, help="Path to CV template (.docx)")
    batch.add_argument("--jobs-file", required=True, help="Path to Excel with jobs")
    batch.add_argument("--output-dir", help="Directory for the tailored CVs")

    subparsers.add_parser(
        "login",
        help="Open a browser so you can log in to LinkedIn; saves cookies for later",
    )

    apply_cmd = subparsers.add_parser(
        "apply",
        help="Auto-apply to jobs (Easy Apply + external ATS handoff)",
    )
    apply_cmd.add_argument(
        "--jobs-file", required=True,
        help="Path to Excel with scraped jobs (output of `scrape`)",
    )
    apply_cmd.add_argument(
        "--limit", type=int, default=None, help="Stop after N applications",
    )
    apply_cmd.add_argument(
        "--submit", action="store_true",
        help="Actually click Submit. Default is dry-run (safer).",
    )
    apply_cmd.add_argument(
        "--external", choices=("skip", "prefill"), default="prefill",
        help="When a job is hosted on an external ATS: skip it or emit a prefill JSON.",
    )
    apply_cmd.add_argument(
        "--no-browser", action="store_true",
        help="Don't open external job URLs in a browser (prefill only).",
    )
    apply_cmd.add_argument(
        "--openai-fallback", action="store_true",
        help="Use OpenAI for questions not in your QA bank.",
    )
    apply_cmd.add_argument(
        "--confirm", action="store_true",
        help="Prompt for Enter before every Submit click.",
    )

    track = subparsers.add_parser("track", help="Show recent applications / stats")
    track.add_argument("--limit", type=int, default=20, help="Rows to show")
    track.add_argument("--stats", action="store_true", help="Summary counts only")

    cover = subparsers.add_parser("cover-letter", help="Generate a tailored cover letter")
    cover.add_argument("--job-description", required=True, help="Path to job description text file")
    cover.add_argument("--title", required=True, help="Job title")
    cover.add_argument("--company", required=True, help="Company name")
    cover.add_argument("--hiring-manager", default="", help="Addressee, if known")

    subparsers.add_parser("doctor", help="Check your environment and report fixes")

    return parser


def cmd_web(args) -> int:
    from job_apply_ai.ui.app import app

    app.run(host=args.host, port=args.port, debug=args.debug)
    return 0


def cmd_scrape(args) -> int:
    from job_apply_ai.scraper.linkedin import LinkedInScraper, ScraperError

    cfg = get_config()

    try:
        scraper = LinkedInScraper()
    except ScraperError as exc:
        logger.error(str(exc))
        return 1

    jobs = scraper.scrape_job_listings(args.keyword, args.location, max_jobs=args.max_jobs)
    if not jobs:
        logger.warning("No jobs found. Run `job-apply-ai doctor` to check your setup.")
        return 1

    output_file = args.output
    if not output_file:
        ensure_directory_exists(str(cfg.paths.jobs_dir))
        today_date = datetime.today().strftime("%Y-%m-%d")
        output_file = str(cfg.paths.jobs_dir / f"linkedin_jobs_{today_date}.xlsx")

    scraper.save_jobs_to_excel(jobs, output_file)
    logger.info("Fetching full descriptions...")
    for i, job in enumerate(jobs):
        logger.info("Fetching description %d/%d: %s", i + 1, len(jobs), job["title"])
        _, _, description = scraper.fetch_job_description(job["link"])
        jobs[i]["description"] = description

    scraper.save_jobs_to_excel(jobs, output_file)
    logger.info("Jobs saved to %s", output_file)
    return 0


def cmd_tailor(args) -> int:
    from job_apply_ai.cv_modifier.cv_analyzer import (
        CVAnalyzer,
        CVModifier,
        MissingSpacyModelError,
        batch_process_jobs,
    )

    cfg = get_config()

    if not os.path.isfile(args.cv):
        logger.error("CV template not found: %s", args.cv)
        return 1

    try:
        if args.jobs_file:
            output_dir = args.output_dir or str(cfg.paths.cvs_dir)
            generated = batch_process_jobs(args.jobs_file, args.cv, output_dir)
            if not generated:
                logger.warning("Failed to generate any CVs")
                return 1
            logger.info("Generated %d tailored CVs", len(generated))
            for cv_path in generated:
                logger.info("  - %s", cv_path)
            return 0

        if not args.job:
            logger.error("Either --job or --jobs-file is required")
            return 1

        try:
            with open(args.job, "r", encoding="utf-8") as fh:
                job_description = fh.read()
        except OSError as exc:
            logger.error("Could not read %s: %s", args.job, exc)
            return 1

        analyzer = CVAnalyzer()
        _, _, matched_categories = analyzer.extract_skills_from_description(job_description)

        modifier = CVModifier(args.cv)
        if not modifier.update_skills_section(matched_categories):
            logger.error("Failed to update skills section")
            return 1

        if args.output:
            output_path = args.output
        else:
            output_dir = args.output_dir or str(cfg.paths.cvs_dir)
            ensure_directory_exists(output_dir)
            today_date = datetime.today().strftime("%Y-%m-%d")
            output_path = os.path.join(output_dir, f"Tailored_CV_{today_date}.docx")

        if not modifier.save_modified_cv(output_path):
            return 1
        logger.info("Tailored CV saved to %s", output_path)
        return 0

    except MissingSpacyModelError as exc:
        logger.error(str(exc))
        return 1


def cmd_batch(args) -> int:
    from job_apply_ai.cv_modifier.cv_analyzer import MissingSpacyModelError, batch_process_jobs

    cfg = get_config()

    if not os.path.isfile(args.cv):
        logger.error("CV template not found: %s", args.cv)
        return 1
    if not os.path.isfile(args.jobs_file):
        logger.error("Jobs file not found: %s", args.jobs_file)
        return 1

    try:
        output_dir = args.output_dir or str(cfg.paths.cvs_dir)
        generated = batch_process_jobs(args.jobs_file, args.cv, output_dir)
    except MissingSpacyModelError as exc:
        logger.error(str(exc))
        return 1

    if not generated:
        logger.warning("No CVs generated")
        return 1

    logger.info("Generated %d tailored CVs", len(generated))
    for cv_path in generated:
        logger.info("  - %s", cv_path)
    return 0


def cmd_login(_args) -> int:
    from job_apply_ai.applicator.session import interactive_login, SessionError

    cfg = get_config()
    try:
        interactive_login(cfg.applicator.cookies_path)
    except SessionError as exc:
        logger.error(str(exc))
        return 1
    return 0


def _load_jobs_from_file(path: str) -> List[dict]:
    import pandas as pd

    df = pd.read_excel(path)
    return df.to_dict(orient="records")


def cmd_apply(args) -> int:
    from job_apply_ai.applicator.easy_apply import EasyApplyDriver
    from job_apply_ai.applicator.external import ExternalApplicationHandler, detect_ats
    from job_apply_ai.applicator.session import BrowserSession, SessionError
    from job_apply_ai.profile.qa_bank import QABank
    from job_apply_ai.profile.user_profile import ProfileError, load_profile
    from job_apply_ai.tracker import ApplicationTracker

    cfg = get_config()

    if not os.path.isfile(args.jobs_file):
        logger.error("Jobs file not found: %s", args.jobs_file)
        return 1

    try:
        profile = load_profile(cfg.applicator.profile_path)
    except ProfileError as exc:
        logger.error(str(exc))
        return 1

    qa_bank = QABank.load(cfg.applicator.qa_bank_path)
    tracker = ApplicationTracker(cfg.applicator.tracker_db)

    try:
        jobs = _load_jobs_from_file(args.jobs_file)
    except Exception as exc:
        logger.error("Could not read %s: %s", args.jobs_file, exc)
        return 1

    easy_jobs: List[dict] = []
    external_jobs: List[dict] = []
    for job in jobs:
        url = job.get("link") or job.get("url") or ""
        if not url:
            continue
        host = (url.split("/")[2] if "//" in url else "").lower()
        if "linkedin.com" in host:
            easy_jobs.append(job)
        else:
            external_jobs.append(job)

    logger.info("Queued %d LinkedIn + %d external jobs", len(easy_jobs), len(external_jobs))

    if args.confirm:
        cfg.applicator.confirm_before_submit = True

    applied = 0
    if easy_jobs:
        session = BrowserSession()
        try:
            session.start()
        except SessionError as exc:
            logger.error("%s  Tip: run `job-apply-ai login` first.", exc)
            return 1

        try:
            if not session.is_logged_in():
                logger.error(
                    "Not logged into LinkedIn. Run `job-apply-ai login` to save cookies."
                )
                return 1

            driver = EasyApplyDriver(
                session=session,
                profile=profile,
                qa_bank=qa_bank,
                tracker=tracker,
                dry_run=not args.submit,
                openai_fallback=args.openai_fallback,
            )
            results = driver.apply_many(easy_jobs, limit=args.limit)
            applied = sum(1 for r in results if r.status.value == "applied")
            for r in results:
                logger.info("  [%s] %s — %s", r.status.value, r.job_url, r.note)
        finally:
            session.stop()

    if external_jobs and args.external == "prefill":
        handler = ExternalApplicationHandler(profile, qa_bank, tracker)
        paths = handler.handle_many(
            external_jobs,
            open_browser=not args.no_browser,
            openai_fallback=args.openai_fallback,
        )
        for p in paths:
            logger.info("  [prefill] %s", p)

    logger.info(
        "Done. %d actual submits, %d external prefills, %d total tracked.",
        applied,
        len(external_jobs) if args.external == "prefill" else 0,
        tracker.stats().get("applied", 0) + tracker.stats().get("dry_run", 0),
    )
    return 0


def cmd_track(args) -> int:
    from job_apply_ai.tracker import ApplicationTracker

    tracker = ApplicationTracker(get_config().applicator.tracker_db)
    if args.stats:
        stats = tracker.stats()
        if not stats:
            print("No applications tracked yet.")
            return 0
        for status, n in sorted(stats.items()):
            print(f"  {status:<14} {n}")
        return 0

    rows = tracker.recent(limit=args.limit)
    if not rows:
        print("No applications tracked yet.")
        return 0
    for row in rows:
        print(
            f"  [{row['status']:<12}] {row['applied_at'][:19]}  "
            f"{row['title'][:40]:<40}  @ {row['company'][:24]:<24}  {row['note']}"
        )
    return 0


def cmd_cover_letter(args) -> int:
    from job_apply_ai.applicator.cover_letter import (
        CoverLetterError,
        CoverLetterRequest,
        generate_cover_letter,
    )
    from job_apply_ai.profile.user_profile import ProfileError, load_profile

    cfg = get_config()

    if not os.path.isfile(args.job_description):
        logger.error("Job description not found: %s", args.job_description)
        return 1

    try:
        profile = load_profile(cfg.applicator.profile_path)
    except ProfileError as exc:
        logger.error(str(exc))
        return 1

    with open(args.job_description, "r", encoding="utf-8") as fh:
        jd = fh.read()

    try:
        path = generate_cover_letter(
            CoverLetterRequest(
                job_title=args.title,
                company=args.company,
                job_description=jd,
                hiring_manager=args.hiring_manager,
            ),
            profile=profile,
        )
    except CoverLetterError as exc:
        logger.error(str(exc))
        return 1

    logger.info("Cover letter: %s", path)
    return 0


def cmd_doctor(_args) -> int:
    """Validate the environment and print actionable fixes."""
    cfg = get_config()
    problems: List[str] = []
    ok: List[str] = []

    env_path = Path.cwd() / ".env"
    if env_path.exists():
        ok.append(f".env found at {env_path}")
    else:
        problems.append(
            f"No .env file at {env_path}. Copy .env.example to .env and edit it."
        )

    try:
        import spacy  # noqa: F401
        ok.append("spaCy installed")
    except ImportError:
        problems.append("spaCy not installed. Run: pip install -r requirements.txt")
    else:
        import spacy as _spacy

        try:
            _spacy.load(cfg.nlp.spacy_model)
            ok.append(f"spaCy model '{cfg.nlp.spacy_model}' installed")
        except OSError:
            problems.append(
                f"spaCy model '{cfg.nlp.spacy_model}' is missing. Run: "
                f"python -m spacy download {cfg.nlp.spacy_model}"
            )

    if shutil.which("google-chrome") or shutil.which("chrome") or _mac_chrome_exists():
        ok.append("Google Chrome detected")
    else:
        problems.append(
            "Google Chrome was not found on PATH. Install Chrome — the scraper needs it."
        )

    try:
        import undetected_chromedriver  # noqa: F401
        ok.append("undetected-chromedriver installed")
    except ImportError:
        problems.append(
            "undetected-chromedriver not installed. Run: pip install -r requirements.txt"
        )

    for name, path in (
        ("outputs", cfg.paths.outputs),
        ("jobs dir", cfg.paths.jobs_dir),
        ("cvs dir", cfg.paths.cvs_dir),
    ):
        try:
            ensure_directory_exists(str(path))
            ok.append(f"{name} writable at {path}")
        except Exception as exc:
            problems.append(f"{name} ({path}) is not writable: {exc}")

    if not cfg.openai.api_key:
        ok.append("OPENAI_API_KEY not set (only required for AI features)")
    else:
        ok.append("OPENAI_API_KEY is set")

    try:
        import yaml  # noqa: F401
        ok.append("pyyaml installed")
    except ImportError:
        problems.append("pyyaml not installed. Run: pip install -r requirements.txt")

    profile_path = cfg.applicator.profile_path
    if profile_path.is_file():
        try:
            from job_apply_ai.profile.user_profile import load_profile
            load_profile(profile_path)
            ok.append(f"profile loaded from {profile_path}")
        except Exception as exc:
            problems.append(f"profile at {profile_path} is invalid: {exc}")
    else:
        problems.append(
            f"No profile at {profile_path}. Copy profile.example.yaml -> "
            f"{profile_path.name} and edit."
        )

    qa_path = cfg.applicator.qa_bank_path
    if qa_path.is_file():
        ok.append(f"qa_bank found at {qa_path}")
    else:
        ok.append(f"qa_bank not present at {qa_path} (optional — copy qa_bank.example.yaml)")

    if cfg.applicator.cookies_path.is_file():
        ok.append(f"LinkedIn cookies saved at {cfg.applicator.cookies_path}")
    else:
        ok.append("No LinkedIn cookies yet. Run `job-apply-ai login` before `apply`.")

    print("Environment check:\n")
    for item in ok:
        print(f"  [OK]   {item}")
    for item in problems:
        print(f"  [FIX]  {item}")

    if problems:
        print(f"\n{len(problems)} problem(s) found.")
        return 1
    print("\nAll checks passed. You're ready to run `job-apply-ai web`.")
    return 0


def _mac_chrome_exists() -> bool:
    return Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome").exists()


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    handlers = {
        "web": cmd_web,
        "scrape": cmd_scrape,
        "tailor": cmd_tailor,
        "batch": cmd_batch,
        "login": cmd_login,
        "apply": cmd_apply,
        "track": cmd_track,
        "cover-letter": cmd_cover_letter,
        "doctor": cmd_doctor,
    }
    handler = handlers.get(args.command)
    if not handler:
        parser.print_help()
        return 1

    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
