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
    resume: Path
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


@dataclass
class Config:
    settings: Settings
    candidate: dict[str, Any]
    profiles: list[Profile]
    base_dir: Path


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
    if settings.unknown_question_policy not in ("skip", "ask"):
        raise ConfigError("settings.unknown_question_policy must be 'skip' or 'ask'")
    if settings.min_delay > settings.max_delay:
        raise ConfigError("settings.min_delay must be <= max_delay")

    profiles = []
    for i, p in enumerate(raw.get("profiles") or []):
        for req in ("name", "resume", "keywords"):
            if not p.get(req):
                raise ConfigError(f"profiles[{i}] is missing '{req}'")
        wt = _list(p.get("work_types"))
        bad = set(wt) - {"onsite", "remote", "hybrid"}
        if bad:
            raise ConfigError(f"profiles[{i}].work_types invalid: {sorted(bad)}")
        profiles.append(
            Profile(
                name=p["name"],
                resume=_resolve(base, p["resume"]),
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
            )
        )
    if not profiles:
        raise ConfigError("Define at least one entry under 'profiles'")
    names = [p.name for p in profiles]
    if len(set(names)) != len(names):
        raise ConfigError("Profile names must be unique")

    return Config(settings=settings, candidate=raw.get("candidate") or {}, profiles=profiles, base_dir=base)


def _resolve(base: Path, p: str | Path) -> Path:
    p = Path(p).expanduser()
    return p if p.is_absolute() else base / p


def check_resumes(cfg: Config) -> list[str]:
    """Return a list of problems with resume/cover letter files."""
    problems = []
    for p in cfg.profiles:
        if not p.resume.is_file():
            problems.append(f"[{p.name}] resume not found: {p.resume}")
        if p.cover_letter and not p.cover_letter.is_file():
            problems.append(f"[{p.name}] cover letter not found: {p.cover_letter}")
    return problems
