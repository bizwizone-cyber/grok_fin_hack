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
| `functions/tavily-research/index.ts` | Supabase Edge Function (deployed, `verify_jwt=false`): Tavily web search for one hit |
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

## Research with Tavily (analyst support only)
Hit drawer → **Research with Tavily**. The page calls the edge function `tavily-research` `{run_id, hit_id}`; it reads the hit's
reason text (service role), builds `listed name + countries + "sanctions OR PEP OR fraud"`, calls Tavily (basic depth, answer on,
max 5 results), stores the result in `web_research` (anon: select only) and the drawer lists current + previous research with
timestamps. **Labelled "External web research — analyst support only, not used by the screening decision"**: it never touches
`system/run`, `output/` or any decision. The Tavily key is held server-side only (function env `TAVILY_API_KEY`, or the private
`app_secrets` table that has RLS on and no policies); it is never in the page or repo.
Limits: same hit once per 60 s, 30 researches/min overall, 5 results; ~4% of hits (35/819) have no listed name in the reason text
(e.g. "no listed name" cases) and return a clear message instead of a search. Anyone with the page can trigger a search (demo only).
