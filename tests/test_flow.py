"""Run the real Easy Apply stepper against a local fixture that mimics the modal's structure."""
import os
from pathlib import Path

import pytest

pw = pytest.importorskip("playwright.sync_api")

from linkedin_applier.config import Config, Profile, Settings
from linkedin_applier.forms import Answerer
from linkedin_applier.linkedin import JobInfo, apply_easy
from linkedin_applier.tracker import Tracker

FIXTURE = Path(__file__).parent / "fixtures" / "easy_apply.html"
CHROMIUM = os.environ.get("APPLIER_TEST_CHROMIUM", "/opt/pw-browsers/chromium")


@pytest.fixture
def page():
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch(executable_path=CHROMIUM if Path(CHROMIUM).exists() else None,
                                  args=["--no-sandbox"])
        except Exception as e:
            pytest.skip(f"no chromium: {e}")
        pg = b.new_page()
        pg.goto(FIXTURE.as_uri())
        yield pg
        b.close()


def setup(tmp_path, cand):
    resume = tmp_path / "r.pdf"
    resume.write_bytes(b"%PDF-1.4 test")
    prof = Profile(name="p", resume=resume, keywords=["k"])
    cfg = Config(settings=Settings(), candidate=cand, profiles=[prof], base_dir=tmp_path)
    return cfg, prof, Tracker(tmp_path / "t.db")


CAND = {
    "phone": "5551234567", "requires_sponsorship": False, "authorized_to_work": True,
    "years_experience": {"default": 2, "python": 6},
}
JOB = JobInfo("1", "Backend Engineer", "Acme", "u", "")


def test_apply_fills_and_submits(page, tmp_path):
    cfg, prof, tr = setup(tmp_path, CAND)
    res = apply_easy(page, JOB, prof, prof.resume, cfg, tr, Answerer(CAND), "apply")
    assert res.status == "applied", res.detail
    sub = page.evaluate("window.__submitted")
    assert sub == {"phone": "5551234567", "years": "6", "sponsor": "n",
                   "gender": "Decline to self-identify", "follow": False, "file": 1}


def test_rehearse_never_submits(page, tmp_path):
    cfg, prof, tr = setup(tmp_path, CAND)
    res = apply_easy(page, JOB, prof, prof.resume, cfg, tr, Answerer(CAND), "rehearse")
    assert res.status == "rehearsed"
    assert page.evaluate("window.__submitted") is None
    assert not page.locator("#dlg").is_visible()  # discarded


def test_unknown_required_question_aborts_and_is_logged(page, tmp_path):
    cand = {k: v for k, v in CAND.items() if k != "phone"}
    cfg, prof, tr = setup(tmp_path, cand)
    res = apply_easy(page, JOB, prof, prof.resume, cfg, tr, Answerer(cand), "apply")
    assert res.status == "needs_manual" and "phone" in res.detail.lower()
    assert page.evaluate("window.__submitted") is None
    assert tr.unanswered_summary()[0][0].startswith("mobile phone")
