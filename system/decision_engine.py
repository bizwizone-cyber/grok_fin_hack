"""Conservative, deterministic SOP decision logic for screening hits."""

import re
import unicodedata
from datetime import date


PLACEHOLDERS = {"na", "n", "a", "test", "unknown", "xxx", "none", "null", "nil", "-"}
COUNTRY_CODES = {
    "us": "united states", "usa": "united states", "gb": "united kingdom", "uk": "united kingdom",
    "de": "germany", "fr": "france", "es": "spain", "it": "italy", "nl": "netherlands",
    "be": "belgium", "ch": "switzerland", "at": "austria", "ie": "ireland", "pt": "portugal",
    "ca": "canada", "au": "australia", "nz": "new zealand", "in": "india", "pk": "pakistan",
    "bd": "bangladesh", "ng": "nigeria", "gh": "ghana", "za": "south africa", "cn": "china",
    "jp": "japan", "ru": "russia", "br": "brazil", "mx": "mexico", "sg": "singapore",
    "my": "malaysia", "tr": "turkey", "ua": "ukraine", "pl": "poland", "se": "sweden",
    "no": "norway", "dk": "denmark", "fi": "finland", "gr": "greece", "ro": "romania",
}


def _fold(value):
    value = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(ch for ch in value if not unicodedata.combining(ch))


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
            elif isinstance(item, dict):
                result.extend(_strings(item.get("name") or item.get("value")))
        return result
    return []


def _variant_sets(variants):
    groups = variants.get("groups", []) if isinstance(variants, dict) else []
    return [set(_fold(x) for x in group if _fold(x)) for group in groups]


def _legal_forms(variants):
    forms = variants.get("company_legal_forms", []) if isinstance(variants, dict) else []
    return {_fold(x) for x in forms if _fold(x)}


def _tokens(name, legal_forms=()):
    text = _fold(name)
    # Anything inside brackets is explanatory metadata, not part of the name.
    text = re.sub(r"\([^)]*\)|\[[^]]*\]|\{[^}]*\}", " ", text)
    text = re.sub(r"\bn\s*/\s*a\b", " ", text)
    text = "".join(ch if ch.isalnum() else " " for ch in text)
    result, seen = [], set()
    for part in text.split():
        if len(part) == 1 or part in PLACEHOLDERS or part.isdigit() or part in legal_forms or part in seen:
            continue
        seen.add(part)
        result.append(part)
    return result


def _levenshtein(a, b, limit=None):
    if len(a) > len(b):
        a, b = b, a
    if limit is not None and len(b) - len(a) > limit:
        return limit + 1
    prev = list(range(len(a) + 1))
    for j, cb in enumerate(b, 1):
        cur = [j]
        row_min = j
        for i, ca in enumerate(a, 1):
            cur.append(min(cur[-1] + 1, prev[i] + 1, prev[i - 1] + (ca != cb)))
            row_min = min(row_min, cur[-1])
        if limit is not None and row_min > limit:
            return limit + 1
        prev = cur
    return prev[-1]


def _parts_match(a, b, groups):
    if a == b:
        return True
    for group in groups:
        if a in group and b in group:
            return True
    shorter = min(len(a), len(b))
    if shorter < 4:
        return False
    allowed = 1 if shorter <= 6 else 2
    return _levenshtein(a, b, allowed) <= allowed


def _sequence_match(short, long, groups):
    """Match each part of a name, allowing adjacent joined/split spellings."""
    memo = {}

    def walk(i, j):
        if i == len(short):
            return True
        if (i, j) in memo:
            return memo[(i, j)]
        for a_end in range(i + 1, min(len(short), i + 2) + 1):
            a = "".join(short[i:a_end])
            for b_end in range(j + 1, min(len(long), j + 2) + 1):
                b = "".join(long[j:b_end])
                if _parts_match(a, b, groups) and walk(a_end, b_end):
                    memo[(i, j)] = True
                    return True
        memo[(i, j)] = False
        return False

    return walk(0, 0)


