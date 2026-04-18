"""Auto-applicator package — drives LinkedIn Easy Apply and external ATSs.

WARNING: Automating interactions with LinkedIn violates their Terms of Service.
Use against your own account at your own risk. Defaults are intentionally
conservative: dry-run on, rate-limited, fails closed on unknown questions.
"""

from job_apply_ai.applicator.session import BrowserSession
from job_apply_ai.applicator.easy_apply import EasyApplyDriver, EasyApplyResult
from job_apply_ai.applicator.external import ExternalApplicationHandler, detect_ats
from job_apply_ai.applicator.cover_letter import (
    CoverLetterError,
    CoverLetterRequest,
    generate_cover_letter,
)

__all__ = [
    "BrowserSession",
    "EasyApplyDriver",
    "EasyApplyResult",
    "ExternalApplicationHandler",
    "detect_ats",
    "CoverLetterError",
    "CoverLetterRequest",
    "generate_cover_letter",
]
