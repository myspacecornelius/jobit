"""Generate a tailored cover letter for a job via OpenAI.

Writes a `.docx` under `outputs/cover_letters/` and returns the path.
If a template path is configured (JOBIT_COVER_LETTER_TEMPLATE), body
paragraphs are appended to a copy of the template so header formatting
(logo, contact block, signature) is preserved. Otherwise a plain doc is
produced from scratch.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from shutil import copyfile
from typing import Optional

from job_apply_ai.config import get_config
from job_apply_ai.profile.user_profile import UserProfile
from job_apply_ai.utils.logging import get_logger

logger = get_logger(__name__)


class CoverLetterError(RuntimeError):
    pass


@dataclass
class CoverLetterRequest:
    job_title: str
    company: str
    job_description: str
    hiring_manager: str = ""
    extra_context: str = ""


def generate_cover_letter(
    request: CoverLetterRequest,
    profile: UserProfile,
    output_dir: Optional[Path] = None,
) -> Path:
    """Produce a tailored cover letter and write it to disk. Returns the path.

    Raises CoverLetterError if OpenAI is unavailable or the export fails.
    """
    cfg = get_config()
    if not cfg.openai.api_key:
        raise CoverLetterError(
            "OPENAI_API_KEY is not set — cover letter generation requires it."
        )

    body = _draft_body(request, profile)

    out_dir = output_dir or (cfg.paths.outputs / "cover_letters")
    out_dir.mkdir(parents=True, exist_ok=True)

    safe_company = _slug(request.company) or "company"
    safe_title = _slug(request.job_title) or "role"
    out_path = out_dir / f"CoverLetter_{safe_company}_{safe_title}.docx"

    _write_docx(out_path, body, request, profile, cfg.applicator.cover_letter_template)
    logger.info("Cover letter written to %s", out_path)
    return out_path


def _draft_body(request: CoverLetterRequest, profile: UserProfile) -> str:
    from openai import OpenAI  # type: ignore

    cfg = get_config().openai
    client = OpenAI(api_key=cfg.api_key)

    system = (
        "You draft concise, professional cover letters in first person. "
        "Match the tone of the job description. Reference 1-2 concrete skills "
        "or experiences from the candidate's profile that map to the role. "
        "Do not fabricate credentials. 3 short paragraphs, under 280 words total. "
        "Do not include headers, dates, or signature — body paragraphs only."
    )

    skills_line = ", ".join(profile.skill_years.keys()) or "(not provided)"
    user = (
        f"Candidate: {profile.full_name}\n"
        f"Current title: {profile.current_title or 'n/a'}\n"
        f"Current company: {profile.current_company or 'n/a'}\n"
        f"Years experience: {profile.years_experience}\n"
        f"Top skills: {skills_line}\n"
        f"\n"
        f"Job: {request.job_title} at {request.company}\n"
        f"Hiring manager: {request.hiring_manager or 'Hiring Manager'}\n"
        f"Job description:\n{request.job_description[:4000]}\n"
        f"{('Extra context: ' + request.extra_context) if request.extra_context else ''}"
    )

    try:
        resp = client.chat.completions.create(
            model=cfg.model,
            temperature=cfg.temperature,
            max_tokens=cfg.max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
    except Exception as exc:
        raise CoverLetterError(f"OpenAI call failed: {exc}") from exc

    content = (resp.choices[0].message.content or "").strip()
    if not content:
        raise CoverLetterError("OpenAI returned an empty cover letter.")
    return content


def _write_docx(
    path: Path,
    body: str,
    request: CoverLetterRequest,
    profile: UserProfile,
    template: Optional[Path],
) -> None:
    try:
        from docx import Document  # type: ignore
    except ImportError as exc:
        raise CoverLetterError(
            "python-docx not installed. Run: pip install -r requirements.txt"
        ) from exc

    if template and template.is_file():
        copyfile(template, path)
        doc = Document(str(path))
        for paragraph in body.split("\n\n"):
            text = paragraph.strip()
            if text:
                doc.add_paragraph(text)
        doc.save(str(path))
        return

    doc = Document()
    doc.add_paragraph(profile.full_name)
    contact_bits = [profile.email, profile.phone, profile.location]
    contact = " | ".join(b for b in contact_bits if b)
    if contact:
        doc.add_paragraph(contact)
    doc.add_paragraph(date.today().strftime("%B %d, %Y"))
    doc.add_paragraph(request.hiring_manager or "Hiring Manager")
    doc.add_paragraph(request.company)
    doc.add_paragraph("")
    doc.add_paragraph(f"Dear {request.hiring_manager or 'Hiring Manager'},")
    for paragraph in body.split("\n\n"):
        text = paragraph.strip()
        if text:
            doc.add_paragraph(text)
    doc.add_paragraph("")
    doc.add_paragraph("Sincerely,")
    doc.add_paragraph(profile.full_name)
    doc.save(str(path))


def _slug(value: str) -> str:
    return "".join(c for c in value if c.isalnum() or c in ("-", "_"))
