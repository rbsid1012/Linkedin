# LinkedIn Easy Apply helper (tailored resume per job)

Flow, per job:

1. **Find roles**: one search per sector profile (tech, data, IT, consulting, finance, VC, innovation), filtered by title/company/description.
2. **Generate LaTeX**: `out/<profile>/<job_id>/resume.tex`, built from your `master_resume.yaml`.
3. **Overleaf + PDF**: `overleaf.html` in the same folder (open it, click the button, get an Overleaf project), and `resume.pdf` compiled locally with `pdflatex`.
4. **Apply** via Easy Apply, uploading that job's PDF.
5. **Screening answers**: rule-based for now (see below); required questions it can't answer skip the job and are logged.

Everything is logged to a local SQLite file.

## Tailoring: what it does and does not do

You write the master resume once (`master_resume.example.yaml`): more bullets than fit, each tagged with keywords.
For each job it **selects and orders** your bullets by (profile `emphasis` weights + keywords in the job description),
reorders skills, picks the summary variant you wrote, and shrinks bullets-per-role until it is one page.
It **never writes or rewords** content, so every line is yours. Overleaf has no API to create projects silently;
`overleaf.html` uses its "open in Overleaf" form, which creates the project in the account you're signed in to.
Preview without LinkedIn: `python -m linkedin_applier preview --profile finance --title "Financial Analyst" --jd jd.txt`.

## Read this first

- **It violates LinkedIn's User Agreement** (automation of the site). Accounts get restricted, and the
  restriction lands on the account you actually want for job hunting. Defaults are deliberately slow
  and capped (20/day, 20–60 s between applications). Don't raise them.
- It runs on **your machine** with a visible browser. Cloud/datacenter IPs get challenged immediately.
- The Easy Apply selectors have **not been verified against live LinkedIn** (the dev environment can't reach a
  logged-in session). The form-stepping logic is tested against a local fixture only. Expect to adjust
  selectors in `linkedin.py` on first contact; run `rehearse` mode before `apply`.
- If LinkedIn shows a captcha/verification page the tool **stops**. It does not try to get around it.
- It never sees your password: you sign in yourself, the browser profile (`.browser-profile/`) keeps the session.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && playwright install chromium
cp config.example.yaml config.yaml      # edit candidate info + profiles
cp master_resume.example.yaml master_resume.yaml   # replace with your real, tagged resume content
# install pdflatex (TeX Live / MacTeX / MiKTeX): needed in rehearse/apply modes
python -m linkedin_applier login        # sign in once, press Enter
```

## Use

```bash
python -m linkedin_applier run                    # scan: list matches + build tex/pdf/overleaf, touch no forms
python -m linkedin_applier run --mode rehearse    # fill forms up to Submit, then discard
python -m linkedin_applier run --mode apply       # actually submit
python -m linkedin_applier run --mode apply --profile data
python -m linkedin_applier report                 # counts + questions that blocked applications
```

Suggested order: `scan` → `rehearse` on a few jobs → `report` → add missing answers → `apply`.

## Coverage and volume

- Many finance, VC and consulting postings are **not Easy Apply**; LinkedIn sends you to the company's own site. Those are
  skipped and logged ("no Easy Apply"). Expect much lower yield there than in tech/data.
- "All available jobs" is bounded by `daily_limit` / `max_per_run` in `config.yaml`. The defaults (20/day) are a deliberate
  account-safety choice; raising them is on you. Profiles are de-duplicated: the first profile to see a job wins.

## Screening questions

Put your own answers in `answers.yaml` (see `answers.example.yaml`; regex on the question, `{company}`/`{title}` filled per job) or under a profile's `answers:` so finance and tech jobs get different wording. `report` prints ready-to-paste stubs for every question that blocked a job.

Answer order: `candidate.answers` (regex → answer) > answers you typed with `--interactive` and
`unknown_question_policy: ask` > built-in rules (years of experience per skill, work authorization,
sponsorship, salary, notice period, contact fields, EEO questions → "decline"). Anything else that is
**required** makes the tool skip that job and log the question; `report` lists them so you can add answers.
It never ticks consent/agreement checkboxes on your behalf, and it unticks "follow company".
Check that your answers are true: they are submitted under your name.

## Tests

```bash
pytest -q
```
