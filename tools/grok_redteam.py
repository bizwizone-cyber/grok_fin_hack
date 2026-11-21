#!/usr/bin/env python3
"""Grok red team for the free-text readers in system/run.

The unseen set words its free text differently from the development set. This
tool asks Grok (through OpenRouter) to write new wordings of the sentences our
readers depend on, runs every wording through the real reader functions in
system/run, and reports where a reader misses a fact or, worse, reads a wrong
one. Grok only writes test sentences and suggests vocabulary; a person decides
what goes into system/run, and system/run itself never calls a model.

Only invented people are used. No development data is sent to Grok or written
to disk. Stdlib only.

Usage:
    tools/grok_redteam.py                    # hand-written wordings, no API call
    OPENROUTER_API_KEY=... tools/grok_redteam.py --live
    tools/grok_redteam.py --cached           # re-test the last Grok wordings
    tools/grok_redteam.py --live --suggest   # also ask Grok for vocabulary fixes

Outcomes per sentence:
    ok      the reader got the fact right
    missed  the reader found nothing (costs a REVIEW, 2-3 points)
    WRONG   the reader found a different fact (can cause an unsafe clearance, 60 points)
"""
from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "redteam_out"
CACHE = OUT_DIR / "grok_templates.json"
DEFAULT_MODEL = "x-ai/grok-4.7"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def load_system():
    loader = importlib.machinery.SourceFileLoader("screening_run", str(ROOT / "system" / "run"))
    spec = importlib.util.spec_from_loader("screening_run", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Invented people. Days are above 12 so day/month order is never ambiguous.
# ---------------------------------------------------------------------------
CUSTOMERS = [
    dict(NAME="MARTA KOVACS", dob=date(1979, 8, 23), COUNTRY="Hungary", DOC="passport",
         PROVIDER="Onfido", check=date(2024, 5, 17)),
    dict(NAME="ADEBAYO OKONJO", dob=date(1985, 11, 30), COUNTRY="Nigeria", DOC="national ID",
         PROVIDER="Sumsub", check=date(2023, 9, 14)),
    dict(NAME="SOFIA LINDBERG", dob=date(1992, 2, 19), COUNTRY="Sweden", DOC="driving licence",
         PROVIDER="Veriff", check=date(2025, 1, 22)),
    dict(NAME="RAHUL MEHTA", dob=date(1968, 7, 15), COUNTRY="India", DOC="passport",
         PROVIDER="Persona", check=date(2024, 12, 3)),
]
LISTED = [
    dict(Name="Viktor Bramov", AGE=47, MONTH="March", YEAR=2019, death=date(2016, 4, 18)),
    dict(Name="Hassan Qureshi", AGE=52, MONTH="November", YEAR=2012, death=date(2009, 10, 27)),
    dict(Name="Elena Duarte", AGE=38, MONTH="June", YEAR=2021, death=date(2020, 1, 30)),
]
LIVED = [
    dict(COUNTRY="the Netherlands", expect="Netherlands", FROM=2008, TO=2015),
    dict(COUNTRY="the United Arab Emirates", expect="United Arab Emirates", FROM=2011, TO=2019),
    dict(COUNTRY="Côte d'Ivoire", expect="Côte d'Ivoire", FROM=2003, TO=2010),
    dict(COUNTRY="South Korea", expect="South Korea", FROM=2014, TO=2020),
]

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]


def ordinal(n):
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


DATE_FORMATS = [
    ("iso", lambda d: d.isoformat()),
    ("d/m/y", lambda d: f"{d.day:02d}/{d.month:02d}/{d.year}"),
    ("d Month y", lambda d: f"{d.day} {MONTHS[d.month - 1]} {d.year}"),
    ("Month d, y", lambda d: f"{MONTHS[d.month - 1]} {d.day}, {d.year}"),
    ("d Mon y", lambda d: f"{d.day} {MONTHS[d.month - 1][:3]} {d.year}"),
    ("d.m.y", lambda d: f"{d.day:02d}.{d.month:02d}.{d.year}"),
    ("y/m/d", lambda d: f"{d.year}/{d.month:02d}/{d.day:02d}"),
    ("dth of Month y", lambda d: f"{ordinal(d.day)} of {MONTHS[d.month - 1]} {d.year}"),
    ("d-m-y", lambda d: f"{d.day:02d}-{d.month:02d}-{d.year}"),
    ("Month dth, y", lambda d: f"{MONTHS[d.month - 1]} {ordinal(d.day)}, {d.year}"),
]