def names_agree(customer_names, hit_names, variants, entity_type="PERSON"):
    legal = _legal_forms(variants) if _is_company(entity_type) else set()
    groups = _variant_sets(variants)
    customer_names = _strings(customer_names)
    hit_names = _strings(hit_names)
    for cname in customer_names:
        cparts = _tokens(cname, legal)
        # A sparse customer name cannot support a safe name clearance or confirmation.
        if len(cparts) < 2 and not _is_company(entity_type):
            continue
        for hname in hit_names:
            hparts = _tokens(hname, legal)
            if not hparts:
                continue
            # The shorter normalized name must match completely. When there are at
            # least two matching parts, extra parts on either side are acceptable.
            short, long = (cparts, hparts) if len(cparts) <= len(hparts) else (hparts, cparts)
            if _sequence_match(short, long, groups):
                return True
            # Also accept reordered name parts. Count a distinct part only once.
            matched = 0
            used = set()
            for part in cparts:
                for j, other in enumerate(hparts):
                    if j not in used and _parts_match(part, other, groups):
                        used.add(j)
                        matched += 1
                        break
            if matched >= 2 or matched == min(len(cparts), len(hparts)):
                return True
    return False


def _is_company(value):
    return "company" in str(value or "").casefold() or "corpor" in str(value or "").casefold()


def _entity_type(value):
    return "COMPANY" if _is_company(value) else "PERSON"


def _parse_date(value):
    """Parse full ISO dates or partial YYYY[-MM[-DD]] values."""
    if not value:
        return None
    match = re.search(r"(?<!\d)(\d{4})(?:[-/](\d{1,2})(?:[-/](\d{1,2}))?)?(?!\d)", str(value))
    if not match:
        return None
    year, month, day = match.groups()
    try:
        return int(year), int(month) if month else None, int(day) if day else None
    except ValueError:
        return None


def _dob_relation(customer_dob, hit_dob):
    c, h = _parse_date(customer_dob), _parse_date(hit_dob)
    if not c or not h:
        return "unknown"
    if abs(c[0] - h[0]) > 1:
        return "different"
    if c[0] != h[0]:
        # A one-year difference is neither agreement nor an airtight contradiction.
        # An age-at-date extraction will provide both possible exact years separately.
        return "unknown"
    if c[1] is not None and h[1] is not None and c[1] != h[1]:
        return "different"
    if c[2] is not None and h[2] is not None and c[2] != h[2]:
        return "different"
    # A year-only date agrees with a full date in the same year; missing components
    # do not manufacture a contradiction.
    return "agree"


def _countries(values):
    result = set()
    for value in _strings(values):
        # Address-like values may be supplied; use the final component as policy says.
        part = value.rsplit(",", 1)[-1].strip()
        folded = _fold(part)
        if folded in ("international", "europe", "worldwide", "global"):
            continue
        nationalities = {"british": "united kingdom", "american": "united states", "us american": "united states",
                         "spanish": "spain", "french": "france", "german": "germany", "italian": "italy",
                         "dutch": "netherlands", "canadian": "canada", "australian": "australia",
                         "nigerian": "nigeria", "ghanaian": "ghana", "indian": "india", "pakistani": "pakistan",
                         "bangladeshi": "bangladesh", "chinese": "china", "japanese": "japan", "russian": "russia",
                         "brazilian": "brazil", "mexican": "mexico", "singaporean": "singapore", "south african": "south africa"}
        result.add(COUNTRY_CODES.get(folded, nationalities.get(folded, folded)))
    return result


def _id(*values):
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _evidence(*values):
    result = []
    for value in values:
        if isinstance(value, (list, tuple)):
            candidates = value
        else:
            candidates = [value]
        for item in candidates:
            if isinstance(item, str) and item.strip() and item != "AMBIGUOUS" and item not in result:
                result.append(item)
            if len(result) == 2:
                return result
    return result


