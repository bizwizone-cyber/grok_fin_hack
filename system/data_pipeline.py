"""Deterministic data loading, entity resolution, and hit-fact extraction."""

import calendar
import json
import os
import re
import unicodedata
from datetime import datetime

from decision_engine import names_agree


MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}

COUNTRIES = {
    "ar": "argentina", "at": "austria", "au": "australia", "bd": "bangladesh",
    "be": "belgium", "br": "brazil", "ca": "canada", "ch": "switzerland",
    "ci": "cote d ivoire", "cl": "chile", "cn": "china", "co": "colombia",
    "cz": "czech republic", "de": "germany", "dk": "denmark", "do": "dominican republic",
    "ec": "ecuador", "es": "spain", "fi": "finland", "fr": "france", "gb": "united kingdom",
    "gh": "ghana", "gr": "greece", "hu": "hungary", "id": "indonesia", "ie": "ireland",
    "in": "india", "it": "italy", "jm": "jamaica", "jp": "japan", "ke": "kenya",
    "kr": "south korea", "lb": "lebanon", "ma": "morocco", "md": "moldova", "me": "montenegro",
    "mx": "mexico", "my": "malaysia", "ng": "nigeria", "nl": "netherlands", "no": "norway",
    "nz": "new zealand", "pa": "panama", "pe": "peru", "pk": "pakistan", "pl": "poland",
    "pt": "portugal", "ro": "romania", "rs": "serbia", "ru": "russia", "se": "sweden",
    "sg": "singapore", "si": "slovenia", "sy": "syria", "th": "thailand", "tr": "turkey",
    "ua": "ukraine", "us": "united states", "uy": "uruguay", "vn": "vietnam", "za": "south africa",
    "ae": "united arab emirates", "il": "israel", "al": "algeria", "am": "armenia", "ht": "haiti",
    "kp": "north korea",
}

NATIONALITIES = {
    "american": "united states", "argentine": "argentina", "australian": "australia",
    "austrian": "austria", "bangladeshi": "bangladesh", "brazilian": "brazil",
    "british": "united kingdom", "canadian": "canada", "chinese": "china", "colombian": "colombia",
    "danish": "denmark", "dutch": "netherlands", "ecuadorean": "ecuador", "french": "france",
    "german": "germany", "ghanaian": "ghana", "hungarian": "hungary", "indian": "india",
    "indonesian": "indonesia", "irish": "ireland", "italian": "italy", "jamaican": "jamaica",
    "japanese": "japan", "kenyan": "kenya", "mexican": "mexico", "nigerian": "nigeria",
    "norwegian": "norway", "pakistani": "pakistan", "polish": "poland", "portuguese": "portugal",
    "romanian": "romania", "russian": "russia", "serbian": "serbia", "singaporean": "singapore",
    "slovenian": "slovenia", "south african": "south africa", "spanish": "spain", "swedish": "sweden",
    "swiss": "switzerland", "thai": "thailand", "turkish": "turkey", "ukrainian": "ukraine",
    "vietnamese": "vietnamese",
}

NON_COUNTRIES = {"", "na", "n a", "unknown", "international", "europe", "worldwide", "global"}


def _read_jsonl(path):
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _fold(value):
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(char for char in text if not unicodedata.combining(char))


def _norm_space(value):
    return re.sub(r"\s+", " ", _fold(value)).strip()


