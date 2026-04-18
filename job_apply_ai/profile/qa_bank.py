"""Question/answer knowledge base with fuzzy matching + optional OpenAI fallback.

Order of resolution inside `answer()`:
  1. Exact match in the YAML bank (user-curated).
  2. Fuzzy match in the YAML bank (difflib SequenceMatcher >= threshold).
  3. Profile-derived answer (name, email, years of experience, etc.).
  4. OpenAI generation, if `openai_fallback=True` and a key is configured.
  5. None — caller decides to skip or prompt.

Unanswered questions are appended to a `_unknown_questions.yaml` sibling file
so you can review and add them to the bank later.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from job_apply_ai.config import get_config
from job_apply_ai.profile.user_profile import UserProfile, profile_answer_for
from job_apply_ai.utils.logging import get_logger

logger = get_logger(__name__)

_FUZZY_THRESHOLD = 0.82


@dataclass
class QABank:
    """Holds the YAML-backed knowledge base."""

    path: Path
    entries: Dict[str, str]
    unknown_path: Path

    @classmethod
    def load(cls, path: Path) -> "QABank":
        entries: Dict[str, str] = {}
        if path.is_file():
            try:
                with path.open("r", encoding="utf-8") as fh:
                    raw = yaml.safe_load(fh) or {}
                if isinstance(raw, dict):
                    entries = {str(k): _as_str(v) for k, v in raw.items()}
            except yaml.YAMLError as exc:
                logger.error("Could not parse QA bank at %s: %s", path, exc)
        else:
            logger.info("QA bank not found at %s — starting empty", path)

        return cls(
            path=path,
            entries=entries,
            unknown_path=path.with_name("_unknown_questions.yaml"),
        )

    def _fuzzy_match(self, question: str) -> Optional[str]:
        q = question.lower().strip()
        best_key, best_score = None, 0.0
        for key in self.entries:
            score = SequenceMatcher(None, q, key.lower().strip()).ratio()
            if score > best_score:
                best_key, best_score = key, score
        if best_key is not None and best_score >= _FUZZY_THRESHOLD:
            logger.debug("Fuzzy match for %r -> %r (score=%.2f)", question, best_key, best_score)
            return self.entries[best_key]
        return None

    def answer(
        self,
        question: str,
        profile: UserProfile,
        openai_fallback: bool = False,
    ) -> Optional[str]:
        if not question:
            return None

        if question in self.entries:
            return self.entries[question]

        fuzzy = self._fuzzy_match(question)
        if fuzzy is not None:
            return fuzzy

        profile_hit = profile_answer_for(profile, question)
        if profile_hit is not None:
            return profile_hit

        if openai_fallback:
            generated = _generate_with_openai(question, profile)
            if generated:
                logger.info("Generated answer via OpenAI for: %s", question)
                return generated

        self._log_unknown(question)
        return None

    def _log_unknown(self, question: str) -> None:
        existing: List[str] = []
        if self.unknown_path.is_file():
            try:
                with self.unknown_path.open("r", encoding="utf-8") as fh:
                    data = yaml.safe_load(fh) or []
                    if isinstance(data, list):
                        existing = [str(item) for item in data]
            except yaml.YAMLError:
                pass

        if question in existing:
            return
        existing.append(question)
        with self.unknown_path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(existing, fh, sort_keys=False)
        logger.warning("Logged unknown question to %s: %s", self.unknown_path, question)


def _as_str(value: object) -> str:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value)


def _generate_with_openai(question: str, profile: UserProfile) -> Optional[str]:
    cfg = get_config().openai
    if not cfg.api_key:
        return None
    try:
        from openai import OpenAI  # type: ignore
    except ImportError:
        logger.warning("openai package not installed; skipping AI fallback")
        return None

    client = OpenAI(api_key=cfg.api_key)
    profile_snippet = json.dumps(
        {
            "name": profile.full_name,
            "years_experience": profile.years_experience,
            "current_title": profile.current_title,
            "current_company": profile.current_company,
            "location": profile.location,
            "skills": list(profile.skill_years.keys()),
        },
        indent=2,
    )
    try:
        resp = client.chat.completions.create(
            model=cfg.model,
            temperature=cfg.temperature,
            max_tokens=300,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are answering a job application question on the candidate's behalf. "
                        "Reply concisely in first person. If the question asks for a number or "
                        "yes/no, respond with just that. Otherwise keep under 2 sentences. "
                        "Use the candidate profile when relevant; never fabricate credentials."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Candidate profile:\n{profile_snippet}\n\nQuestion: {question}",
                },
            ],
        )
        return resp.choices[0].message.content.strip()
    except Exception as exc:
        logger.error("OpenAI fallback failed: %s", exc)
        return None
