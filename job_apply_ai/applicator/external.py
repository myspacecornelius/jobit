"""External-ATS handoff.

When a LinkedIn job sends you to a company career site, we can't reliably
auto-submit (every ATS differs, many require auth). What we *can* do:

1. Detect the ATS from the URL.
2. Generate a prefill artifact — JSON with the user's profile data and
   boilerplate answers — so the user can copy/paste while filling it out.
3. Open the URL in a browser window for the user.
4. Record the job in the tracker as `NEEDS_MANUAL`.

The detector is intentionally small; extend as you hit new sites.
"""

from __future__ import annotations

import json
import webbrowser
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

from job_apply_ai.config import get_config
from job_apply_ai.profile.qa_bank import QABank
from job_apply_ai.profile.user_profile import UserProfile
from job_apply_ai.tracker import Application, ApplicationStatus, ApplicationTracker
from job_apply_ai.utils.logging import get_logger

logger = get_logger(__name__)


# host substring -> friendly name
ATS_HOSTS = {
    "greenhouse.io": "Greenhouse",
    "lever.co": "Lever",
    "myworkdayjobs.com": "Workday",
    "workday.com": "Workday",
    "ashbyhq.com": "Ashby",
    "smartrecruiters.com": "SmartRecruiters",
    "jobvite.com": "Jobvite",
    "icims.com": "iCIMS",
    "bamboohr.com": "BambooHR",
    "taleo.net": "Taleo",
    "successfactors.com": "SuccessFactors",
    "wellfound.com": "Wellfound",
    "angel.co": "Wellfound",
    "builtin.com": "Built In",
}


@dataclass
class ATSDetection:
    provider: str
    domain: str
    needs_login: bool


@dataclass
class PrefillArtifact:
    job_url: str
    title: str
    company: str
    ats: ATSDetection
    profile: Dict[str, object]
    suggested_answers: Dict[str, str]


def detect_ats(url: str) -> ATSDetection:
    host = (urlparse(url).hostname or "").lower()
    for needle, name in ATS_HOSTS.items():
        if needle in host:
            return ATSDetection(provider=name, domain=host, needs_login=True)
    return ATSDetection(provider="Unknown", domain=host, needs_login=True)


_COMMON_QUESTIONS = [
    "Full name",
    "Email",
    "Phone",
    "LinkedIn URL",
    "Portfolio URL",
    "GitHub URL",
    "Location",
    "Are you authorized to work?",
    "Do you require sponsorship?",
    "Willing to relocate?",
    "Years of experience",
    "Current company",
    "Current title",
    "Desired salary",
    "When can you start?",
    "Why do you want this role?",
    "How did you hear about this job?",
]


class ExternalApplicationHandler:
    """Generates prefill artifacts and opens external job pages for the user."""

    def __init__(
        self,
        profile: UserProfile,
        qa_bank: QABank,
        tracker: ApplicationTracker,
        output_dir: Optional[Path] = None,
    ):
        self.profile = profile
        self.qa_bank = qa_bank
        self.tracker = tracker
        self.output_dir = output_dir or get_config().paths.outputs / "prefills"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def handle(
        self,
        job_url: str,
        title: str,
        company: str,
        open_browser: bool = True,
        openai_fallback: bool = False,
    ) -> Path:
        """Record the job as needs_manual and emit a prefill JSON. Returns its path."""
        ats = detect_ats(job_url)
        logger.info("External job at %s detected as %s", ats.domain, ats.provider)

        suggested: Dict[str, str] = {}
        for q in _COMMON_QUESTIONS:
            ans = self.qa_bank.answer(q, self.profile, openai_fallback=openai_fallback)
            if ans:
                suggested[q] = ans

        artifact = PrefillArtifact(
            job_url=job_url,
            title=title,
            company=company,
            ats=ats,
            profile={
                "full_name": self.profile.full_name,
                "email": self.profile.email,
                "phone": self.profile.phone,
                "location": self.profile.location,
                "linkedin_url": self.profile.linkedin_url,
                "portfolio_url": self.profile.portfolio_url,
                "github_url": self.profile.github_url,
                "resume_path": self.profile.resume_path,
                "cover_letter_path": self.profile.cover_letter_path,
            },
            suggested_answers=suggested,
        )

        safe_company = "".join(c for c in company if c.isalnum() or c in ("-", "_")) or "company"
        safe_title = "".join(c for c in title if c.isalnum() or c in ("-", "_")) or "job"
        out_path = self.output_dir / f"{safe_company}_{safe_title}.json"
        with out_path.open("w", encoding="utf-8") as fh:
            json.dump(asdict(artifact), fh, indent=2, default=str)
        logger.info("Prefill written to %s", out_path)

        self.tracker.record(
            Application(
                job_url=job_url,
                title=title,
                company=company,
                status=ApplicationStatus.NEEDS_MANUAL,
                note=f"external:{ats.provider}",
                questions=suggested,
            )
        )

        if open_browser:
            try:
                webbrowser.open(job_url, new=2)
            except Exception as exc:
                logger.warning("Could not open browser: %s", exc)

        return out_path

    def handle_many(
        self,
        jobs: List[Dict[str, str]],
        open_browser: bool = True,
        openai_fallback: bool = False,
    ) -> List[Path]:
        paths: List[Path] = []
        for job in jobs:
            url = job.get("link") or job.get("url")
            if not url:
                continue
            if self.tracker.has_applied(url):
                continue
            paths.append(
                self.handle(
                    url,
                    job.get("title", ""),
                    job.get("company", ""),
                    open_browser=open_browser,
                    openai_fallback=openai_fallback,
                )
            )
        return paths
