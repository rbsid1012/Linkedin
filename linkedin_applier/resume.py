"""Build a tailored one-page LaTeX resume per job from a master resume you wrote.

Hard rule: this only SELECTS and ORDERS what is in your master file. It never
writes or rewords a claim, so every line on a generated resume is something you
wrote and can defend in an interview.

Scoring for each bullet = sum(profile emphasis weight of its tags)
                        + 2 * (JD mentions of each of its tags, capped at 3 per tag)
"""
from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .config import Profile


class ResumeError(RuntimeError):
    pass


def load_master(path: str | Path) -> dict[str, Any]:
    data = yaml.safe_load(Path(path).read_text()) or {}
    for key in ("contact", "education", "experience"):
        if not data.get(key):
            raise ResumeError(f"master resume is missing '{key}'")
    for k in ("name", "email"):
        if not data["contact"].get(k):
            raise ResumeError(f"master resume contact is missing '{k}'")
    return data


# ---------------------------------------------------------------- selection
def _mentions(text: str, term: str) -> int:
    return len(re.findall(rf"(?<![\w+#]){re.escape(term.lower())}(?![\w])", text.lower()))


def _score(tags: list[str], emphasis: dict[str, float], jd: str) -> float:
    s = 0.0
    for t in tags:
        s += emphasis.get(t.lower(), 0.0)
        s += 2 * min(_mentions(jd, t), 3)
    return s


@dataclass
class Selection:
    experience: list[dict]
    projects: list[dict]
    skills: dict[str, list[str]]
    summary: str


def select(master: dict, profile: Profile, jd: str, bullets_per_role: int, max_projects: int = 2) -> Selection:
    emph = {k.lower(): float(v) for k, v in profile.emphasis.items()}

    def top(bullets: list[dict], n: int) -> list[dict]:
        ranked = sorted(enumerate(bullets), key=lambda ib: (-_score(ib[1].get("tags", []), emph, jd), ib[0]))
        return [b for _, b in ranked[:n]]

    exp = []
    for i, role in enumerate(master["experience"]):
        n = bullets_per_role if i < 3 else max(1, bullets_per_role - 1)  # older roles get less room
        exp.append({**role, "bullets": top(role.get("bullets", []), n)})

    projs = sorted(
        master.get("projects") or [],
        key=lambda p: -sum(_score(b.get("tags", []), emph, jd) for b in p.get("bullets", [])),
    )[:max_projects]
    projs = [{**p, "bullets": top(p.get("bullets", []), 2)} for p in projs]

    skills = {}
    for group, items in (master.get("skills") or {}).items():
        skills[group] = sorted(items, key=lambda s: (-_mentions(jd, s), items.index(s)))

    summaries = master.get("summaries") or {}
    summary = summaries.get("default", "")
    best = -1.0
    for tag, text in summaries.items():
        if tag == "default":
            continue
        sc = emph.get(tag.lower(), 0.0) + 2 * min(_mentions(jd, tag), 3)
        if sc > best and sc > 0:
            best, summary = sc, text
    return Selection(exp, projs, skills, summary)


# ---------------------------------------------------------------- LaTeX
_ESC = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_",
        "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
_UNI = {"→": "->", "←": "<-", "•": "-", "“": '"', "”": '"', "’": "'", "‘": "'", "…": "...", " ": " ",
        "–": "--", "—": "---"}


def esc(s: Any) -> str:
    s = str(s)
    for k, v in _UNI.items():
        s = s.replace(k, v)
    return "".join(_ESC.get(ch, ch) for ch in s)


PREAMBLE = r"""\documentclass[10pt,letterpaper]{article}
\usepackage[T1]{fontenc}
\usepackage[utf8]{inputenc}
\usepackage[margin=0.6in]{geometry}
\usepackage{enumitem}
\usepackage[hidelinks]{hyperref}
\usepackage{titlesec}
\pagestyle{empty}
\setlength{\parindent}{0pt}
\titleformat{\section}{\large\bfseries\scshape}{}{0pt}{}[\vspace{-6pt}\rule{\linewidth}{0.4pt}]
\titlespacing*{\section}{0pt}{8pt}{4pt}
\setlist[itemize]{leftmargin=1.2em,itemsep=1pt,topsep=2pt,parsep=0pt}
\newcommand{\entry}[4]{\textbf{#1}\hfill #2\\ \textit{#3}\hfill \textit{#4}\par}
"""


