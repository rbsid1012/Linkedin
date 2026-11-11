"""Config loading and validation."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

MODES = ("scan", "rehearse", "apply")


class ConfigError(ValueError):
    pass


@dataclass
class Settings:
    daily_limit: int = 20
    max_per_run: int = 10
    output_dir: Path = Path("out")
    min_delay: float = 20.0
    max_delay: float = 60.0
    pages_per_keyword: int = 2
    headless: bool = False
    user_data_dir: Path = Path(".browser-profile")
    db_path: Path = Path("applier.db")
    unknown_question_policy: str = "skip"  # skip | ask
    max_consecutive_failures: int = 3


@dataclass
class Profile:
    name: str
    resume: Path | None
    keywords: list[str]
    location: str = ""
    work_types: list[str] = field(default_factory=list)  # onsite | remote | hybrid
    experience_levels: list[str] = field(default_factory=list)
    posted_within_hours: int = 24
    title_include: list[str] = field(default_factory=list)
    title_exclude: list[str] = field(default_factory=list)
    company_exclude: list[str] = field(default_factory=list)
    description_exclude: list[str] = field(default_factory=list)
    cover_letter: Path | None = None
    answers: list[dict] = field(default_factory=list)  # profile-specific {match, answer}; beat global ones
    emphasis: dict[str, float] = field(default_factory=dict)  # tag -> weight for tailored resumes
    headline: str = ""


@dataclass
class Config:
    settings: Settings
    candidate: dict[str, Any]
    profiles: list[Profile]
    base_dir: Path
    master_resume: Path | None = None


def _list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    return [str(x) for x in v]


def load_config(path: str | Path) -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Config not found: {path} (copy config.example.yaml to config.yaml)")
    raw = yaml.safe_load(path.read_text()) or {}
    base = path.resolve().parent

    s_raw = raw.get("settings") or {}
    known = set(Settings.__dataclass_fields__)
    unknown = set(s_raw) - known
    if unknown:
        raise ConfigError(f"Unknown settings: {sorted(unknown)}")
    settings = Settings(**s_raw)
    settings.user_data_dir = _resolve(base, settings.user_data_dir)
    settings.db_path = _resolve(base, settings.db_path)
    settings.output_dir = _resolve(base, settings.output_dir)
    master = _resolve(base, raw["master_resume"]) if raw.get("master_resume") else None
    if settings.unknown_question_policy not in ("skip", "ask"):
        raise ConfigError("settings.unknown_question_policy must be 'skip' or 'ask'")
    if settings.min_delay > settings.max_delay:
        raise ConfigError("settings.min_delay must be <= max_delay")

    profiles = []
    for i, p in enumerate(raw.get("profiles") or []):
        for req in ("name", "keywords") + (() if master else ("resume",)):
            if not p.get(req):
                raise ConfigError(f"profiles[{i}] is missing '{req}'")
        wt = _list(p.get("work_types"))
        bad = set(wt) - {"onsite", "remote", "hybrid"}
        if bad:
            raise ConfigError(f"profiles[{i}].work_types invalid: {sorted(bad)}")
        profiles.append(
            Profile(
                name=p["name"],
                resume=_resolve(base, p["resume"]) if p.get("resume") else None,
                keywords=_list(p["keywords"]),
                location=p.get("location", ""),
                work_types=wt,
                experience_levels=_list(p.get("experience_levels")),
                posted_within_hours=int(p.get("posted_within_hours", 24)),
                title_include=_list(p.get("title_include")),
                title_exclude=_list(p.get("title_exclude")),
                company_exclude=_list(p.get("company_exclude")),
                description_exclude=_list(p.get("description_exclude")),
                cover_letter=_resolve(base, p["cover_letter"]) if p.get("cover_letter") else None,
                emphasis={str(k): float(v) for k, v in (p.get("emphasis") or {}).items()},
                headline=p.get("headline", ""),
                answers=_check_answers(p.get("answers") or [], f"profiles[{i}].answers"),
            )
        )
    if not profiles:
        raise ConfigError("Define at least one entry under 'profiles'")
    names = [p.name for p in profiles]
    if len(set(names)) != len(names):
        raise ConfigError("Profile names must be unique")

    candidate = raw.get("candidate") or {}
    if raw.get("answers_file"):
        af = _resolve(base, raw["answers_file"])
        if not af.is_file():
            raise ConfigError(f"answers_file not found: {af}")
        extra = yaml.safe_load(af.read_text()) or []
        candidate["answers"] = list(candidate.get("answers") or []) + _check_answers(extra, str(af))
    candidate["answers"] = _check_answers(candidate.get("answers") or [], "candidate.answers")
    return Config(settings=settings, candidate=candidate, profiles=profiles, base_dir=base, master_resume=master)


def _resolve(base: Path, p: str | Path) -> Path:
    p = Path(p).expanduser()
    return p if p.is_absolute() else base / p


def check_resumes(cfg: Config) -> list[str]:
    """Return a list of problems with resume/cover letter files."""
    problems = []
    if cfg.master_resume and not cfg.master_resume.is_file():
        problems.append(f"master_resume not found: {cfg.master_resume}")
    for p in cfg.profiles:
        if p.resume and not p.resume.is_file():
            problems.append(f"[{p.name}] resume not found: {p.resume}")
        if p.cover_letter and not p.cover_letter.is_file():
            problems.append(f"[{p.name}] cover letter not found: {p.cover_letter}")
    return problems


def _check_answers(items: list, where: str) -> list[dict]:
    import re

    out = []
    for j, it in enumerate(items):
        if not isinstance(it, dict) or "match" not in it or "answer" not in it:
            raise ConfigError(f"{where}[{j}] needs 'match' and 'answer'")
        try:
            re.compile(it["match"])
        except re.error as e:
            raise ConfigError(f"{where}[{j}] bad regex {it['match']!r}: {e}")
        out.append(it)
    return out
