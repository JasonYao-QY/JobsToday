"""Eligibility from metadata alone: a listing + an exclusion set -> verdict.

Pure functions. No I/O, no network, no API calls.

The reason is the product, not a by-product: when a role the owner expected
is missing from the tab, the UI shows the reason it was dropped. So every
exclusion carries a stable code and a sentence.

Metadata filtering is necessary and not sufficient (proposal §3.2). The
feed has no sponsorship, citizenship, class-standing or pay data, so this
module can only *drop* what the metadata proves ineligible and *flag* what
verify.py has to read the posting to settle. A flag is never a drop.

Judgment is per variant, because variants of one posting disagree: SIG
tags one Philadelphia/NYC row of the same title PhD-only and another
Master's. A listing is eligible when at least one variant is, and it
carries only its eligible variants and eligible locations forward, so the
ranker cannot pick London or a PhD req by accident.
"""

import re

__all__ = [
    "DEFAULT_RULES",
    "check",
    "check_all",
    "exclusion_hit",
]


# Defaults are the owner's stated rules (proposal §6). batch.py may pass a
# modified copy; nothing here reads settings on its own.
DEFAULT_RULES = {
    "term": "Summer 2027",
    # Categories he applies in. Hardware and Product are dropped by default;
    # this is a preference with an eligibility shape (major fit), so it is
    # configurable rather than buried.
    "categories": {"Software", "AI/ML/Data", "Quant"},
    # Employers dropped outright as defense / clearance. Matched with the
    # same company_key the rest of the app uses.
    "defense_companies": [
        "Lockheed Martin", "RTX", "Raytheon", "Northrop Grumman",
        "General Dynamics", "General Dynamics Mission Systems",
        "General Dynamics Electric Boat", "BAE Systems", "L3Harris",
        "L3Harris Technologies", "Leidos", "Booz Allen Hamilton", "SAIC",
        "CACI", "Anduril", "Anduril Industries", "MITRE",
        "Huntington Ingalls Industries", "Sandia National Laboratories",
        "MIT Lincoln Laboratory", "Johns Hopkins Applied Physics Laboratory",
        "Lawrence Livermore National Laboratory", "Los Alamos National Laboratory",
        "Parsons", "Peraton", "ManTech", "KBR", "Textron", "Shield AI",
        "Sierra Nevada Corporation", "Boeing Defense",
    ],
}

# Degree tags that, alone, exclude a Master's student.
_DOCTORAL = {"PhD", "MD", "JD", "PharmD", "MBA"}

# Title wording. Word-bounded and case-insensitive.
_T = lambda p: re.compile(p, re.I)
_CLEARANCE = _T(r"\b(clearance|ts/sci|top secret|secret clearance|polygraph|"
                r"security clearance|cleared)\b")
_UNDERGRAD = _T(r"\b(undergrad|undergraduate|freshman|sophomore|"
                r"first[- ]year|second[- ]year|high school|early career|"
                r"apprentice\w*)\b")
_PHD_TITLE = _T(r"\b(phd|ph\.d\.?|doctoral|postdoc\w*)\b")
_MBA_TITLE = _T(r"\bmba\b")
# "- BS" or "(BS)" or "Bachelor's" naming the degree alone, not "BS/MS".
_BS_ONLY = _T(r"(?<![/\w])(bs|b\.s\.|bachelor['\u2019]?s?)(?![/\w])(?!\s*/\s*(ms|m\.s\.|master))")
# "Master's" / "Masters" / "MS" / "Graduate", but not "Master Data".
_MS_TITLE = _T(r"(?<![\w])(ms|m\.s\.|master['\u2019]s|masters|graduate)(?![\w])")
_BSMS = _T(r"\b(bs|b\.s\.)\s*/\s*(ms|m\.s\.)\b")
# A non-summer season named next to a year: "Spring 2027", "Spring/Summer
# 2027", "Spring or Summer 2027". A year is required so "Fall Detection"
# is not a term.
_SEASON = _T(r"\b(spring|winter|fall)\b")
_YEAR = _T(r"\b20\d\d\b")
_SUMMER = _T(r"\bsummer\b")
_COOP = _T(r"\bco-?op\b")


def _reason(code, text, **extra):
    r = {"code": code, "text": text}
    r.update(extra)
    return r


