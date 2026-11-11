"""Build LinkedIn job-search URLs from a profile."""
from __future__ import annotations

from urllib.parse import urlencode

from .config import Profile

_EXPERIENCE = {"internship": 1, "entry": 2, "associate": 3, "mid-senior": 4, "director": 5, "executive": 6}
_WORK_TYPE = {"onsite": 1, "remote": 2, "hybrid": 3}


def search_url(profile: Profile, keyword: str, page: int = 0) -> str:
    params: dict[str, str] = {
        "keywords": keyword,
        "f_AL": "true",  # Easy Apply only
        "sortBy": "DD",  # most recent
        "f_TPR": f"r{profile.posted_within_hours * 3600}",
    }
    if profile.location:
        params["location"] = profile.location
    if profile.experience_levels:
        params["f_E"] = ",".join(str(_EXPERIENCE[e]) for e in profile.experience_levels)
    if profile.work_types:
        params["f_WT"] = ",".join(str(_WORK_TYPE[w]) for w in profile.work_types)
    if page:
        params["start"] = str(page * 25)
    return "https://www.linkedin.com/jobs/search/?" + urlencode(params)