def _result(ctx, decision, code, reason, evidence, record_id=None):
    cited = _evidence(evidence) or _evidence(ctx.get("hit_id"))
    if cited:
        label = "record" if len(cited) == 1 else "records"
        reason = _sentences(reason, "The deciding %s %s." % (label, " and ".join(cited)))
    return {
        "hit_id": ctx.get("hit_id"),
        "alert_id": ctx.get("alert_id"),
        "decision": decision,
        "reason_code": code,
        "customer_record_id": record_id if record_id is not None else ctx.get("customer_record_id"),
        "reason": reason,
        "evidence": cited,
    }


def _sentences(*sentences):
    return " ".join(s.strip() for s in sentences if s and s.strip())


def evaluate_context(ctx, name_variants):
    """Evaluate one prepared hit context and return one schema-compatible result."""
    customer = ctx.get("customer") or {}
    hit = ctx.get("hit") or {}
    customer_id = ctx.get("customer_record_id")
    customer_source = _id(customer.get("source_id"), customer_id)
    customer_dob_source = _id(customer.get("dob_source_id"), customer_source)
    customer_country_source = _id(customer.get("country_source_id"), customer_source)
    hit_source = _id(hit.get("source_id"), ctx.get("hit_id"))

    # The identity resolver has already established that the screened record is ambiguous.
    if ctx.get("is_ambiguous"):
        ambiguity_sources = (ctx.get("candidate_customer_record_ids") or ctx.get("customer_record_ids")
                             or ctx.get("ambiguous_evidence") or [customer_source, hit_source])
        return _result(ctx, "REVIEW", "AMBIGUOUS_CUSTOMER_RECORD",
                       _sentences("More than one customer record fits the screened identity, so the hit cannot be safely resolved.",
                                  "The customer record is marked AMBIGUOUS pending human review."),
                       ambiguity_sources, "AMBIGUOUS")

    ctype, htype = _entity_type(customer.get("entity_type")), _entity_type(hit.get("entity_type"))
    if ctype != htype:
        return _result(ctx, "DIFFERENT_ENTITY", "ENTITY_TYPE_MISMATCH",
                       _sentences("The customer is a %s and the listed hit is a %s." % (ctype.lower(), htype.lower()),
                                  "Their entity types cannot describe the same identity."),
                       [customer_source, hit_source])

    screened = _parse_date(ctx.get("screened_at"))
    death = _parse_date(hit.get("death_date"))
    if screened and death and date(death[0], death[1] or 1, death[2] or 1) < date(screened[0], screened[1] or 1, screened[2] or 1):
        return _result(ctx, "DIFFERENT_ENTITY", "DECEASED",
                       _sentences("The hit profile records a death date before the customer was screened.",
                                  "The hit therefore cannot be the customer at screening time."),
                       [hit_source, _id(ctx.get("alert_id"), customer_source)])

    customer_dob = customer.get("dob")
    report = _parse_date(hit.get("earliest_report_date"))
    birth = _parse_date(customer_dob)
    if report and birth:
        event_date = date(report[0], report[1] or 1, report[2] or 1)
        birth_date = date(birth[0], birth[1] or 1, birth[2] or 1)
        age = event_date.year - birth_date.year - ((event_date.month, event_date.day) < (birth_date.month, birth_date.day))
        impossible = (age < 16) if ctype == "PERSON" else (birth_date > event_date)
        if impossible:
            reason = ("The customer would have been younger than 16 at the hit's earliest dated report."
                      if ctype == "PERSON" else "The company was incorporated after the hit's earliest dated report.")
            return _result(ctx, "DIFFERENT_ENTITY", "TEMPORAL_IMPOSSIBILITY",
                           _sentences(reason, "The timeline rules out the customer as the listed subject."),
                           [customer_source, hit_source])

    hit_dobs = _strings(hit.get("dobs"))
    relations = [_dob_relation(customer_dob, value) for value in hit_dobs]
    has_dob_conflict = bool(relations) and all(value == "different" for value in relations)
    has_dob_agreement = "agree" in relations
    dob_sources = hit.get("dob_source_ids") or {}
    hit_dob_source = None
    for value in hit_dobs:
        if isinstance(dob_sources, dict):
            hit_dob_source = _id(dob_sources.get(value)) or hit_dob_source
    dob_evidence = [customer_dob_source, hit_dob_source or hit_source]
    if has_dob_conflict:
        return _result(ctx, "DIFFERENT_ENTITY", "DOB_MISMATCH",
                       _sentences("The customer's date of birth contradicts every date supplied for the hit.",
                                  "At least one year differs by more than one, or a shared month/day conflicts, so the identities cannot agree."),
                       dob_evidence)

    # Only a verified legal name can establish a name contradiction. Aliases count on hit side.
    verified_name = customer.get("verified_legal_name")
    hit_names = _strings(hit.get("names"))
    if verified_name and hit_names and not names_agree([verified_name], hit_names, name_variants, htype):
        verified_source = _id(customer.get("verified_name_source_id"), customer.get("source_id"), customer_id)
        return _result(ctx, "DIFFERENT_ENTITY", "NAME_MISMATCH_VERIFIED",
                       _sentences("The verified customer name %s matches none of the hit's names or aliases." % verified_name,
                                  "The verified identity therefore contradicts the listed identity."),
                       [verified_source, hit_source])

    customer_countries = _countries(customer.get("countries"))
    hit_countries = _countries(hit.get("countries"))
    country_agreement = bool(customer_countries & hit_countries)
    authoritative_customer_names = [verified_name] if verified_name else customer.get("names")
    name_agreement = names_agree(authoritative_customer_names, hit_names, name_variants, htype)
    country_conflict = bool(customer_countries and hit_countries and not country_agreement)

    # A geographic contradiction is unsafe when the core person identifiers agree.
    if country_conflict:
        if name_agreement and has_dob_agreement:
            return _result(ctx, "REVIEW", "INSUFFICIENT_DATA",
                           _sentences("The customer's name and date of birth agree with the hit, but their recorded countries differ.",
                                      "Because geography alone cannot clear a name-and-date match, the hit needs human review."),
                           [customer_source, hit_source])
        return _result(ctx, "DIFFERENT_ENTITY", "LOCATION_MISMATCH",
                       _sentences("The customer's recorded countries do not overlap any country tied to the hit.",
                                  "No agreeing name-and-date-of-birth pair prevents clearance."),
                       [customer_country_source, hit_source])

    if ctype == "PERSON" and name_agreement and has_dob_agreement:
        if country_agreement:
            code = "NAME_DOB_LOCATION_MATCH"
            reason = _sentences("The customer's name and date of birth agree with the hit.",
                                "Both records are tied to at least one common country, and no fact contradicts the match.")
        else:
            code = "NAME_DOB_MATCH"
            reason = _sentences("The customer's name and date of birth agree with the hit.",
                                "No country contradiction or other fact prevents treating them as the same person.")
        return _result(ctx, "SAME_ENTITY", code, reason, [customer_source, hit_source])

    if ctype == "COMPANY" and name_agreement and country_agreement:
        return _result(ctx, "SAME_ENTITY", "NAME_LOCATION_MATCH",
                       _sentences("The company name agrees with the hit's name.",
                                  "Both are tied to a common country, with no contradictory fact."),
                       [customer_country_source, hit_source])

    return _result(ctx, "REVIEW", "INSUFFICIENT_DATA",
                   _sentences("The available records do not establish enough agreement to confirm the hit or a decisive contradiction to clear it.",
                              "The hit remains unresolved and should be reviewed by a person."),
                   [customer_source, hit_source])
