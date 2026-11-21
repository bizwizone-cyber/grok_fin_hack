# Review dashboard (Supabase) – demo tooling, not part of the scored entry point

## Quick start

**Live dashboard (no setup):** [open it here](https://bizwizone-cyber.github.io/grok_fin_hack/dashboard/index.html).
It reads the runs already loaded in our Supabase project. Pick a run, filter the hits, click a row to open its details
inline, use the amber **Review this hit** button to log an analyst review, or **Research with Tavily** for web context.

**Run it locally:**
```
git clone https://github.com/bizwizone-cyber/grok_fin_hack.git && cd grok_fin_hack
python3 -m http.server 8765 --directory dashboard
# open http://localhost:8765/index.html
```

**Load a new run into the dashboard** (needs the project's secret key, kept local, never committed):
```
cp dashboard/.env.example dashboard/.env        # paste SUPABASE_KEY (Project Settings > API Keys > secret)
python3 dashboard/push_run.py --data <path to dev folder> --label dev
```
Refresh the page and the new run appears in the run selector with its date, time, commit and runtime.

**Set it up in your own Supabase project** (only if not using ours):
1. Run `dashboard/schema.sql` in the SQL editor.
2. Deploy the function: `supabase functions deploy tavily-research --no-verify-jwt --project-ref <ref>`
   (source in `dashboard/functions/tavily-research/index.ts`).
3. Add the secret `TAVILY_API_KEY` under Edge Functions > Secrets (a key from app.tavily.com, starts with `tvly-`).
4. Put your project URL and publishable key at the top of `dashboard/index.html`.

**Rules:** the dashboard and Tavily never change a decision. `system/run` stays standard-library only and offline.
Web research is shown to the analyst as context; only a human review records a different outcome.

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
