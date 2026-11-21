# Review dashboard (Supabase) – demo tooling, not part of the scored entry point

`system/run` is untouched: standard library only, offline, no Supabase. This folder is a separate
tool that reads our **output rows** (hit_id, alert_id, decision, reason_code, customer_record_id,
reason, evidence ids) – never the raw dev dataset – and shows them to a human reviewer.

Project: `grok-fin-hack` · ref `uqqrzlonhbipdisgfeca` · https://uqqrzlonhbipdisgfeca.supabase.co · eu-west-2

| File | What |
|---|---|
| `schema.sql` | tables `runs`, `decisions`, `analyst_reviews`, indexes, RLS (already applied) |
| `push_run.py` | stdlib: run `system/run` (or take an output file), time it, record git commit, bulk-insert via REST |
| `index.html` | single static page, supabase-js from CDN, publishable key embedded (public by design) |
| `.env.example` | placeholders; copy to `dashboard/.env` (git-ignored) |

## Open the dashboard
```
python3 -m http.server 8765 --directory dashboard
# then open http://localhost:8765/index.html   (opening index.html directly also works)
```
Run selector (date/time) · counts, review rate, runtime · filters (decision, reason code, alert/hit/customer search,
date range for runs and reviews) · chart of decisions by reason code · click a hit for reason/evidence/customer ·
**Review queue** tab: analyst name, decision, POLICY.md reason code, note → saved to `analyst_reviews` · **Review history** tab.

## Push a new run
```
cp dashboard/.env.example dashboard/.env      # fill SUPABASE_KEY with the project's SECRET key (never commit)
set -a; . dashboard/.env; set +a
python3 dashboard/push_run.py --data ~/Downloads/screening-track/dev --label dev      # runs system/run, times it
python3 dashboard/push_run.py --output output/dev.jsonl --label dev --runtime 27      # push an existing output
python3 dashboard/push_run.py --output output/dev.jsonl --dry-run                     # parse only
```
Inserting runs/decisions needs the secret (service_role) key because anon cannot write them. Keep it local.

## Demo-only security exposure
The anon (publishable) key can SELECT `runs`, `decisions`, `analyst_reviews` and INSERT into `analyst_reviews`
(constrained: valid decision and reason code, name ≤ 80 chars, note ≤ 2000). Anyone with the page/key can read the
output rows (customer record ids and reason text) and add reviews. Before using anything real: add Supabase Auth,
restrict policies to `authenticated`, and drop the `demo_anon_*` policies. Don't point it at real customer data.