# ---------------------------------------------------------------------------
# What each category means, for Grok, plus hand-written wordings for offline use.
# ---------------------------------------------------------------------------
CATEGORIES = {
    "kyc_pass": dict(
        brief=("An onboarding analyst's note recording that an identity check PASSED. "
               "Placeholders: {NAME} (customer's full name in capitals), {DOB} (date of birth), "
               "{DOC} (passport / national ID / driving licence), {COUNTRY} (issuing country), "
               "{PROVIDER} (KYC vendor), {CHECKDATE} (date of the check). Use every placeholder once."),
        examples=[
            "{NAME} passed the {PROVIDER} check on {CHECKDATE} using a {DOC} from {COUNTRY}; DOB {DOB}.",
            "ID verification ({PROVIDER}, {CHECKDATE}) approved. Document: {DOC} ({COUNTRY}). Name on document: {NAME}. Date of birth on document: {DOB}.",
        ],
        handwritten=[
            "{PROVIDER} signed off on {NAME}'s {DOC} ({COUNTRY}) on {CHECKDATE}. Birth date shown: {DOB}.",
            "KYC OK - {NAME}, D.O.B. {DOB}, {COUNTRY} {DOC}, checked by {PROVIDER} {CHECKDATE}.",
            "Identity confirmed for {NAME} (born {DOB}) via {PROVIDER} on {CHECKDATE}; document was a {COUNTRY} {DOC}.",
            "{CHECKDATE}: {PROVIDER} cleared {NAME}. {DOC} issued by {COUNTRY}, date of birth {DOB}.",
            "Customer {NAME} - {DOC} from {COUNTRY} accepted by {PROVIDER} on {CHECKDATE}. DOB: {DOB}.",
            "{PROVIDER} liveness and document check green for {NAME}; {COUNTRY} {DOC}; birthdate {DOB}; {CHECKDATE}.",
            "Onboarding complete. {NAME}, birth date {DOB}, verified against a {COUNTRY} {DOC} by {PROVIDER} ({CHECKDATE}).",
            "{NAME}'s identity was successfully established on {CHECKDATE} by {PROVIDER} from a {DOC} ({COUNTRY}) giving {DOB} as the date of birth.",
            "Result from {PROVIDER} on {CHECKDATE}: match. Subject {NAME}, DOB {DOB}, {DOC}, {COUNTRY}.",
            "{PROVIDER} returned a clear result for {NAME} on {CHECKDATE} ({COUNTRY} {DOC}, born {DOB}).",
        ],
    ),
    "kyc_fail": dict(
        brief=("An onboarding analyst's note recording that an identity check did NOT pass "
               "(any reason: rejected, inconclusive, expired, unreadable, sent back, mismatch, suspected "
               "forgery, abandoned...). It must be unambiguous that the check did not pass. "
               "Placeholders: {NAME}, {DOB}, {DOC}, {COUNTRY}, {PROVIDER}, {CHECKDATE}."),
        examples=[
            "{PROVIDER} rejected the {DOC} from {COUNTRY} for {NAME} (DOB {DOB}) on {CHECKDATE}.",
            "ID check for {NAME} on {CHECKDATE}: {PROVIDER} could not verify the {COUNTRY} {DOC}; DOB on file {DOB}.",
        ],
        handwritten=[
            "{PROVIDER} returned 'no match' for {NAME} (DOB {DOB}) on {CHECKDATE}; {COUNTRY} {DOC} photo did not correspond.",
            "{NAME}'s {DOC} ({COUNTRY}, DOB {DOB}) bounced at {PROVIDER} on {CHECKDATE} - glare on the photo page.",
            "Check outcome {CHECKDATE}: {PROVIDER} marked {NAME} as 'refer'. {COUNTRY} {DOC}, born {DOB}.",
            "{PROVIDER} verification of {NAME} (born {DOB}) was NOT successful on {CHECKDATE}; {DOC} from {COUNTRY}.",
            "{NAME} - {COUNTRY} {DOC} out of date when checked by {PROVIDER} on {CHECKDATE}. DOB {DOB}. New document requested.",
            "{PROVIDER} ({CHECKDATE}): {DOC} for {NAME}, DOB {DOB}, {COUNTRY} - result: fail.",
            "Customer {NAME} (DOB {DOB}) dropped out of the {PROVIDER} flow on {CHECKDATE} before uploading the {COUNTRY} {DOC}.",
            "{PROVIDER} raised a red flag on the {COUNTRY} {DOC} of {NAME} (DOB {DOB}) on {CHECKDATE}; possible manipulation.",
            "{NAME}, DOB {DOB}: {PROVIDER} check of {COUNTRY} {DOC} on {CHECKDATE} unsuccessful, customer told to try again.",
            "Identity not established for {NAME} ({DOB}). {PROVIDER} could not read the {DOC} from {COUNTRY} on {CHECKDATE}.",
        ],
    ),
    "age": dict(
        brief=("The first sentence of a watchlist profile that gives the listed person's AGE at a "
               "dated event (sentencing, appointment, arrest, listing...). Placeholders: {Name}, "
               "{AGE} (a number), {MONTH} (month name), {YEAR}. Use each once."),
        examples=[
            "{Name}, aged {AGE}, was sentenced in {MONTH} {YEAR} for fraud.",
            "In {MONTH} {YEAR}, the {AGE}-year-old {Name} was appointed deputy minister.",
        ],
        handwritten=[
            "{Name} ({AGE}) was arrested in {MONTH} {YEAR}.",
            "Court records from {MONTH} {YEAR} list {Name}, then {AGE} years of age, as a defendant.",
            "{Name}, who was {AGE} at the time, was designated in {MONTH} {YEAR}.",
            "At {AGE}, {Name} became a regional governor in {MONTH} {YEAR}.",
            "{Name} was {AGE} when sanctioned in {MONTH} {YEAR}.",
            "A {MONTH} {YEAR} report describes {Name} as {AGE} years old.",
            "{Name}, {AGE}, was named in the {MONTH} {YEAR} indictment.",
            "{Name} (age {AGE}) was convicted in {MONTH} {YEAR}.",
            "{Name} was appointed in {MONTH} {YEAR}, aged {AGE}.",
            "In {YEAR} ({MONTH}), {Name}, {AGE}, was listed by the regulator.",
        ],
    ),
    "death": dict(
        brief=("A sentence in a watchlist profile stating that the listed person has DIED, with the "
               "date of death. Placeholders: {Name}, {DEATH} (the date of death). Use each once."),
        examples=[
            "{Name} died on {DEATH}.",
            "Date of death: {DEATH}.",
        ],
        handwritten=[
            "{Name} passed away on {DEATH} in hospital.",
            "Deceased ({DEATH}).",
            "{Name} was killed in an air strike on {DEATH}.",
            "Reported dead on {DEATH}.",
            "{Name} died {DEATH}.",
            "Death confirmed by family on {DEATH}.",
            "{Name} (d. {DEATH}) was a former minister.",
            "Following {Name}'s death on {DEATH}, the listing was kept.",
            "{Name} was found dead on {DEATH}.",
            "Late {Name}, who died on {DEATH}, remains listed.",
        ],
    ),
    "lived": dict(
        brief=("A bank support or onboarding note saying where a customer used to LIVE or WORK. "
               "Placeholders: {COUNTRY} (a country name, possibly with 'the'), {FROM} and {TO} (years). "
               "Use each once."),
        examples=[
            "Customer said they lived in {COUNTRY} from {FROM} to {TO}.",
            "Self-reported residency and career timeline in {COUNTRY}: {FROM} to {TO}.",
        ],
        handwritten=[
            "Previous address was in {COUNTRY} ({FROM}-{TO}).",
            "Customer relocated from {COUNTRY} in {TO} after moving there in {FROM}.",
            "Spent {FROM}–{TO} working for a bank in {COUNTRY}.",
            "Was resident in {COUNTRY} between {FROM} and {TO}.",
            "Grew up and studied in {COUNTRY}, left in {TO} (arrived {FROM}).",
            "Address history: {COUNTRY}, {FROM} to {TO}.",
            "Customer's home from {FROM} to {TO} was {COUNTRY}.",
            "Employment abroad in {COUNTRY} ({FROM}/{TO}).",
            "Moved to the UK in {TO}; before that lived in {COUNTRY} since {FROM}.",
            "Tax residence {COUNTRY} {FROM}-{TO}.",
        ],
    ),
}


