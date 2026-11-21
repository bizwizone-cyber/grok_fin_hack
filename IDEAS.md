# Ideas: Track 2, sanctions screening

Rule for all ideas: `system/run` must stay deterministic and use only the data folder
(models only through OpenRouter). Ideas that use the internet or Grok Bot go in the
demo / analyst layer, not in `system/run`.

## Team ideas

### 1. Multiple agents, each checking one thing
- One agent for each policy check: Customer resolver, Name, Date of birth, Location,
  Time (age / incorporation / death), Entity type.
- Each agent returns: agrees / contradicts / no data, plus the record IDs it used.
- A coordinator combines the results with the POLICY.md decision order.
- Show each agent's result as one row in the analyst view (scorecard).
- Grok Bot option: each check is a separate Bot, and they hand off in one Slack thread.

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
- One-way safety valve: the LLM can make a decision more careful
  (DIFFERENT -> REVIEW), never less careful.
