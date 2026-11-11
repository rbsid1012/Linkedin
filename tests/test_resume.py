import re
from pathlib import Path

import pytest
import yaml

from linkedin_applier.config import Profile
from linkedin_applier.resume import build_resume, compile_pdf, esc, have_latex, load_master, select

MASTER = load_master("master_resume.example.yaml")


def prof(**kw):
    return Profile(name=kw.pop("name", "p"), resume=None, keywords=["k"], **kw)


def chosen(sel):
    return [b["text"] for r in sel.experience for b in r["bullets"]]


def test_emphasis_and_jd_drive_selection():
    fin = select(MASTER, prof(emphasis={"finance": 3, "modeling": 2}), "", bullets_per_role=1)
    assert "revenue model" in chosen(fin)[0]
    data = select(MASTER, prof(emphasis={}), "We need Snowflake and ETL experience", bullets_per_role=1)
    assert "ETL pipeline" in chosen(data)[0]


def test_never_invents_text():
    allowed = {b["text"] for r in MASTER["experience"] for b in r["bullets"]} | \
              {b["text"] for p in MASTER["projects"] for b in p["bullets"]}
    for jd in ("", "kubernetes aws", "venture capital startups research"):
        sel = select(MASTER, prof(emphasis={"vc": 3}), jd, 4)
        assert set(chosen(sel)) | {b["text"] for p in sel.projects for b in p["bullets"]} <= allowed


def test_summary_variant_chosen_by_tag():
    assert "financial modelling" in select(MASTER, prof(emphasis={"finance": 3}), "", 3).summary
    assert select(MASTER, prof(), "", 3).summary.startswith("Engineer with")


def test_skills_reordered_by_jd_but_not_filtered():
    sel = select(MASTER, prof(), "Java Java experience", 3)
    assert sel.skills["Languages"][0] == "Java"
    assert sorted(sel.skills["Languages"]) == sorted(MASTER["skills"]["Languages"])


def test_latex_escaping():
    assert esc("R&D 50% $5k C# a_b") == r"R\&D 50\% \$5k C\# a\_b"
    assert esc("a → b") == "a -> b"


def test_missing_section_rejected(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text(yaml.safe_dump({"contact": {"name": "x", "email": "y"}, "education": [1]}))
    with pytest.raises(Exception):
        load_master(p)


def test_build_writes_tex_and_overleaf(tmp_path):
    b = build_resume(MASTER, prof(emphasis={"finance": 3}), "Financial Analyst", "excel modeling", tmp_path, want_pdf=False)
    tex = b.tex.read_text()
    assert r"\begin{document}" in tex and "Jane Doe" in tex
    ov = b.overleaf.read_text()
    assert "overleaf.com/docs" in ov and 'name="snip"' in ov and "&lt;" not in tex  # tex is html-escaped inside value
    assert "\\documentclass" in ov.replace("&#x27;", "'") or "documentclass" in ov


@pytest.mark.skipif(not have_latex(), reason="pdflatex not installed")
def test_example_compiles_to_one_page(tmp_path):
    b = build_resume(MASTER, prof(emphasis={"finance": 3}, headline="Financial Analyst"), "Analyst", "excel", tmp_path)
    assert b.error == "" and b.pages == 1 and b.pdf and b.pdf.stat().st_size > 1000
    assert not (tmp_path / "resume.aux").exists()


@pytest.mark.skipif(not have_latex(), reason="pdflatex not installed")
def test_overlong_resume_shrinks_to_one_page(tmp_path):
    big = {**MASTER, "experience": [
        {**MASTER["experience"][0], "company": f"Co{i}", "bullets": [
            {"text": "Did a long thing " * 8 + str(j), "tags": []} for j in range(6)]} for i in range(5)]}
    b = build_resume(big, prof(), "x", "", tmp_path, start_bullets=6)
    assert b.pages == 1 and b.bullets_per_role < 6
