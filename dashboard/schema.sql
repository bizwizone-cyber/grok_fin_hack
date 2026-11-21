-- Applied to Supabase project grok-fin-hack (uqqrzlonhbipdisgfeca) as migration "review_dashboard_schema".
create table public.runs (
  id uuid primary key default gen_random_uuid(),
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  git_commit text,
  data_label text,
  counts jsonb not null default '{}'::jsonb,
  runtime_s numeric
);
create table public.decisions (
  id bigint generated always as identity primary key,
  run_id uuid not null references public.runs(id) on delete cascade,
  hit_id text not null,
  alert_id text not null,
  decision text not null check (decision in ('SAME_ENTITY','DIFFERENT_ENTITY','REVIEW')),
  reason_code text not null,
  customer_record_id text,
  reason text,
  evidence jsonb not null default '[]'::jsonb,
  decided_at timestamptz not null default now(),
  unique (run_id, hit_id)
);
create table public.analyst_reviews (
  id bigint generated always as identity primary key,
  run_id uuid not null references public.runs(id) on delete cascade,
  hit_id text not null,
  analyst text not null check (char_length(analyst) between 1 and 80),
  decision text not null check (decision in ('SAME_ENTITY','DIFFERENT_ENTITY','REVIEW')),
  reason_code text not null check (reason_code in ('TEMPORAL_IMPOSSIBILITY','DECEASED','DOB_MISMATCH','LOCATION_MISMATCH','NAME_MISMATCH_VERIFIED','ENTITY_TYPE_MISMATCH','NAME_DOB_LOCATION_MATCH','NAME_DOB_MATCH','NAME_LOCATION_MATCH','INSUFFICIENT_DATA','AMBIGUOUS_CUSTOMER_RECORD')),
  note text check (note is null or char_length(note) <= 2000),
  reviewed_at timestamptz not null default now(),
  foreign key (run_id, hit_id) references public.decisions (run_id, hit_id) on delete cascade
);
create index decisions_decision_idx on public.decisions (decision);
create index decisions_reason_code_idx on public.decisions (reason_code);
create index decisions_decided_at_idx on public.decisions (decided_at);
create index decisions_run_alert_idx on public.decisions (run_id, alert_id);
create index runs_started_at_idx on public.runs (started_at desc);
create index analyst_reviews_run_hit_idx on public.analyst_reviews (run_id, hit_id);
create index analyst_reviews_reviewed_at_idx on public.analyst_reviews (reviewed_at desc);

alter table public.runs enable row level security;
alter table public.decisions enable row level security;
alter table public.analyst_reviews enable row level security;

-- DEMO ONLY: public read + public insert of analyst reviews (no login in the demo).
-- runs/decisions are written with the secret/service key (bypasses RLS).
revoke all on public.runs, public.decisions, public.analyst_reviews from anon, authenticated;
grant select on public.runs, public.decisions, public.analyst_reviews to anon;
grant insert on public.analyst_reviews to anon;
create policy demo_anon_select_runs on public.runs for select to anon using (true);
create policy demo_anon_select_decisions on public.decisions for select to anon using (true);
create policy demo_anon_select_reviews on public.analyst_reviews for select to anon using (true);
create policy demo_anon_insert_reviews on public.analyst_reviews for insert to anon with check (true);
