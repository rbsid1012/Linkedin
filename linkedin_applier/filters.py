"""Decide whether a job is worth applying to, before opening any form."""
from __future__ import annotations

from .config import Profile


def _has_any(text: str, needles: list[str]) -> str | None:
    t = text.lower()
    for n in needles:
        if n.lower() in t:
            return n
    return None


def evaluate(profile: Profile, title: str, company: str, description: str = "") -> tuple[bool, str]:
    """Return (ok, reason). Reason is empty when ok."""
    if profile.title_include and not _has_any(title, profile.title_include):
        return False, f"title lacks any of {profile.title_include}"
    hit = _has_any(title, profile.title_exclude)
    if hit:
        return False, f"title contains excluded '{hit}'"
    hit = _has_any(company, profile.company_exclude)
    if hit:
        return False, f"company excluded '{hit}'"
    hit = _has_any(description, profile.description_exclude)
    if hit:
        return False, f"description contains excluded '{hit}'"
    return True, ""