def _norm_title(s):
    s = re.sub(r"[‐-―−]", "-", (s or "").casefold())
    s = re.sub(r"[^\w\s/&+-]", " ", s)
    return " ".join(s.split())


def _default_company_key(name):
    return _norm_title(name)


# Words that say nothing about which role it is.
_ROLE_NOISE = {"intern", "internship", "interns", "summer", "2026", "2027", "2028",
               "the", "and", "of", "for", "a", "an", "in", "program", "programme",
               "-", "&", "/", "team", "teams", "multiple", "co-op", "coop"}


def _role_tokens(s):
    toks = re.split(r"[\s/,()-]+", _norm_title(s))
    # Crude stem: "engineering" and "engineer" are the same role word.
    return {t[:6] for t in toks if t and t not in _ROLE_NOISE}


def _default_role_match(a, b):
    """Fallback when batch.py does not pass tracker.role_sim: one title's
    role words contain the other's ("Software Engineer Intern" covers
    "Software Engineering Intern - Summer 2027")."""
    ta, tb = _role_tokens(a), _role_tokens(b)
    if not ta or not tb:
        return _norm_title(a) == _norm_title(b)
    return ta <= tb or tb <= ta


def exclusion_hit(company, title, exclusions, company_key=None, role_match=None,
                  company_match=None):
    """The first exclusion entry that covers (company, title), or None.

    An entry is {company, title?, kind, note?}. No title means the whole
    company is closed (banned, capped, defense, one-application firms). A
    title means that role only, matched with role_match, which batch.py
    sets to tracker.role_sim so application wording and listing wording
    are compared the same way the mail scan compares them.

    company_match(a, b) -> bool, when given, decides "same company" instead
    of key equality. batch.py passes tracker.same_company: applications.json
    holds company names as mail spelled them ("IMC", "CTC"), and only the
    tracker's tolerant matcher knows those are "IMC Trading" and "Chicago
    Trading Company".
    """
    company_key = company_key or _default_company_key
    role_match = role_match or _default_role_match
    ck = company_key(company)
    for e in exclusions or ():
        ec = e.get("company") or ""
        if company_match is not None:
            if not company_match(ec, company):
                continue
        elif company_key(ec) != ck:
            continue
        if not e.get("title") or role_match(e["title"], title):
            return e
    return None


def _variant_verdict(v, rules):
    """(drop_reasons, flags) for one variant."""
    drops, flags = [], []

    terms = v.get("terms") or []
    if rules["term"] not in terms:
        drops.append(_reason("term", "Not a %s posting (%s)." % (
            rules["term"], ", ".join(terms) or "no term")))
    else:
        others = [t for t in terms if t != rules["term"]]
        if others:
            flags.append(_reason("multi_term",
                                 "Also listed for %s; check it is not a "
                                 "January, spring or multi-season start."
                                 % ", ".join(others)))

    degrees = set(v.get("degrees") or [])
    if degrees and degrees <= _DOCTORAL:
        drops.append(_reason("degree", "Degree tag %s only." % "/".join(sorted(degrees))))
    elif degrees == {"Bachelor's"}:
        # Tagging is loose (proposal §3.3): a signal, not a drop.
        flags.append(_reason("bachelors_tag",
                             "Tagged Bachelor's only; check Master's students "
                             "are eligible."))

    locs = v.get("locations") or []
    if locs and all(l["us"] is False for l in locs):
        drops.append(_reason("non_us", "Only outside the US (%s)." %
                             "; ".join(l["raw"] for l in locs)))
    elif not locs:
        flags.append(_reason("no_location", "No location listed."))
    elif any(l["us"] is None for l in locs) and not any(l["us"] for l in locs):
        flags.append(_reason("location_unknown",
                             "Location could not be placed: %s." %
                             "; ".join(l["raw"] for l in locs)))
    return drops, flags


def _title_verdict(title, rules):
    drops, flags = [], []
    if _CLEARANCE.search(title):
        drops.append(_reason("clearance", "Title mentions a security clearance."))
    if _UNDERGRAD.search(title):
        drops.append(_reason("class_standing",
                             "Title gates on class standing (%s)." %
                             _UNDERGRAD.search(title).group(0)))
    if _PHD_TITLE.search(title):
        drops.append(_reason("degree", "Title names a PhD role."))
    elif _MBA_TITLE.search(title) and not _MS_TITLE.search(title):
        drops.append(_reason("degree", "Title names an MBA role."))
    if _BS_ONLY.search(title) and not _BSMS.search(title) and not _MS_TITLE.search(title):
        drops.append(_reason("degree", "Title names a Bachelor's-only req."))
    m = _SEASON.search(title) if _YEAR.search(title) else None
    if m and not _SUMMER.search(title):
        drops.append(_reason("term", "Title names a %s start." % m.group(0).lower()))
    elif m:
        flags.append(_reason("multi_term", "Title also names a %s start."
                             % m.group(0).lower()))
    if _COOP.search(title):
        flags.append(_reason("coop", "Co-op: check the length and start date."))
    return drops, flags


