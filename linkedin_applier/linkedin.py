"""Drives LinkedIn's Easy Apply flow with Playwright.

LinkedIn's markup changes often. Locators here prefer roles/visible text over
class names, and all form handling goes through forms.EXTRACT_JS, so a markup
change should break one small function, not everything.
"""
from __future__ import annotations

import logging
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Locator, Page, TimeoutError as PWTimeout

from .config import Config, Profile
from .filters import evaluate
from .resume import build_resume, load_master
from .forms import EXTRACT_JS, Answerer, FormField, norm, pick_option
from .search import search_url
from .tracker import Tracker

log = logging.getLogger("applier")
MAX_STEPS = 12


class Blocked(RuntimeError):
    """LinkedIn is asking for a captcha/verification. Stop everything; don't try to get around it."""


@dataclass
class JobInfo:
    job_id: str
    title: str
    company: str
    url: str
    description: str


@dataclass
class Result:
    status: str  # applied | rehearsed | needs_manual | failed
    detail: str = ""


# --------------------------------------------------------------------------- session
def check_blocked(page: Page) -> None:
    if re.search(r"/checkpoint/|/uas/|authwall|captcha", page.url):
        raise Blocked(f"LinkedIn challenge page: {page.url}")


def ensure_logged_in(page: Page, wait_s: int = 300) -> None:
    page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
    deadline = time.time() + wait_s
    while time.time() < deadline:
        if "/feed" in page.url and page.locator("input[role=combobox], #global-nav").count():
            return
        if re.search(r"/login|/authwall|/checkpoint|/uas/", page.url):
            log.info("Not logged in. Sign in in the browser window (waiting up to %ss)...", wait_s)
        page.wait_for_timeout(2000)
    raise Blocked("Login not completed in time. Run `python -m linkedin_applier login` first.")


def pause(cfg: Config, lo: float | None = None, hi: float | None = None) -> None:
    s = cfg.settings
    time.sleep(random.uniform(lo if lo is not None else s.min_delay, hi if hi is not None else s.max_delay))


# --------------------------------------------------------------------------- search
def collect_job_ids(page: Page) -> list[str]:
    """Scroll the results list so lazy cards render, then return job ids in order."""
    for _ in range(6):
        page.evaluate(
            """() => { const c = document.querySelectorAll('li[data-occludable-job-id], [data-job-id]');
                      if (c.length) c[c.length-1].scrollIntoView({block:'end'}); }"""
        )
        page.wait_for_timeout(700)
    ids = page.evaluate(
        """() => Array.from(document.querySelectorAll('[data-occludable-job-id], [data-job-id]'))
                 .map(e => e.getAttribute('data-occludable-job-id') || e.getAttribute('data-job-id'))"""
    )
    seen, out = set(), []
    for i in ids:
        if i and i not in seen:
            seen.add(i)
            out.append(i)
    return out


def read_job(page: Page, job_id: str) -> JobInfo | None:
    url = f"https://www.linkedin.com/jobs/view/{job_id}/"
    page.goto(url, wait_until="domcontentloaded")
    check_blocked(page)
    try:
        page.locator("h1").first.wait_for(timeout=15000)
    except PWTimeout:
        return None
    data = page.evaluate(
        """() => {
          const t = s => { const e = document.querySelector(s); return e ? e.innerText.trim() : ''; };
          const title = t('h1');
          const company = t('.job-details-jobs-unified-top-card__company-name') ||
                          (document.querySelector('a[href*="/company/"]') || {innerText: ''}).innerText.trim();
          const desc = t('#job-details') || t('.jobs-description__content') || t('main');
          return {title, company, desc};
        }"""
    )
    return JobInfo(job_id, data["title"], data["company"], url, data["desc"])


def easy_apply_button(page: Page) -> Locator | None:
    for role in ("button", "link"):
        loc = page.get_by_role(role, name=re.compile(r"^Easy Apply", re.I))
        if loc.count() and loc.first.is_visible():
            return loc.first
    return None


def already_applied(page: Page) -> bool:
    return page.get_by_text(re.compile(r"^(Applied\b|You applied)", re.I)).count() > 0


# --------------------------------------------------------------------------- apply
def _dialog(page: Page) -> Locator:
    return page.locator("[role=dialog]").last


def _button(dialog: Locator, pattern: str) -> Locator | None:
    loc = dialog.get_by_role("button", name=re.compile(pattern, re.I))
    for i in range(loc.count()):
        b = loc.nth(i)
        if b.is_visible() and b.is_enabled():
            return b
    return None


def discard(page: Page) -> None:
    try:
        d = _dialog(page)
        close = d.get_by_role("button", name=re.compile(r"dismiss|close", re.I))
        if close.count():
            close.first.click()
            page.wait_for_timeout(500)
        confirm = page.get_by_role("button", name=re.compile(r"^discard", re.I))
        if confirm.count():
            confirm.first.click()
            page.wait_for_timeout(500)
    except Exception as e:  # best effort cleanup
        log.debug("discard failed: %s", e)


