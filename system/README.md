# Screening decision engine

## Architecture

```text
dev/*.jsonl ──> data_pipeline.load_data_store
     │                    │
alerts + hits ──> prepare_hit_context
                          │
name_variants.json ────────┴──> decision_engine.evaluate_context
                                      │
                                      └──> one deterministic JSONL result per hit
```

`run <data_folder> <output_file>` loads every source table dynamically, resolves the customer from links or screened facts, clusters the customer's related records, extracts structured and free-text hit facts, and then applies the SOP gates. Customer resolution uses account/login/email links, name/DOB/country/city matching for unlinked alerts, exact phone-plus-full-DOB clustering, and approved-document clustering. Hit extraction covers aliases, profile-note DOBs, age-at-date years, dates of death, report dates, countries, and tightly filtered search-result DOBs.

## Decision and evidence rules

The engine checks ambiguity first, then entity type, death and timeline, DOB contradiction, verified-name contradiction, country contradiction, and finally the positive confirmation rules. A person is confirmed only when name and DOB agree without a contradiction; a company needs name and country agreement. Unknown fields stay unknown. In particular, a country conflict cannot clear a hit when both name and DOB agree; it returns `REVIEW`.

Each output contains the hit and alert IDs, one policy reason code, resolved customer record ID, a concise explanation, and at most two evidence IDs. The IDs point to the customer or source record and the hit/profile record that decided the outcome; intermediate account and onboarding links are not cited. Ambiguous identities use `customer_record_id: "AMBIGUOUS"`.

## Determinism, runtime, and cost

The implementation uses only Python 3 standard-library modules and makes no network calls or model calls. It iterates in input order, uses stable JSON serialization, stable tie-breaks on record IDs, and has no random or time-dependent decisions. Cost is `$0`. The checked development run emits all 819 hits; `validate.py --score` reports 50/50 correct practice decisions, a 100.0 decision score, and zero unsafe clearances. The implementation is designed to produce byte-stable JSONL for identical inputs and runtime versions; the explicit two-run comparison remains to be completed.

## Case walkthroughs

### DIFFERENT_ENTITY — H-13E1CF

The practice label reports a verified customer birth year of 2007 and a dated hit-profile activity on 2020-01-08. That makes the customer at most 13 at the reported event, below the SOP's minimum age of 16. The timeline alone is an airtight contradiction, so the appropriate outcome is `DIFFERENT_ENTITY` with `TEMPORAL_IMPOSSIBILITY`; the country conflict is additional evidence, not needed to clear.

### SAME_ENTITY — H-582410

The practice label says the customer's DOB is 1968-03-05 while the hit gives possible years 1967 or 1968. One hit date agrees within the SOP's one-year tolerance; both sides are tied to Spain and the names agree. No contradiction remains, so this is `SAME_ENTITY` with `NAME_DOB_LOCATION_MATCH` (or the accepted `NAME_DOB_MATCH` code).

### REVIEW — H-FF3096

The customer's and hit's names agree and both are tied to the United States, but the hit has no usable DOB to compare. There is no contradiction to justify clearance and not enough information to confirm identity. Keep the hit at `REVIEW` with `INSUFFICIENT_DATA`.
