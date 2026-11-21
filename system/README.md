# Screening entity resolution

`system/run` is a single Python 3 executable. It uses only the standard library and makes no model calls. Decisions follow `POLICY.md` exactly and are deterministic.

```
system/run <data_folder> <output_file>
```

A run on the development set (819 hits, ~250k supporting rows) takes about **16 seconds** on this machine and costs **$0**.

## Architecture

```mermaid
flowchart TD
  A[Alert] --> B[Resolve the customer]
  B --> B1[Follow account_ref, login_handle or email]
  B1 --> B2{Link leads to more than one person?}
  B2 -->|yes| B3[Screened name, DOB, country and city pick between them]
  B2 -->|no| B4[Use the linked records]
  B --> B5[Expand the same person]
  B5 --> B6[Phone + full DOB, or same approved document number, transitively]
  B6 --> B7[Most recent approved KYC check: provider or onboarding note]
  A --> C[Resolve the listed person]
  C --> C1[Structured fields: names, aliases, dobs, countries, type, death]
  C --> C2[Age-at-date in the first paragraph of profile notes]
  C --> C3[Search results only if they use the listing phrase in a country tied to the hit, and only when no DOB was found above]
  C --> C4[Dated reports: 'According to …; Month D, YYYY:']
  B7 --> D[Decision rules from POLICY.md]
  C1 --> D
  C2 --> D
  C3 --> D
  C4 --> D
  D --> E1[DIFFERENT_ENTITY on a hard contradiction]
  D --> E2[SAME_ENTITY on agreement and no contradiction]
  D --> E3[REVIEW otherwise]
```

The comparison is Splink-inspired and stdlib-only: each field (name, date of birth, country and entity type) becomes an explicit `agree`, `contradict` or `unknown` comparison level. A small fixed-weight Fellegi–Sunter-style score then gates confirmations: DOB agreement is stronger than a name match and country agreement is corroboration. The score never authorises a clearance; only a hard contradiction in `POLICY.md` can do that. Name agreement itself uses the edit-distance and variant rules in principle 8, not a similarity score.

## How we find the customer

1. Follow whichever link the alert carries: `account_ref` → `accounts.account_id` (including joint holders), `login_handle` → `accounts.login_handle`, or `email` → `customers.email`.
2. If that link leads to more than one person, the screened name, date of birth, country and city pick between them (principle 2). Without a link, those same screened details are used to search the system of record. We never use another customer's records.
3. Records that share a phone number **and** the same full date of birth, or the same document number on approved identity checks, belong to one person, transitively (principle 3). A shared phone or address alone does not merge them.
4. The most recent **approved** identity check of that person supplies the verified legal name, date of birth and document country, whether it sits in `kyc_checks` or only in an onboarding note. Rejected and expired checks are ignored. A KYC-team note counts as a passed check unless it records a failure, so the failure wording is deliberately wide: rejected, failed, declined, inconclusive, on hold, awaiting, sent for review, resubmit, unreadable, expired or past expiry, suspected fraud or tampering, "could not verify", "did not match" and similar. Words that also appear in passed notes ("expiry 2031", "no signs of fraud") only count inside a phrase that states a failure. A failed check misread as passed could clear a real match, so this errs towards ignoring the note. Verified values beat declared ones.
5. Countries tied to the customer are residence and nationality on all of their records, the approved identity-document country, and places the notes say they lived. In free text a country name may be followed by any punctuation, so "passport (Bangladesh)" and "in Austria: 2008" both count.
6. If more than one person still fits the alert, we decide the hit for each. If they all lead to the same decision, we give it; otherwise the decision is `REVIEW` with `AMBIGUOUS_CUSTOMER_RECORD`. Either way `customer_record_id` is `AMBIGUOUS`. When one person has several records, any of them is accepted.

## How we find the listed person

