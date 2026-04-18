"""Drive LinkedIn's Easy Apply multi-step form.

Flow each call to `apply(job_url)`:
  1. Navigate to the job page.
  2. Click Easy Apply (fail fast if button isn't present).
  3. Loop: fill visible fields -> click Next/Review.
  4. At the review step: click Submit (or bail in dry-run).
  5. Record the outcome in the tracker.

All selectors come from config; LinkedIn changes its DOM often, so overrides
live in `.env` without needing a code edit.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from job_apply_ai.applicator.form_filler import (
    FieldFillResult,
    click_first_available,
    fill_visible_fields,
)
from job_apply_ai.applicator.session import BrowserSession
from job_apply_ai.config import ApplicatorConfig, get_config
from job_apply_ai.profile.qa_bank import QABank
from job_apply_ai.profile.user_profile import UserProfile
from job_apply_ai.tracker import Application, ApplicationStatus, ApplicationTracker
from job_apply_ai.utils.logging import get_logger

logger = get_logger(__name__)

MAX_FORM_STEPS = 8


@dataclass
class EasyApplyResult:
    job_url: str
    status: ApplicationStatus
    note: str = ""
    answered: Dict[str, str] = field(default_factory=dict)
    unanswered: List[str] = field(default_factory=list)


class EasyApplyDriver:
    """Orchestrates Easy Apply for one or many jobs within a single session."""

    def __init__(
        self,
        session: BrowserSession,
        profile: UserProfile,
        qa_bank: QABank,
        tracker: ApplicationTracker,
        applicator_cfg: Optional[ApplicatorConfig] = None,
        dry_run: bool = True,
        openai_fallback: bool = False,
    ):
        self.session = session
        self.profile = profile
        self.qa_bank = qa_bank
        self.tracker = tracker
        self.cfg = applicator_cfg or get_config().applicator
        self.dry_run = dry_run
        self.openai_fallback = openai_fallback
        self._applied_this_run = 0
        self._rate_window_start = time.time()

    def apply_many(self, jobs: List[Dict[str, str]], limit: Optional[int] = None) -> List[EasyApplyResult]:
        """Apply to a list of {title, company, link} dicts."""
        results: List[EasyApplyResult] = []
        to_process = jobs[:limit] if limit else jobs

        for i, job in enumerate(to_process, 1):
            url = job.get("link") or job.get("url")
            title = job.get("title", "")
            company = job.get("company", "")
            if not url:
                logger.warning("Skipping job %d — no URL", i)
                continue

            if self.tracker.has_applied(url):
                logger.info("Skipping already-applied: %s @ %s", title, company)
                results.append(
                    EasyApplyResult(job_url=url, status=ApplicationStatus.SKIPPED, note="duplicate")
                )
                continue

            self._enforce_rate_limit()

            logger.info("[%d/%d] Applying: %s @ %s", i, len(to_process), title, company)
            result = self.apply(url, title=title, company=company)
            results.append(result)

            if i < len(to_process):
                delay = random.uniform(self.cfg.delay_min, self.cfg.delay_max)
                logger.debug("Sleeping %.1fs before next application", delay)
                time.sleep(delay)

        return results

    def apply(self, job_url: str, title: str = "", company: str = "") -> EasyApplyResult:
        driver = self.session.driver
        if driver is None:
            raise RuntimeError("BrowserSession not started — call session.start() first")

        answered: Dict[str, str] = {}
        unanswered: List[str] = []

        def _record_and_return(status: ApplicationStatus, note: str) -> EasyApplyResult:
            app = Application(
                job_url=job_url, title=title, company=company,
                status=status, note=note, questions=answered,
            )
            self.tracker.record(app)
            return EasyApplyResult(
                job_url=job_url, status=status, note=note,
                answered=answered, unanswered=unanswered,
            )

        try:
            driver.get(job_url)
            time.sleep(random.uniform(2, 4))
        except WebDriverException as exc:
            return _record_and_return(ApplicationStatus.FAILED, f"nav_error:{exc}")

        if not click_first_available(driver, self.cfg.easy_apply_selectors, timeout=5):
            return _record_and_return(
                ApplicationStatus.NEEDS_MANUAL,
                "Easy Apply button not found — likely an external application",
            )

        resolver = self._make_resolver(answered, unanswered)
        submitted = False

        for step in range(1, MAX_FORM_STEPS + 1):
            time.sleep(1.2)

            step_results = fill_visible_fields(driver, resolver)
            self._log_step(step, step_results)

            hard_unknowns = [
                r.label for r in step_results
                if not r.filled and r.skipped_reason == "unknown" and r.label
            ]
            if hard_unknowns:
                return _record_and_return(
                    ApplicationStatus.NEEDS_MANUAL,
                    f"unknown_question: {hard_unknowns[0]}",
                )

            if click_first_available(driver, self.cfg.submit_button_selectors, timeout=2):
                if self.dry_run:
                    logger.info("[dry-run] would have submitted — stopping before actual submit")
                    return _record_and_return(ApplicationStatus.DRY_RUN, "dry_run")

                if self.cfg.confirm_before_submit:
                    input(">>> About to click Submit. Press Enter to confirm, Ctrl-C to abort.\n")

                time.sleep(1.5)
                submitted = True
                break

            if click_first_available(driver, self.cfg.review_button_selectors, timeout=2):
                continue
            if click_first_available(driver, self.cfg.next_button_selectors, timeout=2):
                continue

            return _record_and_return(
                ApplicationStatus.FAILED,
                f"No next/review/submit button on step {step}",
            )

        if submitted:
            self._applied_this_run += 1
            return _record_and_return(ApplicationStatus.APPLIED, "submitted")
        return _record_and_return(ApplicationStatus.FAILED, "exceeded_max_steps")

    def _make_resolver(self, answered: Dict[str, str], unanswered: List[str]):
        def resolve(label: str) -> Optional[str]:
            # Resume / cover letter go through file-path fields.
            lower = label.lower()
            if "resume" in lower or "cv" in lower:
                return self.profile.resume_path or None
            if "cover letter" in lower:
                return self.profile.cover_letter_path or None

            ans = self.qa_bank.answer(
                label, self.profile, openai_fallback=self.openai_fallback
            )
            if ans is None:
                unanswered.append(label)
            else:
                answered[label] = ans
            return ans

        return resolve

    def _log_step(self, step: int, results: List[FieldFillResult]) -> None:
        for r in results:
            if r.filled:
                logger.debug("step %d fill %s = %r", step, r.label, r.answer)
            elif r.label:
                logger.debug(
                    "step %d skip %s (%s)", step, r.label, r.skipped_reason or "n/a"
                )

    def _enforce_rate_limit(self) -> None:
        """Pause if we've already applied to `max_per_hour` in the last hour."""
        window = 3600
        now = time.time()
        if now - self._rate_window_start >= window:
            self._rate_window_start = now
            self._applied_this_run = self.tracker.applied_in_last(window)

        already_applied = self.tracker.applied_in_last(window)
        if already_applied >= self.cfg.max_per_hour:
            wait_for = window - (now - self._rate_window_start)
            logger.warning(
                "Rate limit hit (%d/h). Sleeping %.0fs before continuing.",
                self.cfg.max_per_hour, max(wait_for, 60),
            )
            time.sleep(max(wait_for, 60))
