#!/usr/bin/env python3
"""Run every test on several versions of system/run and write dashboard/tests.html.

Versions are git refs; each one's system/run is taken with `git show`, so the
working tree is not touched. Tests:

  practice   the 50 answered dev hits (dev/practice_labels.jsonl)
  dev        the full 819-hit dev run: decision mix, run time, determinism
  synthetic  a generated benchmark with answers (default: the 10,000-hit set
             in ~/Downloads/synthetic_screening_benchmark), scored per scenario
  redteam    the free-text readers on the cached Grok wordings and the
             hand-written ones (tools/grok_redteam.py)

The page only embeds results and our own output rows: no dev data rows, no
customer details beyond what output/dev.jsonl already holds. Stdlib only.

    tools/build_test_dashboard.py
    tools/build_test_dashboard.py --synthetic /path/to/synthetic_test
"""
from __future__ import annotations

import argparse
import collections
import importlib.machinery
import importlib.util
import json
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "dashboard" / "tests.html"
DEFAULT_SYNTH = Path.home() / "Downloads" / "synthetic_screening_benchmark" / "synthetic_test"

VERSIONS = [
    ("baseline", "Before hardening", "43fb0f1", "Rules engine as it stood at 15:00, before the red team and before main's hardening."),
    ("nongrok", "Non-Grok (main)", "f4ebeb5", "Main without the red team: hand hardening (date formats, crash-safe fallback, stricter death rules, trimmed evidence) and Amin's company/namesake safety rules."),
    ("grok", "Grok red team + main", "grok-redteam", "Everything on main plus the fixes found by the Grok red team, per-ground evidence, and a narrower company rule that keeps registry incorporation dates."),
]

SCENARIOS = {
    0: "Birth year in free-text profile",
    1: "Year-only birth date on the hit",
    2: "Name + DOB agree, country conflicts",
    3: "Rejected old check, newer approved",
    4: "Namesake 'Jr' record, shared phone",
    5: "Second record, same phone + DOB",
    6: "Reordered, accented name",
    7: "Company with a legal form",
    8: "No DOB and no country",
    9: "DOB contradicts by 12 years",
}

COST = {
    ("DIFFERENT_ENTITY", "SAME_ENTITY"): 3, ("DIFFERENT_ENTITY", "REVIEW"): 3,
    ("SAME_ENTITY", "DIFFERENT_ENTITY"): 60, ("SAME_ENTITY", "REVIEW"): 2,
    ("REVIEW", "DIFFERENT_ENTITY"): 60, ("REVIEW", "SAME_ENTITY"): 2,
}


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout


def load_module(path, name):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def run_system(script, data, out):
    t0 = time.monotonic()
    subprocess.run([sys.executable, "-I", str(script), str(data), str(out)], check=True,
                   capture_output=True, text=True)
    return round(time.monotonic() - t0, 1)


def read_rows(path):
    with open(path, encoding="utf-8") as fh:
        return {r["hit_id"]: r for r in (json.loads(l) for l in fh if l.strip())}


def score(rows, labels):
    allrev = sum(COST.get((g["decision"], "REVIEW"), 0) for g in labels)
    cost = right = unsafe = code = cust = ev = 0
    for g in labels:
        r = rows[g["hit_id"]]
        c = COST.get((g["decision"], r["decision"]), 0)
        cost += c
        unsafe += c == 60
        if r["decision"] == g["decision"]:
            right += 1
            code += r["reason_code"] in g["reason_codes"]
            cust += r["customer_record_id"] in g["customer_record_ids"]
            ev += set(r["evidence"]) == set(g["evidence"])
    pct = (lambda n: round(100 * n / right, 1) if right else 0.0)
    return {
        "n": len(labels), "right": right, "unsafe": unsafe, "cost": cost, "allreview_cost": allrev,
        "decision_score": round(max(0.0, 100 * (1 - cost / allrev)), 1) if allrev else 100.0,
        "code_pct": pct(code), "customer_pct": pct(cust), "evidence_exact": ev,
    }