def _unique(values):
    result, seen = [], set()
    for value in values:
        if value is not None and str(value).strip() and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _strings(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        result = []
        for item in value:
            if isinstance(item, str) and item.strip():
                result.append(item)
        return result
    return []


def _entity_type(value):
    return "company" if "company" in _fold(value) or "corpor" in _fold(value) else "person"


def _normalise_date(value):
    """Return YYYY, YYYY-MM, or YYYY-MM-DD. Invalid strings remain unknown."""
    text = str(value or "").strip()
    if not text:
        return None
    text = re.sub(r"\s+", " ", text)
    match = re.fullmatch(r"(\d{4})", text)
    if match:
        return match.group(1)
    match = re.fullmatch(r"(\d{4})[-/](\d{1,2})", text)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        return "%04d-%02d" % (year, month) if 1 <= month <= 12 else None
    match = re.fullmatch(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if match:
        year, month, day = map(int, match.groups())
        try:
            datetime(year, month, day)
            return "%04d-%02d-%02d" % (year, month, day)
        except ValueError:
            return None
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if match:
        day, month, year = map(int, match.groups())
        try:
            datetime(year, month, day)
            return "%04d-%02d-%02d" % (year, month, day)
        except ValueError:
            return None
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    match = re.fullmatch(r"([A-Za-z]+)\s+(\d{4})", text)
    if match and _fold(match.group(1)) in MONTHS:
        return "%04d-%02d" % (int(match.group(2)), MONTHS[_fold(match.group(1))])
    return None


def _date_parts(value):
    norm = _normalise_date(value)
    if not norm:
        return None
    parts = [int(piece) for piece in norm.split("-")]
    return parts[0], (parts[1] if len(parts) > 1 else None), (parts[2] if len(parts) > 2 else None)


def _full_date(value):
    norm = _normalise_date(value)
    return norm if norm and len(norm) == 10 else None


def _dates_compatible(a, b):
    left, right = _date_parts(a), _date_parts(b)
    if not left or not right:
        return True
    for lpart, rpart in zip(left, right):
        if lpart is not None and rpart is not None and lpart != rpart:
            return False
    return True


def _canonical_country(value):
    if value is None:
        return None
    text = str(value).rsplit(",", 1)[-1]
    text = re.sub(r"\[[^]]*\]", "", text)
    text = re.sub(r"\([^)]*\)", "", text)
    text = _norm_space(text).replace("’", "'")
    text = re.sub(r"^the\s+", "", text)
    text = text.replace("cote d'ivoire", "cote d ivoire").replace("cote divoire", "cote d ivoire")
    if text in NON_COUNTRIES:
        return None
    return COUNTRIES.get(text, NATIONALITIES.get(text, text))


def _country_values(values):
    return _unique([country for value in _strings(values) if (country := _canonical_country(value))])


def _phone(value):
    value = re.sub(r"\D", "", str(value or ""))
    return value if len(value) >= 7 else None


def _name_tokens(value):
    value = re.sub(r"\([^)]*\)|\[[^]]*\]|\{[^}]*\}", " ", _fold(value))
    return [part for part in re.findall(r"[\w]+", value, flags=re.UNICODE) if len(part) > 1 and not part.isdigit()]


def _variant_lookup(variants):
    lookup = {}
    for group in variants.get("groups", []):
        canonical = _fold(group[0])
        for name in group:
            lookup[_fold(name)] = canonical
    return lookup


def _name_key(value, variants):
    forms = {_fold(form) for form in variants.get("company_legal_forms", [])}
    replacements = _variant_lookup(variants)
    parts = [replacements.get(part, part) for part in _name_tokens(value) if part not in forms]
    return " ".join(sorted(set(parts)))


def _search_query(value):
    return " ".join(sorted(_name_tokens(value)))


class _UnionFind:
    def __init__(self, items):
        self.parent = {item: item for item in items}

    def find(self, item):
        parent = self.parent[item]
        while parent != self.parent[parent]:
            self.parent[parent] = self.parent[self.parent[parent]]
            parent = self.parent[parent]
        self.parent[item] = parent
        return parent

    def union(self, first, second):
        first, second = self.find(first), self.find(second)
        if first != second:
            if first < second:
                self.parent[second] = first
            else:
                self.parent[first] = second


def _latest_approved(checks):
    approved = [check for check in checks if _fold(check.get("result")) == "approved"]
    if not approved:
        return None
    return max(approved, key=lambda check: (str(check.get("checked_at") or ""), str(check.get("check_id") or "")))


def load_data_store(data_folder):
    """Read the source folder once and create deterministic indexes."""
    tables = {}
    for name in ("alerts", "hits", "customers", "accounts", "onboarding", "kyc_checks", "notes", "search_results"):
        tables[name] = _read_jsonl(os.path.join(data_folder, name + ".jsonl"))
    with open(os.path.join(data_folder, "name_variants.json"), encoding="utf-8") as handle:
        variants = json.load(handle)

    customers = {row["record_id"]: row for row in tables["customers"]}
    customers_by_email, accounts_by_id, accounts_by_login = {}, {}, {}
    customer_accounts, customer_phones = {}, {}
    for customer in tables["customers"]:
        record_id = customer["record_id"]
        customer_accounts[record_id] = []
        customer_phones[record_id] = [_phone(customer.get("phone"))]
        email = _norm_space(customer.get("email"))
        if email:
            customers_by_email.setdefault(email, []).append(record_id)
    for account in tables["accounts"]:
        customer_id = account.get("customer_id")
        if customer_id not in customers:
            continue
        accounts_by_id.setdefault(account.get("account_id"), []).append(account)
        login = _norm_space(account.get("login_handle"))
        if login:
            accounts_by_login.setdefault(login, []).append(account)
        customer_accounts[customer_id].append(account)
        customer_phones[customer_id].append(_phone(account.get("phone")))

    onboarding_by_pair, applicants_by_customer, customers_by_applicant = {}, {}, {}
    for row in tables["onboarding"]:
        key = (row.get("account_id"), row.get("customer_id"))
        onboarding_by_pair[key] = row
        customer_id, applicant = row.get("customer_id"), row.get("kyc_applicant_id")
        if customer_id in customers and applicant:
            applicants_by_customer.setdefault(customer_id, []).append(applicant)
            customers_by_applicant.setdefault(applicant, []).append(customer_id)

    checks_by_applicant = {}
    for check in tables["kyc_checks"]:
        checks_by_applicant.setdefault(check.get("applicant_id"), []).append(check)
    notes_by_email = {}
    for note in tables["notes"]:
        email = _norm_space(note.get("requester_email"))
        if email:
            notes_by_email.setdefault(email, []).append(note)
    search_by_query = {}
    for result in tables["search_results"]:
        search_by_query.setdefault(_norm_space(result.get("query")), []).append(result)

    name_index, token_index = {}, {}
    for customer in tables["customers"]:
        record_id = customer["record_id"]
        for name in (customer.get("full_name"), customer.get("preferred_name"), customer.get("login_name")):
            key = _name_key(name, variants)
            if key:
                name_index.setdefault(key, []).append(record_id)
            for token in set(_name_tokens(name)):
                if len(token) >= 3:
                    token_index.setdefault(token, []).append(record_id)

    uf = _UnionFind(customers)
    by_phone_dob = {}
    for record_id, customer in customers.items():
        dob = _full_date(customer.get("dob"))
        if not dob:
            continue
        for phone in _unique(customer_phones[record_id]):
            if phone:
                by_phone_dob.setdefault((phone, dob), []).append(record_id)
    for record_ids in by_phone_dob.values():
        for record_id in record_ids[1:]:
            uf.union(record_ids[0], record_id)
    by_document = {}
    for applicant, checks in checks_by_applicant.items():
        linked_customers = customers_by_applicant.get(applicant, [])
        if not linked_customers:
            continue
        for check in checks:
            if _fold(check.get("result")) != "approved":
                continue
            document = re.sub(r"\W", "", _fold(check.get("document_number")))
            if document:
                by_document.setdefault(document, []).extend(linked_customers)
    for record_ids in by_document.values():
        record_ids = _unique(record_ids)
        for record_id in record_ids[1:]:
            uf.union(record_ids[0], record_id)
    clusters = {}
    for record_id in customers:
        clusters.setdefault(uf.find(record_id), []).append(record_id)
    for members in clusters.values():
        members.sort()

    return {
        "data_folder": data_folder, "variants": variants, "customers": customers,
        "alerts": {row["alert_id"]: row for row in tables["alerts"]},
        "hits": {row["hit_id"]: row for row in tables["hits"]},
        "customers_by_email": customers_by_email, "accounts_by_id": accounts_by_id,
        "accounts_by_login": accounts_by_login, "customer_accounts": customer_accounts,
        "onboarding_by_pair": onboarding_by_pair, "applicants_by_customer": applicants_by_customer,
        "checks_by_applicant": checks_by_applicant, "notes_by_email": notes_by_email,
        "search_by_query": search_by_query, "name_index": name_index, "token_index": token_index,
        "union_find": uf, "clusters": clusters, "customer_fact_cache": {}, "hit_fact_cache": {},
        "alert_candidate_cache": {},
    }


def _customer_names(customer):
    return _unique([customer.get("full_name"), customer.get("preferred_name"), customer.get("login_name")])


def _customer_countries(customer):
    return _country_values([customer.get("nationality"), customer.get("residence_country")])


def _city_matches(screened_city, customer):
    city = _norm_space(screened_city)
    if not city:
        return False
    return city == _norm_space(customer.get("city")) or city in _norm_space(customer.get("address"))


def _fit_score(alert, customer, variants):
    screened = alert.get("customer") or {}
    if _entity_type(screened.get("entity_type")) != _entity_type(customer.get("entity_type")):
        return -100
    score = 0
    if screened.get("name") and names_agree([screened["name"]], _customer_names(customer), variants, customer.get("entity_type")):
        score += 4
    if screened.get("dob"):
        if not _dates_compatible(screened.get("dob"), customer.get("dob")):
            return -100
        if _date_parts(customer.get("dob")):
            score += 3
    screened_country = _canonical_country(screened.get("country"))
    if screened_country:
        countries = _customer_countries(customer)
        if countries and screened_country not in countries:
            return -100
        if screened_country in countries:
            score += 2
    if screened.get("city"):
        if _city_matches(screened["city"], customer):
            score += 1
        elif customer.get("city") or customer.get("address"):
            return -100
    return score


def _linked_candidates(alert, store):
    found = []
    for account in store["accounts_by_id"].get(alert.get("account_ref"), []):
        found.append(account.get("customer_id"))
    for account in store["accounts_by_login"].get(_norm_space(alert.get("login_handle")), []):
        found.append(account.get("customer_id"))
    found.extend(store["customers_by_email"].get(_norm_space(alert.get("email")), []))
    found = _unique(found)
    if len(found) <= 1:
        return found
    scores = {record_id: _fit_score(alert, store["customers"][record_id], store["variants"]) for record_id in found}
    valid = [record_id for record_id in found if scores[record_id] >= 0]
    if valid and max(scores[record_id] for record_id in valid) > 0:
        high = max(scores[record_id] for record_id in valid)
        return [record_id for record_id in valid if scores[record_id] == high]
    return valid or found


def _unlinked_candidates(alert, store):
    screened = alert.get("customer") or {}
    name = screened.get("name")
    if not name:
        return []
    key = _name_key(name, store["variants"])
    candidates = list(store["name_index"].get(key, []))
    if not candidates:
        buckets = [set(store["token_index"].get(token, [])) for token in _name_tokens(name) if len(token) >= 3]
        buckets = [bucket for bucket in buckets if bucket]
        if len(buckets) > 1:
            candidates = list(set.intersection(*buckets))
        elif buckets:
            candidates = list(buckets[0])
    result = []
    for record_id in candidates:
        customer = store["customers"][record_id]
        if _fit_score(alert, customer, store["variants"]) >= 4:
            result.append(record_id)
    return sorted(_unique(result))


def _parse_kyc_note(note):
    text = str(note.get("text") or "")
    if _fold(note.get("channel")) != "kyc":
        return None
    name = None
    name_patterns = (
        r"\bidentifying\s+(?:the\s+)?holder\s+as\s+([^,.(]+?)(?=\s*(?:,|\.|\bDOB\b|\bborn\b))",
        r"\b(?:identity verification for|(?:passport|national id|driving licen[cs]e|id)\s+(?:from\s+[A-Za-z '\-]+\s+)?(?:for|of))\s+([A-Z][A-Z '\-]+?)(?=\s*(?:\(|,|\bon\s+\d{4}|\.))",
        r"\bname\s+([A-Z][A-Z '\-]+?)(?=\s*(?:,|\.|\bDOB\b|\bborn\b))",
        r"\bvalidated\s+([A-Z][A-Z '\-]+?)(?=\s+on\s+\d{4})",
    )
    for pattern in name_patterns:
        match = re.search(pattern, text, re.I)
        if match:
            name = match.group(1).strip(" ,.")
            break
    dob = None
    match = re.search(r"\b(?:born|dob|birth date(?:\s+of)?)\s*[:]?\s*([^,.]+(?:,\s*\d{4})?)", text, re.I)
    if match:
        dob = _normalise_date(match.group(1).strip())
    if not dob:
        match = re.search(r"\(\s*(\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{1,2}-\d{1,2})\s*\)", text)
        if match:
            dob = _normalise_date(match.group(1))
    country = None
    match = re.search(r"\b(?:passport\s+from|issued in|originating from|documentation is from|national id from|driving licen[cs]e from|from)\s+([A-Za-z][A-Za-z '\-]+?)(?:\s+(?:on\s+\d{4}|for\b)|\.|,|$)", text, re.I)
    if match:
        country = _canonical_country(match.group(1))
    if not name and not dob and not country:
        return None
    return {"name": name, "dob": dob, "country": country, "note_id": note.get("note_id"), "date": note.get("created_at")}


def _cluster_facts(members, representative, store):
    cache_key = (tuple(members), representative)
    cached = store["customer_fact_cache"].get(cache_key)
    if cached is not None:
        return cached
    customers = [store["customers"][record_id] for record_id in members]
    checks = []
    notes = []
    for customer in customers:
        for applicant in store["applicants_by_customer"].get(customer["record_id"], []):
            checks.extend(store["checks_by_applicant"].get(applicant, []))
        notes.extend(store["notes_by_email"].get(_norm_space(customer.get("email")), []))
    approved = _latest_approved(checks)
    note_kycs = [parsed for note in notes if (parsed := _parse_kyc_note(note))]
    latest_note = max(note_kycs, key=lambda item: (str(item.get("date") or ""), str(item.get("note_id") or "")), default=None)
    verified_name = approved.get("verified_name") if approved else (latest_note or {}).get("name")
    verified_dob = _normalise_date(approved.get("verified_dob")) if approved else (latest_note or {}).get("dob")
    verified_source = approved.get("check_id") if approved else (latest_note or {}).get("note_id")
    country_values = []
    country_sources = []
    for customer in customers:
        country_values.extend(_customer_countries(customer))
        if _customer_countries(customer):
            country_sources.append(customer["record_id"])
    if approved and (country := _canonical_country(approved.get("document_country"))):
        country_values.append(country)
        country_sources.append(approved.get("check_id"))
    elif latest_note and latest_note.get("country"):
        country_values.append(latest_note["country"])
        country_sources.append(latest_note.get("note_id"))
    for note in notes:
        text = str(note.get("text") or "")
        for match in re.finditer(r"\b(?:living(?:\s+and\s+working)?|lived(?:\s+and\s+worked)?|residency|residence|resided|resides|home\s+and\s+(?:a\s+)?job)\b[^.\n]{0,70}?\bin\s+([A-Za-z][A-Za-z '\-]+?)(?=\s+(?:from|to|between|throughout|until)\b|[.,;]|$)", text, re.I):
            if country := _canonical_country(match.group(1)):
                country_values.append(country)
                country_sources.append(note.get("note_id"))
    declared_dob = _normalise_date(store["customers"][representative].get("dob"))
    names = []
    if verified_name:
        names.append(verified_name)
    for customer in customers:
        names.extend(_customer_names(customer))
    source = verified_source or representative
    facts = {
        "names": _unique(names), "verified_legal_name": verified_name,
        "verified_name_source_id": verified_source, "dob": verified_dob or declared_dob,
        "dob_source_id": verified_source or representative, "countries": _unique(country_values),
        "country_source_id": next((source_id for source_id in country_sources if source_id), representative),
        "entity_type": _entity_type(store["customers"][representative].get("entity_type")),
        "source_id": source,
    }
    store["customer_fact_cache"][cache_key] = facts
    return facts


def _extract_age_years(text):
    result = []
    expressions = (
        r"\b(?:aged|age(?:\s+of)?|at\s+the\s+age\s+of)\s*(\d{1,3})\b[^.\n]{0,120}?\b([A-Za-z]+)\s+(?:\d{1,2},?\s+)?(\d{4})\b",
        r"\b(\d{1,3})\s*(?:years? old|year-old)\b[^.\n]{0,120}?\b([A-Za-z]+)\s+(?:\d{1,2},?\s+)?(\d{4})\b",
        r"\b([A-Za-z]+)\s+(?:\d{1,2},?\s+)?(\d{4})\b[^.\n]{0,120}?\b(?:aged|age(?:\s+of)?|at\s+the\s+age\s+of|was|is|a)\s*(\d{1,3})(?:\s*(?:years? old|year-old))?\b",
        r"\b,\s*(\d{1,3})\s*,[^.\n]{0,120}?\b([A-Za-z]+)\s+(?:\d{1,2},?\s+)?(\d{4})\b",
        r"\b([A-Za-z]+)\s+(?:\d{1,2},?\s+)?(\d{4})\b[^.\n]{0,120}?\b(\d{1,3})\s*(?:years? old|year-old)\b",
        r"\b([A-Za-z]+)\s+(?:\d{1,2},?\s+)?(\d{4})\b[^.\n]{0,120}?\b,\s*(\d{1,3})\s*,",
    )
    for expression in expressions:
        for groups in re.findall(expression, text, flags=re.I):
            age, month, year = groups
            if _fold(month) not in MONTHS and _fold(groups[0]) in MONTHS:
                month, year, age = groups
            if _fold(month) in MONTHS and 0 < int(age) < 120:
                result.extend([str(int(year) - int(age) - 1), str(int(year) - int(age))])
    return _unique(result)


def _birth_dates_from_notes(text):
    first = str(text or "").split("\n\n", 1)[0]
    values = []
    date_pattern = r"(\d{4}[-/]\d{1,2}(?:[-/]\d{1,2})?|\d{1,2}/\d{1,2}/\d{4}|[A-Za-z]+\s+\d{1,2},?\s+\d{4}|\d{1,2}\s+[A-Za-z]+\s+\d{4}|\d{4})"
    for match in re.finditer(r"\b(?:born|birth(?:date)?|date of birth|dob)\b\D{0,18}" + date_pattern, first, re.I):
        if normalized := _normalise_date(match.group(1)):
            values.append(normalized)
    values.extend(_extract_age_years(first))
    return _unique(values)


def _reported_dates(notes):
    result = []
    for match in re.finditer(r"(?mi)^\s*According to [^;\n]+;\s*([A-Za-z]+\s+\d{1,2},?\s+\d{4}|[A-Za-z]+\s+\d{4})\s*:", str(notes or "")):
        if date := _normalise_date(match.group(1)):
            result.append(date)
    return result


def _death_date(hit):
    if date := _normalise_date(hit.get("deceased_date")):
        return date
    notes = str(hit.get("profile_notes") or "")
    pattern = r"\b(?:died|deceased)\b\D{0,20}(\d{4}[-/]\d{1,2}(?:[-/]\d{1,2})?|\d{1,2}/\d{1,2}/\d{4}|[A-Za-z]+\s+\d{1,2},?\s+\d{4}|\d{1,2}\s+[A-Za-z]+\s+\d{4})"
    match = re.search(pattern, notes, re.I)
    return _normalise_date(match.group(1)) if match else None


def _hit_countries(hit):
    values = []
    for field in ("countries", "nationality", "citizenship", "addresses"):
        values.extend(_country_values(hit.get(field)))
    values.extend(_country_values([hit.get("place_of_birth")]))
    return _unique(values)


def _listing_phrase(hit):
    category = _fold(hit.get("primary_category") or (_strings(hit.get("categories")) or [""])[0])
    if "sanction" in category:
        return "a sanctioned individual"
    if category == "pep":
        return "a politically exposed person"
    if "enforcement" in category:
        return "the subject of enforcement action"
    if "adverse" in category:
        return "named in adverse media"
    if category == "soe":
        return "a state-owned enterprise official"
    return "named on a watchlist"


def _search_birth_years(hit, countries, store):
    query = _search_query(hit.get("name"))
    phrase = _listing_phrase(hit)
    result = []
    source_ids = {}
    for row in store["search_by_query"].get(query, []):
        text = _norm_space((row.get("title") or "") + " " + (row.get("snippet") or ""))
        if phrase not in text:
            continue
        if countries and not any(country in text for country in countries):
            continue
        for year in _extract_age_years(str(row.get("snippet") or "")):
            result.append(year)
            source_ids[year] = row.get("result_id")
    return _unique(result), source_ids


def _hit_facts(hit, store):
    cached = store["hit_fact_cache"].get(hit.get("hit_id"))
    if cached is not None:
        return cached
    source_id = hit.get("hit_id")
    names = _unique([hit.get("name"), hit.get("best_matching_name")] + _strings(hit.get("aliases")))
    dobs, sources = [], {}
    for value in _strings(hit.get("dobs")):
        if normalized := _normalise_date(value):
            dobs.append(normalized)
            sources[normalized] = source_id
    notes = hit.get("profile_notes") or ""
    for value in _birth_dates_from_notes(notes):
        dobs.append(value)
        sources[value] = source_id
    countries = _hit_countries(hit)
    if not dobs:
        years, search_sources = _search_birth_years(hit, countries, store)
        for year in years:
            dobs.append(year)
            sources[year] = search_sources[year]
    reports = _reported_dates(notes)
    facts = {
        "names": _unique(names), "dobs": _unique(dobs), "dob_source_ids": sources,
        "death_date": _death_date(hit), "earliest_report_date": min(reports) if reports else None,
        "countries": countries, "entity_type": _entity_type(hit.get("entity_type")), "source_id": source_id,
    }
    store["hit_fact_cache"][hit.get("hit_id")] = facts
    return facts


def _context(alert, hit, store, members, representative, ambiguous=False):
    return {
        "hit_id": hit.get("hit_id"), "alert_id": hit.get("alert_id"), "screened_at": alert.get("screened_at"),
        "is_ambiguous": ambiguous, "customer_record_id": "AMBIGUOUS" if ambiguous else representative,
        "customer": _cluster_facts(members, representative, store) if representative else {
            "names": [], "verified_legal_name": None, "dob": None, "countries": [], "entity_type": "person", "source_id": None,
        },
        "hit": _hit_facts(hit, store),
    }


def prepare_hit_context(alert, hit, store):
    """Resolve the alert's plausible people and extract comparable facts for one hit."""
    cache_key = alert.get("alert_id")
    candidates = store["alert_candidate_cache"].get(cache_key)
    if candidates is None:
        candidates = _linked_candidates(alert, store)
        if not candidates:
            candidates = _unlinked_candidates(alert, store)
        store["alert_candidate_cache"][cache_key] = candidates
    clusters = {}
    for record_id in candidates:
        root = store["union_find"].find(record_id)
        score = _fit_score(alert, store["customers"][record_id], store["variants"])
        current = clusters.get(root)
        if current is None or (score, record_id) > (current[0], current[1]):
            clusters[root] = (score, record_id)
    if not clusters:
        return _context(alert, hit, store, [], None)
    ordered = sorted(clusters, key=lambda root: (-clusters[root][0], clusters[root][1]))
    candidate_contexts = [_context(alert, hit, store, store["clusters"][root], clusters[root][1]) for root in ordered]
    if len(candidate_contexts) == 1:
        return candidate_contexts[0]
    base = candidate_contexts[0]
    base["customer_record_id"] = "AMBIGUOUS"
    base["candidate_contexts"] = candidate_contexts
    base["candidate_customer_record_ids"] = [item["customer_record_id"] for item in candidate_contexts]
    return base
