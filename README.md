# LinkedIn Easy Apply helper (one resume per role)

Searches LinkedIn for each **profile** (e.g. `backend`, `data`), filters jobs by title/company/description,
and applies via Easy Apply using **that profile's resume**. Everything is logged to a local SQLite file.

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
# put resumes/backend.pdf, resumes/data.pdf, ... in place
python -m linkedin_applier login        # sign in once, press Enter
```

## Use

```bash
python -m linkedin_applier run                    # scan: list matches, touch no forms
python -m linkedin_applier run --mode rehearse    # fill forms up to Submit, then discard
python -m linkedin_applier run --mode apply       # actually submit
python -m linkedin_applier run --mode apply --profile data
python -m linkedin_applier report                 # counts + questions that blocked applications
```

Suggested order: `scan` → `rehearse` on a few jobs → `report` → add missing answers → `apply`.

## How resume routing works

Each profile has its own `resume`, `keywords`, and filters. A job discovered under a profile is applied to with that
profile's resume; jobs are de-duplicated across profiles (first profile to see it wins), so keep
`title_include` tight to avoid a "data" job being claimed by "backend".

## Screening questions

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