def redteam(script, rt):
    mod = load_module(script, "rt_target_%d" % id(script))
    out = {}
    sources = {"Hand-written": {c: s["handwritten"] for c, s in rt.CATEGORIES.items()}}
    cache = ROOT / "redteam_out" / "grok_templates.json"
    if cache.exists():
        sources["Grok"] = json.loads(cache.read_text())["templates"]
    for src, templates in sources.items():
        cats = {}
        for cat in rt.CATEGORIES:
            tally = collections.Counter()
            for text, exp, _ in rt.cases(cat, templates.get(cat) or []):
                outcome, _, _ = rt.check(mod, cat, text, exp)
                tally[outcome] += 1
            cats[cat] = {"ok": tally["ok"], "missed": tally["missed"], "WRONG": tally["WRONG"]}
        out[src] = cats
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--synthetic", type=Path, default=DEFAULT_SYNTH,
                    help="folder with data/ and ground_truth.jsonl")
    args = ap.parse_args(argv)

    rt = load_module(ROOT / "tools" / "grok_redteam.py", "grok_redteam")
    practice = [json.loads(l) for l in open(ROOT / "dev" / "practice_labels.jsonl", encoding="utf-8")]
    synth_ok = (args.synthetic / "ground_truth.jsonl").exists()
    synth_labels = ([json.loads(l) for l in open(args.synthetic / "ground_truth.jsonl", encoding="utf-8")]
                    if synth_ok else [])

    results, dev_rows = {}, {}
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for key, label, ref, blurb in VERSIONS:
            commit = git("rev-parse", "--short", ref).strip()
            script = tmp / f"{key}.py"
            script.write_text(git("show", f"{ref}:system/run"), encoding="utf-8")
            print(f"{label} ({commit}) ...", file=sys.stderr)

            o1, o2 = tmp / f"{key}_dev1.jsonl", tmp / f"{key}_dev2.jsonl"
            secs = run_system(script, ROOT / "dev", o1)
            run_system(script, ROOT / "dev", o2)
            rows = read_rows(o1)
            dev_rows[key] = rows
            res = {
                "label": label, "ref": ref, "commit": commit, "blurb": blurb,
                "dev": {
                    "seconds": secs,
                    "deterministic": o1.read_bytes() == o2.read_bytes(),
                    "decisions": dict(collections.Counter(r["decision"] for r in rows.values())),
                },
                "practice": score(rows, practice),
                "redteam": redteam(script, rt),
            }
            if synth_ok:
                so = tmp / f"{key}_synth.jsonl"
                ssecs = run_system(script, args.synthetic / "data", so)
                srows = read_rows(so)
                per = {}
                for m, name in SCENARIOS.items():
                    labs = [g for g in synth_labels if int(g["hit_id"].split("-")[1], 16) % 10 == m]
                    s = score(srows, labs)
                    ex = labs[0]
                    s.update(name=name, expected=ex["decision"], expected_code=ex["reason_codes"][0],
                             got=srows[ex["hit_id"]]["decision"], got_code=srows[ex["hit_id"]]["reason_code"])
                    per[str(m)] = s
                res["synthetic"] = {"seconds": ssecs, "overall": score(srows, synth_labels), "scenarios": per}
            results[key] = res

    # Dev hits where the Grok version decides differently from the non-Grok one
    diffs = []
    a, b = dev_rows["nongrok"], dev_rows["grok"]
    for hid in sorted(a):
        if (a[hid]["decision"], a[hid]["reason_code"]) != (b[hid]["decision"], b[hid]["reason_code"]):
            diffs.append({"hit_id": hid, "nongrok": [a[hid]["decision"], a[hid]["reason_code"]],
                          "grok": [b[hid]["decision"], b[hid]["reason_code"]], "reason": b[hid]["reason"],
                          "evidence": b[hid]["evidence"]})

    payload = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "versions": [k for k, *_ in VERSIONS],
        "results": results,
        "dev_diffs": diffs,
        "synthetic_present": synth_ok,
        "scenarios": SCENARIOS,
    }
    html = PAGE.read_text(encoding="utf-8")
    start, end = '<script id="results" type="application/json">', "</script>"
    i = html.index(start) + len(start)
    j = html.index(end, i)
    PAGE.write_text(html[:i] + json.dumps(payload, ensure_ascii=False).replace("</", "<\\/") + html[j:],
                    encoding="utf-8")
    print(f"wrote {PAGE}", file=sys.stderr)


if __name__ == "__main__":
    main()
