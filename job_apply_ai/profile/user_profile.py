"""Load the user's application profile from YAML."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from job_apply_ai.utils.logging import get_logger

logger = get_logger(__name__)


class ProfileError(RuntimeError):
    """Raised when the profile file is missing or malformed."""


@dataclass
class UserProfile:
    """What the applicator needs to fill in any job form."""

    # Contact / identity
    first_name: str
    last_name: str
    email: str
    phone: str
    location: str = ""
    linkedin_url: str = ""
    portfolio_url: str = ""
    github_url: str = ""

    # Work authorization
    authorized_to_work: bool = True
    requires_sponsorship: bool = False

    # Logistics
    willing_to_relocate: bool = False
    willing_to_commute: bool = True
    remote_only: bool = False

    # Compensation & experience
    years_experience: int = 0
    current_company: str = ""
    current_title: str = ""
    desired_salary_usd: Optional[int] = None
    notice_period_weeks: int = 2

    # Skill years (skill name -> years)
    skill_years: Dict[str, int] = field(default_factory=dict)

    # Files
    resume_path: str = ""
    cover_letter_path: str = ""

    # Demographics (optional, for EEO self-id forms)
    gender: str = "Decline to self-identify"
    ethnicity: str = "Decline to self-identify"
    veteran_status: str = "I am not a protected veteran"
    disability_status: str = "I do not wish to answer"

    # Free-form extras for custom questions
    extras: Dict[str, Any] = field(default_factory=dict)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


def load_profile(path: Path) -> UserProfile:
    """Read the YAML profile and return a UserProfile.

    Raises ProfileError with a clear message if anything is off.
    """
    if not path.is_file():
        raise ProfileError(
            f"Profile not found at {path}. Copy profile.example.yaml to "
            f"{path.name} and fill in your details."
        )

    try:
        with path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    except yaml.YAMLError as exc:
        raise ProfileError(f"Could not parse {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ProfileError(f"{path} must be a YAML mapping (key: value).")

    required = ("first_name", "last_name", "email", "phone")
    missing = [k for k in required if not raw.get(k)]
    if missing:
        raise ProfileError(
            f"Profile at {path} is missing required fields: {', '.join(missing)}"
        )

    valid_keys = set(UserProfile.__dataclass_fields__.keys())
    unknown = [k for k in raw if k not in valid_keys]
    if unknown:
        logger.warning("Unknown profile keys (stashed in extras): %s", unknown)

    known = {k: v for k, v in raw.items() if k in valid_keys}
    extras = {**raw.get("extras", {}), **{k: v for k, v in raw.items() if k not in valid_keys}}
    known["extras"] = extras

    profile = UserProfile(**known)
    logger.info("Loaded profile for %s", profile.full_name)
    return profile


def profile_answer_for(profile: UserProfile, question: str) -> Optional[str]:
    """Best-effort answer to a standard question using profile fields.

    Returns None if the question doesn't map to a known field — the QA bank
    and/or OpenAI fallback handle the rest.
    """
    q = question.lower().strip()

    # Years of experience in $skill
    if "year" in q and "experience" in q:
        for skill, years in profile.skill_years.items():
            if skill.lower() in q:
                return str(years)
        return str(profile.years_experience)

    if "first name" in q:
        return profile.first_name
    if "last name" in q or "surname" in q:
        return profile.last_name
    if "full name" in q or q in ("name", "your name"):
        return profile.full_name
    if "email" in q:
        return profile.email
    if "phone" in q or "mobile" in q or "contact number" in q:
        return profile.phone
    if "linkedin" in q and "url" in q:
        return profile.linkedin_url
    if "portfolio" in q or "website" in q:
        return profile.portfolio_url
    if "github" in q:
        return profile.github_url
    if "location" in q or "city" in q or "address" in q:
        return profile.location
    if "authorized" in q or "authorised" in q or "eligible to work" in q:
        return "Yes" if profile.authorized_to_work else "No"
    if "sponsorship" in q or "visa" in q:
        return "Yes" if profile.requires_sponsorship else "No"
    if "relocate" in q:
        return "Yes" if profile.willing_to_relocate else "No"
    if "notice period" in q:
        return f"{profile.notice_period_weeks} weeks"
    if "salary" in q or "compensation" in q or "expected pay" in q:
        return str(profile.desired_salary_usd) if profile.desired_salary_usd else None
    if "current company" in q or "current employer" in q:
        return profile.current_company
    if "current title" in q or "current role" in q or "job title" in q:
        return profile.current_title
    if "gender" in q:
        return profile.gender
    if "ethnic" in q or "race" in q:
        return profile.ethnicity
    if "veteran" in q:
        return profile.veteran_status
    if "disab" in q:
        return profile.disability_status

    return profile.extras.get(question) if isinstance(profile.extras, dict) else None
