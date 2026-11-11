import textwrap

import pytest

from linkedin_applier.config import ConfigError, check_resumes, load_config
from linkedin_applier.filters import evaluate
from linkedin_applier.forms import Answerer, FormField, pick_option
from linkedin_applier.search import search_url
from linkedin_applier.tracker import Tracker

CAND = {
    "first_name": "Jane", "last_name": "Doe", "email": "j@x.com", "phone": "555",
    "authorized_to_work": True, "requires_sponsorship": False, "salary_expectation": 100000,
    "years_experience": {"default": 3, "python": 5, "java": 1, "c": 2},
    "answers": [{"match": "how did you hear", "answer": "LinkedIn"}],
}


def f(label, kind="text", options=(), required=True):
    return FormField(id="x", label=label, kind=kind, options=list(options), required=required)


@pytest.mark.parametrize("q,expected", [
    ("How many years of experience do you have with Python?", "5"),
    ("How many years of work experience do you have using Java?", "1"),
    ("How many years of experience do you have with Rust?", "3"),          # falls back to default
    ("Years of experience with Scala (Required)", "3"),
    ("How many years of experience do you have with C++?", "2"),           # 'c' only as a whole token
    ("Will you now or in the future require sponsorship?", False),
    ("Are you legally authorized to work in the United States?", True),
    ("Are you authorized to work in the US without sponsorship?", True),
    ("What are your salary expectations?", "100000"),
    ("Email address", "j@x.com"),
    ("How did you hear about this job?", "LinkedIn"),
    ("Do you have a pet dragon?", None),
])
def test_answers(q, expected):
    assert Answerer(CAND).answer(f(q)) == expected


def test_javascript_does_not_match_java():
    cand = {**CAND, "years_experience": {"default": 3, "java": 9}}
    assert Answerer(cand).answer(f("How many years of experience with JavaScript?")) == "3"


def test_sponsorship_needed_flips_combined_answer():
    cand = {**CAND, "requires_sponsorship": True}
    assert Answerer(cand).answer(f("Authorized to work without sponsorship?")) is False


def test_demographics_decline_and_checkbox_defaults():
    a = Answerer(CAND)
    assert a.answer(f("Gender", "select")) == "decline"
    assert a.answer(f("Follow Acme to stay up to date", "checkbox")) is False
    assert a.answer(f("I agree to the terms", "checkbox")) is None  # never auto-consent


def test_learned_answers_are_used_after_explicit():
    a = Answerer(CAND, learned=lambda q: "42" if "lucky" in q else None)
    assert a.answer(f("Lucky number?")) == "42"


def test_pick_option():
    assert pick_option(True, ["Select an option", "Yes", "No"]) == "Yes"
    assert pick_option(False, ["Yes", "No"]) == "No"
    assert pick_option("decline", ["Male", "Female", "Decline to self-identify"]) == "Decline to self-identify"
    assert pick_option("decline", ["Male", "Female"]) is None
    assert pick_option("2 weeks", ["1 week", "2 weeks or less", "1 month"]) == "2 weeks or less"
    assert pick_option("Nope", ["Yes", "No"]) is None


def test_filters(tmp_path):
    cfgp = tmp_path / "c.yaml"
    cfgp.write_text(textwrap.dedent("""
        profiles:
          - name: a
            resume: r.pdf
            keywords: [x]
            title_include: [backend]
            title_exclude: [staff]
            company_exclude: [Revature]
            description_exclude: ["security clearance"]
    """))
    p = load_config(cfgp).profiles[0]
    assert evaluate(p, "Backend Engineer", "Acme")[0]
    assert not evaluate(p, "Frontend Engineer", "Acme")[0]
    assert not evaluate(p, "Staff Backend Engineer", "Acme")[0]
    assert not evaluate(p, "Backend Engineer", "revature llc")[0]
    assert not evaluate(p, "Backend Engineer", "Acme", "Requires Security Clearance")[0]


def test_config_validation(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("profiles: []")
    with pytest.raises(ConfigError):
        load_config(p)
    p.write_text("profiles:\n  - {name: a, resume: r.pdf, keywords: [k], work_types: [mars]}")
    with pytest.raises(ConfigError):
        load_config(p)
    p.write_text("profiles:\n  - {name: a, resume: r.pdf, keywords: [k]}")
    cfg = load_config(p)
    assert check_resumes(cfg)  # r.pdf doesn't exist
    (tmp_path / "r.pdf").write_bytes(b"%PDF")
    assert not check_resumes(cfg)


def test_example_config_loads():
    cfg = load_config("config.example.yaml")
    assert [p.name for p in cfg.profiles] == ["tech", "data", "it", "consulting", "finance", "vc", "innovation"]
    assert cfg.master_resume.name == "master_resume.yaml"


def test_search_url():
    cfg = load_config("config.example.yaml")
    url = search_url(cfg.profiles[0], "Backend Engineer", page=1)
    for part in ("f_AL=true", "f_E=2%2C3", "f_TPR=r86400", "start=25", "keywords=Backend+Engineer"):
        assert part in url


def test_tracker(tmp_path):
    t = Tracker(tmp_path / "t.db")
    assert not t.seen("1")
    t.record("1", "matched")
    assert not t.seen("1")          # scan results must not block a real apply
    t.record("1", "applied")
    assert t.seen("1") and t.applied_today() == 1
    t.log_unanswered("q", "1"); t.log_unanswered("q", "2")
    assert t.unanswered_summary() == [("q", 2)]
    t.learn("q", "a")
    assert t.learned("q") == "a"