def fill_fields(page: Page, dialog: Locator, answerer: Answerer, ask, tracker: Tracker, job_id: str) -> str | None:
    """Fill every empty field in the current step. Returns the label of a required field
    we couldn't answer, or None if the step is complete."""
    raw = dialog.evaluate(EXTRACT_JS)
    for r in raw:
        f = FormField(**r)
        if f.filled:
            continue
        ans = answerer.answer(f)
        if ans is None and f.required:
            ans = ask(f) if ask else None
        if ans is None:
            if f.required:
                tracker.log_unanswered(norm(f.label), job_id)
                return f.label or "(unlabelled field)"
            continue  # optional and unknown: leave blank
        _apply_answer(dialog, f, ans)
    return None


def _apply_answer(dialog: Locator, f: FormField, ans) -> None:
    el = dialog.locator(f'[data-applier-id="{f.id}"]')
    if f.kind in ("text", "textarea", "number"):
        text = "Yes" if ans is True else "No" if ans is False else str(ans)
        el.fill(text)
        # LinkedIn's location/typeahead fields need a pick from a dropdown.
        if el.get_attribute("role") == "combobox":
            opt = dialog.page.get_by_role("option").first
            try:
                opt.wait_for(timeout=2000)
                opt.click()
            except PWTimeout:
                pass
    elif f.kind == "select":
        choice = pick_option(ans, f.options)
        if choice is None:
            raise ValueError(f"no option matches {ans!r} among {f.options}")
        el.select_option(label=choice)
    elif f.kind == "radio":
        choice = pick_option(ans, f.options)
        if choice is None:
            raise ValueError(f"no option matches {ans!r} among {f.options}")
        # click the visible <label> for that option; the input itself is often visually hidden
        dialog.get_by_text(choice, exact=True).first.click()
    elif f.kind == "checkbox":
        want = bool(ans) if isinstance(ans, bool) else str(ans).lower() in ("true", "yes")
        if el.is_checked() != want:
            el.set_checked(want, force=True)


def upload_documents(dialog: Locator, resume: Path, cover_letter: Path | None) -> None:
    inputs = dialog.locator("input[type=file]")
    for i in range(inputs.count()):
        inp = inputs.nth(i)
        ctx = (inp.evaluate("e => (e.closest('div,fieldset,section')||document.body).innerText.slice(0,300)") or "").lower()
        if "cover" in ctx:
            if cover_letter:
                inp.set_input_files(str(cover_letter))
            continue
        inp.set_input_files(str(resume))


def apply_easy(page: Page, job: JobInfo, profile: Profile, resume: Path, cfg: Config, tracker: Tracker, answerer: Answerer,
               mode: str, ask=None) -> Result:
    btn = easy_apply_button(page)
    if not btn:
        return Result("skipped", "no Easy Apply button")
    btn.click()
    try:
        _dialog(page).wait_for(timeout=10000)
    except PWTimeout:
        return Result("failed", "Easy Apply dialog did not open")

    last_sig, stuck = None, 0
    try:
        for _ in range(MAX_STEPS):
            d = _dialog(page)
            upload_documents(d, resume, profile.cover_letter)
            try:
                missing = fill_fields(page, d, answerer, ask, tracker, job.job_id)
            except ValueError as e:
                discard(page)
                return Result("needs_manual", str(e))
            if missing:
                discard(page)
                return Result("needs_manual", f"unanswered required question: {missing}")

            submit = _button(d, r"^submit application")
            if submit:
                if mode != "apply":
                    discard(page)
                    return Result("rehearsed", "reached submit step; discarded (rehearse mode)")
                # untick 'follow company' is handled by the checkbox rule; submit now
                submit.click()
                page.wait_for_timeout(2500)
                if page.get_by_text(re.compile(r"application (was )?sent|applied", re.I)).count():
                    done = page.get_by_role("button", name=re.compile(r"^(done|dismiss)", re.I))
                    if done.count():
                        done.first.click()
                    return Result("applied")
                err = _inline_error(page)
                discard(page)
                return Result("failed", f"submit not confirmed{': ' + err if err else ''}")

            nxt = _button(d, r"^review") or _button(d, r"^(next|continue)")
            if not nxt:
                discard(page)
                return Result("failed", "no Next/Review/Submit button found")
            nxt.click()
            page.wait_for_timeout(1200)
            err = _inline_error(page)
            sig = (err, d.inner_text()[:200] if d.count() else "")
            stuck = stuck + 1 if sig == last_sig else 0
            last_sig = sig
            if err and stuck >= 1:
                discard(page)
                return Result("needs_manual", f"form error: {err}")
        discard(page)
        return Result("failed", "too many steps")
    except Exception as e:
        discard(page)
        log.exception("apply failed"); return Result("failed", f"{type(e).__name__}: {e}")