# ---------------------------------------------------------------------------
# Grok through OpenRouter
# ---------------------------------------------------------------------------
def call_grok(messages, model, key, temperature=0.9):
    body = json.dumps({"model": model, "messages": messages, "temperature": temperature,
                       "max_tokens": 4000}).encode()
    req = urllib.request.Request(OPENROUTER_URL, data=body, headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "X-Title": "screening-redteam",
    })
    # python.org builds on macOS ship without CA certificates; fall back to the
    # system bundle so the connection is still verified.
    ctx = ssl.create_default_context()
    if not ctx.get_ca_certs() and os.path.exists("/etc/ssl/cert.pem"):
        ctx.load_verify_locations("/etc/ssl/cert.pem")
    with urllib.request.urlopen(req, timeout=180, context=ctx) as resp:
        data = json.load(resp)
    return data["choices"][0]["message"]["content"]


def parse_json_block(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object in model reply")
    return json.loads(m.group(0))


def grok_templates(cat, spec, n, model, key):
    placeholders = sorted(set(re.findall(r"\{\w+\}", " ".join(spec["examples"]))))
    prompt = (
        "You write test data for a parser used in bank sanctions screening.\n"
        f"Category: {spec['brief']}\n\n"
        "Examples of the existing wording:\n" + "\n".join(f"- {e}" for e in spec["examples"]) +
        f"\n\nWrite {n} NEW sentences or short notes for this category the way a different vendor, "
        "analyst or data provider might phrase them. Vary vocabulary, word order, punctuation, "
        "abbreviations and where the date sits. Keep them realistic and unambiguous. "
        f"Keep the placeholders exactly as written ({', '.join(placeholders)}), each used once, "
        "and do not add any other dates, ages or countries.\n"
        'Reply with only JSON: {"templates": ["...", "..."]}'
    )
    reply = call_grok([{"role": "user", "content": prompt}], model, key)
    templates = parse_json_block(reply).get("templates") or []
    keep = []
    for t in templates:
        if isinstance(t, str) and all(p in t for p in placeholders):
            keep.append(t.strip())
    return keep


def grok_suggestions(failures, model, key):
    lines = "\n".join(f"- [{f['category']}] expected {f['expected']}, reader gave {f['got']}: {f['text']}"
                      for f in failures[:40])
    prompt = (
        "These sentences were misread by a rule-based (regex) parser in a sanctions screening system. "
        "For each, quote the exact words that carry the fact the parser missed, and propose the word or "
        "short phrase to add to the parser's vocabulary. Never propose anything that would make a failed "
        "identity check read as passed.\n" + lines +
        '\nReply with only JSON: {"suggestions": [{"quote": "...", "add": "...", "category": "..."}]}'
    )
    return parse_json_block(call_grok([{"role": "user", "content": prompt}], model, key, 0.0)).get("suggestions") or []


# ---------------------------------------------------------------------------
# Running sentences through the real readers
# ---------------------------------------------------------------------------
def fill(template, values):
    out = template
    for k, v in values.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def norm_name(s):
    # system/run compares names ignoring punctuation, so "MARTA KOVACS." is fine
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", s or "")).strip().upper()


def cases(cat, templates):
    """Yield (text, expected, values) for every template x invented person."""
    i = 0
    for t in templates:
        if cat in ("kyc_pass", "kyc_fail"):
            for c in CUSTOMERS:
                label, fmt = DATE_FORMATS[i % len(DATE_FORMATS)]
                i += 1
                vals = dict(NAME=c["NAME"], DOB=fmt(c["dob"]), DOC=c["DOC"], COUNTRY=c["COUNTRY"],
                            PROVIDER=c["PROVIDER"], CHECKDATE=c["check"].isoformat())
                yield fill(t, vals), c, label
        elif cat == "age":
            for p in LISTED:
                yield fill(t, p), p, ""
        elif cat == "death":
            for p in LISTED:
                label, fmt = DATE_FORMATS[i % len(DATE_FORMATS)]
                i += 1
                yield fill(t, dict(Name=p["Name"], DEATH=fmt(p["death"]))), p, label
        elif cat == "lived":
            for p in LIVED:
                yield fill(t, p), p, ""


def check(sysmod, cat, text, exp):
    """Return (outcome, expected, got)."""
    if cat == "kyc_fail":
        got = sysmod.parse_kyc_note({"text": text, "channel": "kyc", "note_id": "NOTE-TEST", "created_at": None})
        if got is None:
            return "ok", "not a pass", "not a pass"
        return "WRONG", "not a pass", f"read as PASS (name={got['verified_name']}, dob={got['verified_dob'] and got['verified_dob'].iso()})"
    if cat == "kyc_pass":
        got = sysmod.parse_kyc_note({"text": text, "channel": "kyc", "note_id": "NOTE-TEST", "created_at": None})
        want = f"name={exp['NAME']}, dob={exp['dob'].isoformat()}"
        if got is None:
            return "missed", want, "not read as a pass"
        problems, wrong = [], False
        gname, gdob = got["verified_name"], got["verified_dob"]
        if not gname:
            problems.append("no name")
        elif norm_name(gname) != exp["NAME"]:
            problems.append(f"name={gname}")
            wrong = True
        if not gdob:
            problems.append("no dob")
        elif gdob.iso() != exp["dob"].isoformat():
            problems.append(f"dob={gdob.iso()}")
            wrong = True
        if not problems:
            return "ok", want, want
        return ("WRONG" if wrong else "missed"), want, "; ".join(problems)
    if cat == "age":
        years, _ = sysmod.extract_age_years(text)
        got = sorted({d.year for d in years})
        want = sorted({exp["YEAR"] - exp["AGE"] - 1, exp["YEAR"] - exp["AGE"]})
        if got == want:
            return "ok", want, got
        if not got:
            return "missed", want, "no age found"
        if not set(want) & set(got):
            return "WRONG", want, got
        return "missed", want, f"partly: {got}"
    if cat == "death":
        # A profile that mentions the person's death is flagged deceased by the
        # provider; system/run only trusts free-text death wording when it is.
        got = sysmod.death_date({"profile_notes": text, "deceased_date": None, "life_status": "Deceased"})
        want = exp["death"].isoformat()
        if not got:
            return "missed", want, "no death date"
        if got.iso() == want:
            return "ok", want, want
        # a year-only reading of the right year is safe, just less precise
        if got.year == exp["death"].year and (not got.month):
            return "ok", want, got.iso()
        return "WRONG", want, got.iso()
    if cat == "lived":
        got = [c for c, _ in sysmod.lived_countries([{"text": text, "channel": "phone", "note_id": "NOTE-TEST"}])]
        want = exp["expect"]
        if want in got:
            return "ok", want, got
        if not got:
            return "missed", want, "no country"
        return "WRONG", want, got
    raise ValueError(cat)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--live", action="store_true", help="ask Grok for new wordings (needs OPENROUTER_API_KEY)")
    ap.add_argument("--cached", action="store_true", help="re-test the wordings Grok wrote last time")
    ap.add_argument("--suggest", action="store_true", help="with --live, ask Grok for vocabulary fixes")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--n", type=int, default=12, help="wordings per category from Grok")
    ap.add_argument("--show", type=int, default=6, help="failures to print per category")
    args = ap.parse_args(argv)

    sysmod = load_system()
    OUT_DIR.mkdir(exist_ok=True)
    key = os.environ.get("OPENROUTER_API_KEY")

    if args.live:
        if not key:
            print("OPENROUTER_API_KEY is not set; use the hand-written wordings (no flag) instead.", file=sys.stderr)
            return 2
        source = f"Grok ({args.model})"
        templates = {}
        for cat, spec in CATEGORIES.items():
            try:
                templates[cat] = grok_templates(cat, spec, args.n, args.model, key)
            except (urllib.error.URLError, ValueError, KeyError) as exc:
                print(f"{cat}: Grok call failed ({exc}); skipping", file=sys.stderr)
                templates[cat] = []
        if not any(templates.values()):
            print("Grok returned no usable wordings; nothing was tested.", file=sys.stderr)
            return 2
        CACHE.write_text(json.dumps({"model": args.model, "templates": templates}, indent=1, ensure_ascii=False))
    elif args.cached:
        saved = json.loads(CACHE.read_text())
        templates, source = saved["templates"], f"Grok ({saved['model']}), cached"
    else:
        templates = {cat: spec["handwritten"] for cat, spec in CATEGORIES.items()}
        source = "hand-written wordings (not Grok)"

    print(f"Red team source: {source}\n")
    report, failures = {}, []
    print(f"{'category':10} {'sentences':>9} {'ok':>5} {'missed':>7} {'WRONG':>6}")
    for cat in CATEGORIES:
        counts = {"ok": 0, "missed": 0, "WRONG": 0}
        rows = []
        for text, exp, label in cases(cat, templates.get(cat) or []):
            outcome, want, got = check(sysmod, cat, text, exp)
            counts[outcome] += 1
            if outcome != "ok":
                row = dict(category=cat, outcome=outcome, expected=str(want), got=str(got),
                           date_format=label, text=text)
                rows.append(row)
                failures.append(row)
        report[cat] = dict(counts=counts, failures=rows)
        total = sum(counts.values())
        print(f"{cat:10} {total:>9} {counts['ok']:>5} {counts['missed']:>7} {counts['WRONG']:>6}")

    for cat, r in report.items():
        bad = sorted(r["failures"], key=lambda f: f["outcome"] != "WRONG")
        if not bad:
            continue
        print(f"\n== {cat}: first {min(args.show, len(bad))} of {len(bad)} problems (WRONG first)")
        for f in bad[:args.show]:
            fmt = f" [{f['date_format']}]" if f["date_format"] else ""
            print(f"  {f['outcome']:6}{fmt} expected {f['expected']} | got {f['got']}\n         {f['text']}")

    if args.live and args.suggest and failures and key:
        try:
            sugg = grok_suggestions(failures, args.model, key)
            (OUT_DIR / "grok_suggestions.json").write_text(json.dumps(sugg, indent=1, ensure_ascii=False))
            print(f"\nGrok suggested {len(sugg)} vocabulary additions -> {OUT_DIR / 'grok_suggestions.json'} "
                  "(for a person to approve; nothing is applied automatically)")
        except (urllib.error.URLError, ValueError, KeyError) as exc:
            print(f"\nsuggestion call failed: {exc}", file=sys.stderr)

    (OUT_DIR / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str))
    wrong = sum(r["counts"]["WRONG"] for r in report.values())
    print(f"\n{wrong} WRONG readings (possible unsafe clearances). Full report: {OUT_DIR / 'report.json'}")
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main())