def render_tex(master: dict, sel: Selection, profile: Profile) -> str:
    c = master["contact"]
    parts = [esc(c[k]) for k in ("email", "phone", "location") if c.get(k)]
    links = [rf"\href{{{l['url']}}}{{{esc(l['label'])}}}" for l in c.get("links", [])]
    L = [PREAMBLE, r"\begin{document}", rf"\begin{{center}}{{\LARGE\bfseries {esc(c['name'])}}}\\[2pt]"]
    if profile.headline or master.get("headline"):
        L.append(rf"{esc(profile.headline or master['headline'])}\\[2pt]")
    L.append(" \\textbar{} ".join(parts + links) + r"\end{center}")
    if sel.summary:
        L += [r"\section*{Summary}", esc(sel.summary)]

    L.append(r"\section*{Experience}")
    for r in sel.experience:
        L.append(rf"\entry{{{esc(r['company'])}}}{{{esc(r.get('dates', ''))}}}{{{esc(r['title'])}}}{{{esc(r.get('location', ''))}}}")
        if r["bullets"]:
            L.append(r"\begin{itemize}")
            L += [rf"\item {esc(b['text'])}" for b in r["bullets"]]
            L.append(r"\end{itemize}")
    if sel.projects:
        L.append(r"\section*{Projects}")
        for p in sel.projects:
            L.append(rf"\textbf{{{esc(p['name'])}}}\hfill {esc(p.get('dates', ''))}\par")
            L.append(r"\begin{itemize}")
            L += [rf"\item {esc(b['text'])}" for b in p["bullets"]]
            L.append(r"\end{itemize}")
    L.append(r"\section*{Education}")
    for e in master["education"]:
        L.append(rf"\entry{{{esc(e['school'])}}}{{{esc(e.get('dates', ''))}}}{{{esc(e['degree'])}}}{{{esc(e.get('location', ''))}}}")
        if e.get("details"):
            L.append(esc("; ".join(e["details"])) + r"\par")
    if sel.skills:
        L.append(r"\section*{Skills}")
        for g, items in sel.skills.items():
            L.append(rf"\textbf{{{esc(g)}:}} {esc(', '.join(items))}\par")
    L.append(r"\end{document}")
    return "\n".join(L) + "\n"


def overleaf_html(tex: str, name: str) -> str:
    """A local page that POSTs the source to Overleaf's 'open in Overleaf' endpoint,
    which creates a new project in whichever Overleaf account you are signed in to."""
    return f"""<!doctype html><meta charset="utf-8"><title>Open in Overleaf</title>
<body style="font-family:sans-serif;margin:2rem">
<form action="https://www.overleaf.com/docs" method="post" target="_blank">
<input type="hidden" name="snip" value="{html.escape(tex, quote=True)}">
<input type="hidden" name="snip_name" value="{html.escape(name, quote=True)}">
<input type="hidden" name="engine" value="pdflatex">
<p>Sends this resume's LaTeX source to overleaf.com and opens it as a new project.</p>
<button type="submit">Open in Overleaf</button></form></body>"""


# ---------------------------------------------------------------- build
def have_latex() -> bool:
    return shutil.which("pdflatex") is not None


def compile_pdf(tex_path: Path) -> int:
    """Compile; return page count. Raises ResumeError on failure."""
    if not have_latex():
        raise ResumeError("pdflatex not found (install TeX Live / MacTeX / MiKTeX)")
    proc = subprocess.run(
        ["pdflatex", "-interaction=nonstopmode", "-halt-on-error", tex_path.name],
        cwd=tex_path.parent, capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        tail = "\n".join(proc.stdout.splitlines()[-15:])
        raise ResumeError(f"pdflatex failed:\n{tail}")
    m = re.search(r"\((\d+) pages?", proc.stdout)
    return int(m.group(1)) if m else 1


@dataclass
class Built:
    tex: Path
    pdf: Path | None
    overleaf: Path
    pages: int
    bullets_per_role: int
    error: str = ""


def build_resume(master: dict, profile: Profile, job_title: str, jd: str, out_dir: Path,
                 want_pdf: bool = True, start_bullets: int = 4) -> Built:
    out_dir.mkdir(parents=True, exist_ok=True)
    tex_path = out_dir / "resume.tex"
    jd_full = f"{job_title}\n{jd}"
    pages, pdf, err = 0, None, ""
    n = start_bullets
    while True:
        sel = select(master, profile, jd_full, n)
        tex = render_tex(master, sel, profile)
        tex_path.write_text(tex)
        if not (want_pdf and have_latex()):
            if want_pdf:
                err = "pdflatex not installed; no PDF produced"
            break
        try:
            pages = compile_pdf(tex_path)
        except ResumeError as e:
            err = str(e)
            break
        pdf = tex_path.with_suffix(".pdf")
        if pages <= 1 or n <= 1:  # shrink until it fits on one page
            break
        n -= 1
    for junk in ("aux", "log", "out"):
        tex_path.with_suffix(f".{junk}").unlink(missing_ok=True)
    ov = out_dir / "overleaf.html"
    ov.write_text(overleaf_html(tex_path.read_text(), f"{master['contact']['name']} - {profile.name}"))
    (out_dir / "meta.json").write_text(json.dumps({
        "profile": profile.name, "job_title": job_title, "bullets_per_role": n, "pages": pages,
        "chosen": [[b["text"] for b in r["bullets"]] for r in sel.experience]}, indent=2))
    if pdf and pages > 1:
        err = f"still {pages} pages at 1 bullet/role"
    return Built(tex_path, pdf, ov, pages, n, err)