def _inline_error(page: Page) -> str:
    loc = page.locator("[role=dialog] [role=alert], [role=dialog] .artdeco-inline-feedback--error")
    for i in range(loc.count()):
        t = loc.nth(i).inner_text().strip()
        if t:
            return t[:160]
    return ""


# --------------------------------------------------------------------------- orchestration
def run(cfg: Config, tracker: Tracker, mode: str, only_profile: str | None = None, interactive: bool = False) -> None:
    from playwright.sync_api import sync_playwright

    answerers = {pr.name: Answerer(cfg.candidate, learned=tracker.learned, extra=pr.answers) for pr in cfg.profiles}
    master = load_master(cfg.master_resume) if cfg.master_resume else None

    def ask(f: FormField):
        if not interactive or cfg.settings.unknown_question_policy != "ask":
            return None
        print(f"\nUnknown required question: {f.label!r} ({f.kind}) options={f.options or '-'}")
        a = input("Answer (blank to skip this job): ").strip()
        if not a:
            return None
        tracker.learn(norm(f.label), a)
        return a

    s = cfg.settings
    applied_this_run = 0
    failures = 0
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(s.user_data_dir), headless=s.headless, viewport={"width": 1280, "height": 900}
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            ensure_logged_in(page)
            for profile in cfg.profiles:
                if only_profile and profile.name != only_profile:
                    continue
                for kw in profile.keywords:
                    for pg in range(s.pages_per_keyword):
                        page.goto(search_url(profile, kw, pg), wait_until="domcontentloaded")
                        check_blocked(page)
                        try:
                            page.wait_for_selector("[data-occludable-job-id], [data-job-id]", timeout=15000)
                        except PWTimeout:
                            log.info("[%s] no results for %r page %d", profile.name, kw, pg)
                            break
                        for job_id in collect_job_ids(page):
                            if tracker.seen(job_id):
                                continue
                            if mode == "apply" and (
                                applied_this_run >= s.max_per_run or tracker.applied_today() >= s.daily_limit
                            ):
                                log.info("Limit reached (run %d/%d, 24h %d/%d). Stopping.",
                                         applied_this_run, s.max_per_run, tracker.applied_today(), s.daily_limit)
                                return
                            job = read_job(page, job_id)
                            if not job:
                                continue
                            rec = dict(title=job.title, company=job.company, url=job.url, profile=profile.name)
                            if already_applied(page):
                                tracker.record(job_id, "skipped", detail="already applied", **rec)
                                continue
                            ok, why = evaluate(profile, job.title, job.company, job.description)
                            if not ok:
                                tracker.record(job_id, "skipped", detail=why, **rec)
                                log.info("skip  %s @ %s: %s", job.title, job.company, why)
                                continue
                            if not easy_apply_button(page):
                                tracker.record(job_id, "skipped", detail="no Easy Apply", **rec)
                                continue

                            # Steps 2+3: tailored LaTeX + PDF + Overleaf launcher for this exact job.
                            resume, note = profile.resume, ""
                            if master:
                                built = build_resume(master, profile, job.title, job.description,
                                                     s.output_dir / profile.name / job.job_id,
                                                     want_pdf=True)
                                resume, note = built.pdf, f" tex={built.tex} overleaf={built.overleaf}"
                                if built.error:
                                    log.warning("resume for %s: %s", job.job_id, built.error.splitlines()[0])
                            rec["resume"] = resume or ""
                            if mode == "scan":
                                tracker.record(job_id, "matched", detail=f"resume={resume}{note}", **rec)
                                log.info("match %s @ %s -> %s", job.title, job.company, resume)
                                continue
                            if not resume or not Path(resume).is_file():
                                tracker.record(job_id, "failed", detail=f"no resume PDF{note}", **rec)
                                failures += 1
                                continue
                            answerer = answerers[profile.name]
                            answerer.ctx = {"company": job.company, "title": job.title}
                            res = apply_easy(page, job, profile, Path(resume), cfg, tracker, answerer, mode, ask)
                            tracker.record(job_id, res.status, detail=res.detail + note, **rec)
                            log.info("%-12s %s @ %s %s", res.status, job.title, job.company, res.detail)
                            if res.status == "applied":
                                applied_this_run += 1
                            failures = failures + 1 if res.status == "failed" else 0
                            if failures >= s.max_consecutive_failures:
                                log.error("%d consecutive failures: selectors probably broke. Stopping.", failures)
                                return
                            pause(cfg)
        except Blocked as e:
            log.error("STOPPED: %s\nResolve it manually in a normal browser session; don't just re-run.", e)
        finally:
            ctx.close()