- Names: `name`, aliases, and `best_matching_name`.
- Dates of birth: the `dobs` field; if it is empty, an age at a date in the **first paragraph** of `profile_notes` (someone aged 45 in March 2019 was born in 1973 or 1974); if still empty, web search results for the name. A search result is used only when it describes the same kind of listing in a country tied to the hit. The phrases are those in principle 5 ("a sanctioned individual", "a politically exposed person", and so on).
- Countries: `countries`, `nationality`, `citizenship`, the last part of each address, and `place_of_birth`. "International" and "Europe" are not countries. Codes and nationality adjectives map to the same country as the full name; territories stay their own country. Official long forms are listed whole, so "Korea, Democratic People's Republic of" is North Korea and "Virgin Islands, British" is the British Virgin Islands, not the United Kingdom.
- **Unreadable countries never clear a hit.** If either side has a country value the system cannot map (a spelling it has not seen), that value might be the country both share. The hit is then neither cleared nor confirmed on location; it goes to `REVIEW`, and the reason names the value that could not be read. Missing a country can only cost a review (3 points), never an unsafe clearance (60).
- Dated reports are exactly the lines `According to …; September 10, 2025: …` (day optional, as in `June 2014:`). Other wordings do not count.
- Date of death: `deceased_date`, or a death date written in the profile.
- For companies, only the `dobs` field is an incorporation date.

Name parts are compared as in principle 8: case, accents, punctuation and brackets are ignored; initials and placeholders are not parts; company legal forms from `name_variants.json` are not parts; two parts match if they are identical, share a variant group, are written together or apart, or differ by at most 1 edit (4–6 letters) or 2 edits (longer); names agree when at least two parts match, or every part of the shorter name matches. An individual customer whose name has fewer than two real parts is neither confirmed nor cleared on name.

## Evidence

`evidence` lists only the record IDs that decided the hit, as they appear in the files: the customer record or records, the approved check (`KYC-…`) or onboarding note (`NOTE-…`) that supplied the verified facts, a lived-in note when location used it, and a search result (`SR-…`) only when it supplied a date of birth. The reason is two to four sentences that name this hit's own facts and, on `REVIEW`, what is missing.

## Determinism and models

- Hits are written in sorted `hit_id` order.
- Sets of records are sorted by ID. "Most recent" checks are ordered by date, then ID.
- There is no randomness and no clock. The only dates used are those in the data (for example `alerts.screened_at`).
- No language model and no OpenRouter call. There is no API cost, and a second run is byte-identical to the first (confirmed on the development set).
- The local comparison levels and fixed weights replace a dependency on Splink: there is no training pass, random seed, or external data. They can only keep an uncertain hit in `REVIEW`; they cannot turn it into `DIFFERENT_ENTITY`.

## Three example cases

### A clear mismatch — `H-13E1CF` (`TEMPORAL_IMPOSSIBILITY`)

The customer, Abdul Rahman Yasin (`CUS-364225`, with a second record `CUS-851234` of the same person), has a verified date of birth of 2007-08-21 (`KYC-358C76`). The listed profile's earliest dated report is "According to the thenews.com.pk; January 08, 2020: …". He was at most 12 at that date, so the hit is a different person.

### A match — `H-582410` (`NAME_DOB_LOCATION_MATCH`)

The alert's account has two joint holders; the screened name and date of birth fit `CUS-377745`, Elvira Velasco Morillo. An onboarding note (`NOTE-14E25A`) verifies her date of birth as 1968-03-05. The listed profile gives birth years 1967 or 1968 (from "a 30-year-old" in November 1998). Name and date of birth agree, both sides are linked to Spain, and nothing contradicts them.

### Left for review — `H-CDEC83` (`INSUFFICIENT_DATA`, `customer_record_id` `AMBIGUOUS`)

No alert link. A search on the screened name, year of birth and country finds two people (records `CUS-037213`, `CUS-565167`, `CUS-892985`). Decided separately, each leads to the same outcome: the name agrees with Juan González and both sides are linked to Panama, but the hit has no usable date of birth, so the hit can be neither cleared nor confirmed. Because every person gives the same decision, that decision is returned, with `customer_record_id` `AMBIGUOUS`.

## How to score locally

```
system/run dev output/dev.jsonl
python3 validate.py --data dev --output output/dev.jsonl --score dev/practice_labels.jsonl
```

On the 50 practice labels this system scores **100.0**, with **0 unsafe clearances**, and every right decision has an accepted reason code and customer record.