def check(listing, exclusions=(), rules=None, company_key=None, role_match=None,
          company_match=None):
    """One listing -> verdict.

    {ok, key, company, title, reasons, flags, variants, locations, signals}

    ok False: `reasons` says why, first reason first. ok True: `variants`
    and `locations` are the eligible subset, `flags` are what verify.py
    must settle, `signals` are facts the ranker may use (masters_title,
    texas). Never raises on a well-formed listing.
    """
    rules = rules or DEFAULT_RULES
    company, title = listing.get("company") or "", listing.get("title") or ""
    out = {"ok": False, "key": listing.get("key"), "company": company,
           "title": title, "reasons": [], "flags": [], "variants": [],
           "locations": [], "signals": {}}

    hit = exclusion_hit(company, title, exclusions, company_key, role_match, company_match)
    if hit:
        kind = hit.get("kind") or "excluded"
        note = hit.get("note")
        text = {
            "applied": "Already applied",
            "banned": "Company is on the do-not-apply list",
            "capped": "Application cap reached at this company",
            "pending": "Suggested in an earlier batch, not yet applied",
            "defense": "Defense or clearance employer",
            "ineligible": "Checked before and found ineligible",
        }.get(kind, "Excluded")
        if hit.get("title") and kind in ("applied", "pending", "ineligible"):
            text += " (%s)" % hit["title"]
        out["reasons"].append(_reason(kind, text + (": %s." % note if note else "."),
                                      entry=hit))
        return out

    defense = [{"company": c, "kind": "defense"} for c in rules.get("defense_companies", ())]
    if exclusion_hit(company, title, defense, company_key):
        out["reasons"].append(_reason("defense", "Defense or clearance employer."))
        return out

    cats = rules.get("categories")
    cat = listing.get("category")
    if cats and cat not in cats:
        out["reasons"].append(_reason("category", "Category %s is outside %s." %
                                      (cat or "untagged", ", ".join(sorted(cats)))))
        return out

    tdrops, tflags = _title_verdict(title, rules)
    if tdrops:
        out["reasons"].extend(tdrops)
        return out

    variant_reasons, kept, flags = [], [], list(tflags)
    for v in listing.get("variants") or []:
        drops, vflags = _variant_verdict(v, rules)
        if drops:
            variant_reasons.extend(drops)
        else:
            kept.append(v)
            for f in vflags:
                if f["code"] not in {x["code"] for x in flags}:
                    flags.append(f)
    if not kept:
        # Report each distinct reason once, in the order first seen.
        seen = set()
        for r in variant_reasons:
            if (r["code"], r["text"]) not in seen:
                seen.add((r["code"], r["text"]))
                out["reasons"].append(r)
        return out

    kept_ids = {v["id"] for v in kept}
    locs = [l for l in listing.get("locations") or []
            if l.get("variant") in kept_ids and l["us"] is not False]
    texas = any(l.get("state") == "TX" for l in locs)
    if texas:
        flags.append(_reason("texas", "Texas location: check for a citizenship "
                                      "requirement."))
    out.update(
        ok=True, flags=flags, variants=kept, locations=locs,
        signals={
            "masters_title": bool(_MS_TITLE.search(title)) or bool(_BSMS.search(title)),
            "texas": texas,
            "texas_only": bool(locs) and all(l.get("state") == "TX" for l in locs),
            "multi_city": len(locs) > 1,
        },
    )
    return out


def check_all(listings, exclusions=(), rules=None, company_key=None, role_match=None,
              company_match=None):
    """(eligible, dropped) — both lists of verdicts, input order kept."""
    ok, dropped = [], []
    for x in listings:
        v = check(x, exclusions, rules, company_key, role_match, company_match)
        (ok if v["ok"] else dropped).append(v)
    return ok, dropped
