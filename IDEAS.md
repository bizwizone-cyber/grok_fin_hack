# Ideas: Track 2, sanctions screening

Rule for all ideas: `system/run` must stay deterministic and use only the data folder
(models only through OpenRouter). Ideas that use the internet or Grok Bot go in the
demo / analyst layer, not in `system/run`.

## Team ideas

### 1. Multiple agents, each checking one thing (LLM as sensor, rules as judge)
- One agent for each policy check: Customer resolver, Name, Date of birth, Location,
  Time (age / incorporation / death), Entity type.
- Each agent returns: agrees / contradicts / no data, plus the record IDs it used.
- An agent is a sensor, not a judge. It answers small fact questions only, for example
  "Does this note state an approved ID check?" or "Which date of birth does it give?".
  It never returns SAME / DIFFERENT / REVIEW.
- A coordinator combines the results with the POLICY.md decision order. The coordinator
  is rule code, not an LLM. Only the coordinator decides.
- Show each agent's result as one row in the analyst view (scorecard).
- Grok Bot option: each check is a separate Bot, and they hand off in one Slack thread.
- Source: PolicyGuard (June 2026), https://arxiv.org/abs/2606.32004

### 2. LLM checks the internet for PEPs and other important figures
- For REVIEW hits with missing facts (often no date of birth), an agent opens the
  hit's `source_urls` and searches the web (Grok Bot computer or Tavily).
- It looks for the missing fact, for example "aged 52 in 2019" or "born 1971".
- It only SUGGESTS. It never changes a decision. A human confirms, with the URL as evidence.
- Demo layer only: the brief says `system/run` reads only the data folder.

### 3. AI must use evidence when it makes decisions (reduce hallucinations)
- Code retrieves the exact evidence with joins (account -> customer -> KYC / notes -> hit).
- The LLM gets only that evidence pack and POLICY.md.
- It must cite a record ID (CUS-, KYC-, NOTE-, SR-, H-) for every fact.
- Code audits the answer: each cited ID exists, each quoted value is in that record,
  and the decision agrees with the rule engine. Any failure -> REVIEW.
- Verbatim quote gate: for each fact, the LLM must return the exact text it used.
  Code checks that this text is in the record, character by character.
  No match -> the fact is not used -> the hit stays REVIEW.
- Limit of the gate: it proves that the text exists. It does not prove that the model
  read the text correctly.
- One-way safety valve: the LLM can make a decision more careful
  (DIFFERENT -> REVIEW), never less careful.
- Sources: Verifiable by Construction (2026), https://arxiv.org/pdf/2609.15964 ;
  ACL-Verbatim (2026), https://arxiv.org/abs/2605.21102

### 4. Cascade: rules first, LLM only for the hard cases
- The rule engine decides the easy cases. It uses no model.
- The LLM gets only the hard cases: free text that looks like an ID check or a place
  lived, but matches no regex. This is the risk from new wording in the unseen set.
- In the cascade, the LLM is a sensor (idea 1) and each fact must pass the verbatim
  quote gate (idea 3).
- Evidence: a Federal Reserve study (Sept 2025) reports about 92% fewer false positives
  with LLMs than with the best fuzzy matcher. Its cascade is about twice as fast as an
  LLM-only system.
- Conditions for `system/run`: temperature 0, the same output in five runs, and a
  correct run with no API key (then the hard cases stay REVIEW).
- Source: https://www.federalreserve.gov/econres/feds/can-llms-improve-sanctions-screening-in-the-financial-system-evidence-from-a-fuzzy-matching-assessment.htm
