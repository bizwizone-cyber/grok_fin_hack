#!/usr/bin/env python3
"""Push one screening run (our OUTPUT rows only) to Supabase for the review dashboard.

Standard library only. Never uploads the raw dev dataset (customers, KYC, notes...):
only hit_id, alert_id, decision, reason_code, customer_record_id, reason, evidence ids.

Env:  SUPABASE_URL  e.g. https://uqqrzlonhbipdisgfeca.supabase.co
      SUPABASE_KEY  a key allowed to INSERT into runs/decisions
                    (the project's secret / service_role key - NEVER commit it).
      (a .env file next to this script, or in the repo root, is read if present)

Usage:
  python3 dashboard/push_run.py --data <data folder>            # runs system/run, times it, pushes
  python3 dashboard/push_run.py --output output/dev.jsonl       # push an existing output file
Options: --label TEXT  --commit SHA  --system-run PATH  --started-at ISO  --runtime SECONDS
"""
import argparse, collections, json, os, subprocess, sys, tempfile, time, uuid
import urllib.request, urllib.error
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DECISIONS = {"SAME_ENTITY", "DIFFERENT_ENTITY", "REVIEW"}


def load_env():
    for p in (os.path.join(HERE, ".env"), os.path.join(REPO, ".env")):
        if os.path.isfile(p):
            for line in open(p, encoding="utf-8"):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def git_commit(repo):
    try:
        sha = subprocess.check_output(["git", "-C", repo, "rev-parse", "--short", "HEAD"],
                                      text=True, stderr=subprocess.DEVNULL).strip()
        dirty = subprocess.check_output(["git", "-C", repo, "status", "--porcelain", "--", "system"],
                                        text=True, stderr=subprocess.DEVNULL).strip()
        return sha + ("+dirty" if dirty else "")
    except Exception:
        return None


def rest(method, path, body=None, extra=None):
    url = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/" + path
    key = os.environ["SUPABASE_KEY"]
    headers = {"apikey": key, "Authorization": "Bearer " + key,
               "Content-Type": "application/json", "Prefer": "return=minimal"}
    headers.update(extra or {})
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status
    except urllib.error.HTTPError as e:
        sys.exit("Supabase error %s on %s: %s" % (e.code, path, e.read().decode("utf-8", "replace")[:500]))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", help="data folder to run system/run on")
    ap.add_argument("--output", help="existing output .jsonl (skips running system/run)")
    ap.add_argument("--system-run", default=os.path.join(REPO, "system", "run"))
    ap.add_argument("--label", help="data_label stored with the run (default: data folder name)")
    ap.add_argument("--commit", help="git commit to record (default: HEAD of this repo)")
    ap.add_argument("--started-at", help="ISO timestamp override (default: now)")
    ap.add_argument("--runtime", type=float, help="runtime seconds when pushing an existing --output")
    ap.add_argument("--dry-run", action="store_true", help="parse and print the summary, upload nothing")
    a = ap.parse_args()
    load_env()
    if not a.dry_run:
        for k in ("SUPABASE_URL", "SUPABASE_KEY"):
            if not os.environ.get(k):
                sys.exit("Set %s (see dashboard/.env.example)" % k)
    if not (a.data or a.output):
        ap.error("give --data <folder> or --output <file>")

    started = datetime.now(timezone.utc)
    runtime = a.runtime
    out = a.output
    if a.data:
        out = out or os.path.join(tempfile.mkdtemp(prefix="push_run_"), "out.jsonl")
        t0 = time.time()
        subprocess.run([sys.executable, a.system_run, a.data, out], check=True)
        runtime = round(time.time() - t0, 2)
    finished = datetime.now(timezone.utc)
    if a.started_at:
        started = datetime.fromisoformat(a.started_at.replace("Z", "+00:00"))
        if runtime is not None:
            finished = datetime.fromtimestamp(started.timestamp() + runtime, timezone.utc)

    rows, run_id = [], str(uuid.uuid4())
    seen = set()
    for n, line in enumerate(open(out, encoding="utf-8"), 1):
        if not line.strip():
            continue
        r = json.loads(line)
        if r["decision"] not in DECISIONS or r["hit_id"] in seen:
            sys.exit("line %d: bad decision or duplicate hit_id %r" % (n, r.get("hit_id")))
        seen.add(r["hit_id"])
        rows.append({"run_id": run_id, "hit_id": r["hit_id"], "alert_id": r["alert_id"],
                     "decision": r["decision"], "reason_code": r["reason_code"],
                     "customer_record_id": r.get("customer_record_id"),
                     "reason": r.get("reason"), "evidence": r.get("evidence") or [],
                     "decided_at": finished.isoformat()})
    c = collections.Counter(r["decision"] for r in rows)
    counts = {"total": len(rows), "SAME_ENTITY": c["SAME_ENTITY"], "DIFFERENT_ENTITY": c["DIFFERENT_ENTITY"],
              "REVIEW": c["REVIEW"], "by_reason_code": dict(collections.Counter(r["reason_code"] for r in rows))}
    run = {"id": run_id, "started_at": started.isoformat(), "finished_at": finished.isoformat(),
           "git_commit": a.commit or git_commit(REPO),
           "data_label": a.label or (os.path.basename(os.path.normpath(a.data)) if a.data else "output-file"),
           "counts": counts, "runtime_s": runtime}
    print("run", run_id, run["git_commit"], json.dumps({k: counts[k] for k in list(counts)[:4]}), "runtime_s", runtime)
    if a.dry_run:
        return
    rest("POST", "runs", run)
    try:
        for i in range(0, len(rows), 200):
            rest("POST", "decisions", rows[i:i + 200])
    except SystemExit:
        try:
            rest("DELETE", "runs?id=eq." + run_id)  # cascade removes partial decisions
        except SystemExit:
            pass
        raise
    print("pushed %d decisions (run id %s)" % (len(rows), run_id))


if __name__ == "__main__":
    main()
