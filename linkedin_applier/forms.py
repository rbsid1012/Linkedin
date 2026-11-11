"""Turn a form question into an answer. Pure logic, no browser, easy to test.

Rule order: your explicit `answers` > answers you taught it interactively >
built-in rules for common screening questions. Anything else returns None, and
the caller decides whether to skip the job or ask you.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

DECLINE_RE = re.compile(r"decline|prefer not|do not wish|don'?t wish|choose not|not to (say|disclose|answer)", re.I)
PLACEHOLDER_RE = re.compile(r"^\s*(select|choose|please select)\b.*$", re.I)


@dataclass
class FormField:
    id: str
    label: str
    kind: str  # text | number | textarea | select | radio | checkbox
    options: list[str] = field(default_factory=list)
    value: str = ""
    required: bool = False

    @property
    def filled(self) -> bool:
        if self.kind == "checkbox":
            return False  # always decide explicitly
        return bool(self.value.strip()) and not PLACEHOLDER_RE.match(self.value)


def norm(s: str) -> str:
    s = re.sub(r"\b(required|optional)\b", " ", s, flags=re.I)
    s = re.sub(r"[*:?]", " ", s)
    return re.sub(r"\s+", " ", s).strip().lower()


Answer = str | bool | None


class Answerer:
    def __init__(self, candidate: dict[str, Any], learned: Callable[[str], str | None] = lambda q: None):
        self.c = candidate
        self.learned = learned
        self.explicit: list[tuple[re.Pattern, Any]] = []
        for item in candidate.get("answers") or []:
            self.explicit.append((re.compile(item["match"], re.I), item["answer"]))

    # -- public ---------------------------------------------------------
    def answer(self, f: FormField) -> Answer:
        q = norm(f.label)
        if not q:
            return None
        for pat, ans in self.explicit:
            if pat.search(q):
                return ans
        taught = self.learned(q)
        if taught is not None:
            return taught
        if f.kind == "checkbox":
            return self._checkbox(q)
        return self._builtin(q, f)

    # -- rules ------------------------------------------------------------
    def _checkbox(self, q: str) -> Answer:
        if re.search(r"follow .*(company|us)|stay up to date|newsletter", q):
            return False
        return None  # consents etc.: do not tick on the user's behalf unless an explicit answer says so

    def _builtin(self, q: str, f: FormField) -> Answer:
        c = self.c

        # Demographic / EEO: decline by default.
        if re.search(r"gender|race|ethnic|veteran|disabilit|sexual orientation|pronoun", q):
            return "decline"

        # Years of experience.
        if re.search(r"\byears?\b.*\bexperience\b|\bexperience\b.*\byears?\b|how many years", q):
            return self._years(q)

        # Authorization + sponsorship (check combined form first).
        sponsor = re.search(r"sponsor", q)
        auth = re.search(r"authori[sz]ed|legally.*work|eligible to work|right to work|work permit", q)
        if sponsor and auth:
            return bool(c.get("authorized_to_work", False)) and not bool(c.get("requires_sponsorship", True))
        if sponsor:
            return bool(c.get("requires_sponsorship", True))
        if auth:
            return bool(c.get("authorized_to_work", False))

        if re.search(r"relocat", q):
            return bool(c.get("willing_to_relocate", False))
        if re.search(r"commut|on-?site|in[- ]office|hybrid", q) and re.search(r"comfortable|willing|able|ok", q):
            return bool(c.get("ok_with_onsite", False))
        if re.search(r"\bremote\b", q) and re.search(r"comfortable|willing|able|ok", q):
            return bool(c.get("ok_with_remote", True))

        if re.search(r"salary|compensation|\bctc\b|pay expectation", q):
            v = c.get("salary_expectation")
            return str(v) if v is not None else None
        if re.search(r"notice period|start date|when can you start|availab", q):
            v = c.get("notice_period")
            return str(v) if v else None

        # Contact basics.
        if re.search(r"first name", q):
            return c.get("first_name")
        if re.search(r"last name|surname|family name", q):
            return c.get("last_name")
        if re.fullmatch(r"(full )?name", q):
            return " ".join(x for x in (c.get("first_name"), c.get("last_name")) if x) or None
        if re.search(r"e-?mail", q):
            return c.get("email")
        if re.search(r"phone|mobile", q) and not re.search(r"country", q):
            return c.get("phone")
        if re.search(r"linkedin", q):
            return c.get("linkedin_url")
        if re.search(r"github", q):
            return c.get("github_url")
        if re.search(r"website|portfolio|personal site", q):
            return c.get("website")
        if re.fullmatch(r".*\b(city|location)\b.*", q) and f.kind in ("text", "textarea"):
            return c.get("city")
        return None

    def _years(self, q: str) -> Answer:
        table: dict[str, Any] = {str(k).lower(): v for k, v in (self.c.get("years_experience") or {}).items()}
        default = table.pop("default", None)
        best = None
        for skill, yrs in table.items():
            if re.search(rf"(?<![a-z0-9]){re.escape(skill)}(?![a-z0-9])", q):
                if best is None or len(skill) > len(best[0]):
                    best = (skill, yrs)
        val = best[1] if best else default
        return None if val is None else str(int(val))


def pick_option(answer: Answer, options: list[str]) -> str | None:
    """Map an answer onto the visible option labels of a select/radio. None if no safe match."""
    opts = [o for o in options if o.strip() and not PLACEHOLDER_RE.match(o)]
    if isinstance(answer, bool):
        want = "yes" if answer else "no"
        for o in opts:
            if norm(o) == want or norm(o).startswith(want + " "):
                return o
        return None
    if answer is None:
        return None
    a = norm(str(answer))
    if a == "decline":
        return next((o for o in opts if DECLINE_RE.search(o)), None)
    if a in ("yes", "no"):
        return pick_option(a == "yes", options)
    for o in opts:
        if norm(o) == a:
            return o
    # unique containment either way (e.g. answer "2 weeks" vs option "2 weeks or less")
    def word_in(needle: str, hay: str) -> bool:
        return bool(needle) and re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", hay) is not None

    hits = [o for o in opts if word_in(a, norm(o)) or word_in(norm(o), a)]
    return hits[0] if len(hits) == 1 else None


# ---------------------------------------------------------------------------
# Browser side: describe every visible control in a container as FormField data.
# Stamps data-applier-id on each control so Python can address it afterwards.
EXTRACT_JS = r"""
(root) => {
  const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const txt = el => (el && el.innerText || '').replace(/\s+/g, ' ').trim();
  const labelFor = el => {
    if (el.id) { const l = root.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) return txt(l); }
    const lb = el.getAttribute('aria-labelledby');
    if (lb) { const t = lb.split(/\s+/).map(i => txt(document.getElementById(i))).join(' ').trim(); if (t) return t; }
    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
    const wrap = el.closest('label'); if (wrap) return txt(wrap);
    return '';
  };
  const groupLabel = el => {
    const fs = el.closest('fieldset');
    if (fs) { const lg = fs.querySelector('legend, [data-test-form-builder-radio-button-form-component__title]'); if (lg) return txt(lg); }
    return '';
  };
  const out = []; let n = 0; const doneGroups = new Set();
  const stamp = el => { if (!el.dataset.applierId) el.dataset.applierId = 'f' + (n++) + '-' + Math.random().toString(36).slice(2,6); return el.dataset.applierId; };
  for (const el of root.querySelectorAll('input, select, textarea')) {
    const type = (el.type || '').toLowerCase();
    if (['hidden', 'file', 'submit', 'button'].includes(type)) continue;
    // Radios/checkboxes are often visually hidden themselves; judge them by their label or fieldset.
    const shown = visible(el) || Array.from(el.labels || []).some(visible) || (el.closest('fieldset') && visible(el.closest('fieldset')));
    if (!shown) continue;
    const required = el.required || el.getAttribute('aria-required') === 'true';
    if (type === 'radio') {
      const key = el.name || groupLabel(el);
      if (doneGroups.has(key)) continue; doneGroups.add(key);
      const radios = Array.from(root.querySelectorAll('input[type=radio]')).filter(r => (r.name || groupLabel(r)) === key);
      out.push({ id: stamp(el), kind: 'radio', label: groupLabel(el) || labelFor(el), required,
                 options: radios.map(r => labelFor(r)), value: (radios.find(r => r.checked) ? labelFor(radios.find(r => r.checked)) : '') });
      continue;
    }
    if (type === 'checkbox') {
      out.push({ id: stamp(el), kind: 'checkbox', label: labelFor(el) || groupLabel(el), required, options: [], value: el.checked ? 'true' : '' });
      continue;
    }
    if (el.tagName === 'SELECT') {
      const opts = Array.from(el.options).map(o => o.text.trim());
      out.push({ id: stamp(el), kind: 'select', label: labelFor(el) || groupLabel(el), required, options: opts, value: el.selectedOptions[0] ? el.selectedOptions[0].text.trim() : '' });
      continue;
    }
    out.push({ id: stamp(el), kind: el.tagName === 'TEXTAREA' ? 'textarea' : (type === 'number' ? 'number' : 'text'),
               label: labelFor(el) || groupLabel(el) || el.placeholder || '', required, options: [], value: el.value || '' });
  }
  return out;
}
"""
