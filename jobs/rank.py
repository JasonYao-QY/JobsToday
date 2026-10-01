"""Ranking: eligible verdicts -> ordered, explained, with a city and a resume.

Pure. Takes filters.check output (the eligible variants and locations
only) and returns entries whose score is a plain sum of named parts. The
parts are the explanation: he will disagree with an order and will want to
see why something placed where it did (proposal §7.4), so every point is
attached to a sentence, and nothing else moves the score.

Preferences, in his order (proposal §6):
  1. model-building work above evaluation / QA framing; "model risk
     validation" and similar rank low even at good firms
  2. large firms' SWE and AIML tracks above small robotics startups
  3. SWE roles in the Bay Area or Seattle; finance roles in NYC, Chicago
     or Boston; pick the preferred city of a multi-city req

The weights encode that order: the work-type part spans the widest range
(-6 to +6), firm size and city +3 each, then +-1 adjustments. Work type
does not strictly dominate: a large-firm analytics role in a preferred
city (-1+3+3) still ties a small-firm SWE role elsewhere (+5).
"""

import re

__all__ = [
    "DEFAULT_PREFS",
    "is_finance",
    "resume_for",
    "score",
    "rank",
]

_T = lambda p: re.compile(p, re.I)

# --------------------------------------------------------------------------
# preferences as data, so a disagreement is a one-line edit

DEFAULT_PREFS = {
    "work": [
        # (points, label, pattern) — first match wins, so order matters:
        # evaluation wording beats a model-building word in the same title
        # ("ML Model Validation Intern" is validation work).
        (-6, "model risk / validation framing",
         r"\bmodel risk\b|\bmodel validation\b|\bvalidation\b|\bverification\b"),
        (-4, "evaluation, QA or test framing",
         r"\bevaluation\b|\beval\b|\bqa\b|\bquality assurance\b|\btest(ing)?\b"
         r"|\blabel(l)?ing\b|\bannotation\b|\bbenchmark"),
        (6, "model-building work",
         r"\bmachine learning\b|\bml\b|\bdeep learning\b|\bai\b|\bartificial intelligence\b"
         r"|\bapplied scientist\b|\bresearch engineer\b|\bperception\b|\bcomputer vision\b"
         r"|\bllm\b|\bgenerative\b|\bgenai\b|\bmultimodal\b|\bnlp\b|\brobot learning\b"
         r"|\bautonomy\b|\bmodel(ing|ling)?\b"),
        (5, "software engineering",
         r"\bsoftware\b|\bswe\b|\bdeveloper\b|\bbackend\b|\bback-end\b|\bfull[- ]?stack\b"
         r"|\binfrastructure\b|\bplatform\b|\bdistributed\b|\bcompiler\b"
         r"|\bmember of technical staff\b"),
        (5, "quant development / research",
         r"\bquant\w*\b|\btrading\b|\balgorithmic\b"),
        (3, "data science / data engineering",
         r"\bdata scien\w+\b|\bdata engineer\w*\b|\bdata platform\b"),
        (-1, "analytics / business role",
         r"\banalyst\b|\banalytics\b|\bbusiness intelligence\b|\bproduct\b|\boperations\b"
         r"|\binvestment banking\b|\bmarketing\b"),
    ],
    "large": [
        "Google", "Alphabet", "Apple", "Microsoft", "Amazon", "Amazon Web Services",
        "Meta", "NVIDIA", "Netflix", "Salesforce", "Adobe", "Oracle", "IBM", "Intel",
        "AMD", "Qualcomm", "Cisco", "Uber", "Lyft", "Airbnb", "LinkedIn", "Snap",
        "Pinterest", "Stripe", "Databricks", "Snowflake", "Waymo", "Tesla", "Intuit",
        "ServiceNow", "Workday", "Dell Technologies", "HP", "HP IQ", "Visa",
        "Mastercard", "PayPal", "Bloomberg", "Walmart", "Target", "Capital One",
        "JPMorgan Chase", "Goldman Sachs", "Morgan Stanley", "Citadel",
        "Citadel Securities", "Jane Street", "Two Sigma", "Hudson River Trading",
        "D. E. Shaw", "Susquehanna International Group (SIG)", "Optiver",
        "IMC Trading", "Roblox", "Electronic Arts", "Epic Games", "Datadog",
        "Cloudflare", "DoorDash", "Instacart", "Coinbase", "Robinhood", "Figma",
        "Notion", "Scale AI", "OpenAI", "Anthropic", "Samsung", "Sony", "Siemens",
        "Bosch", "GE Vernova", "General Motors", "Ford", "Marvell", "Micron",
        "Broadcom", "Texas Instruments", "Applied Materials", "Cadence", "Synopsys",
        "Autodesk", "Palo Alto Networks", "CrowdStrike", "Twilio", "Zoom", "Dropbox",
        "eBay", "Expedia", "Wayfair", "State Farm", "Allstate", "Progressive",
        "Liberty Mutual", "MetLife", "Prudential", "Fidelity Investments", "BlackRock",
        "Charles Schwab", "American Express", "Wells Fargo", "Bank of America",
        "Citi", "Citigroup", "Disney", "Nike", "Johnson & Johnson", "Nasdaq",
        "CME Group", "Intercontinental Exchange", "S&P Global", "Moody's", "MSCI",
        "Bridgewater Associates", "Millennium", "Point72", "DRW", "Jump Trading",
        "Akuna Capital", "Tower Research Capital", "Rippling", "Brex", "Ramp",
        "Plaid", "Chime", "Affirm", "Block", "Shopify", "Spotify", "Reddit",
        "Duolingo", "Atlassian", "Splunk", "VMware", "Arm", "Lockheed Martin",
    ],
    # Small robotics startups rank below large firms (proposal §6). Only
    # applied when the company is not in `large`.
    "robotics": r"\brobot\w*\b|\bautonom\w*\b|\bdrone\w*\b",
    "finance_names": [
        "Citadel", "Citadel Securities", "Jane Street", "Two Sigma",
        "Hudson River Trading", "D. E. Shaw", "Susquehanna International Group (SIG)",
        "Optiver", "IMC Trading", "Akuna Capital", "DRW", "Jump Trading",
        "Tower Research Capital", "Virtu Financial", "Chicago Trading Company",
        "Five Rings", "Old Mission", "PDT Partners", "AQR Capital Management",
        "Bridgewater Associates", "Millennium", "Point72", "Balyasny",
        "Schonfeld", "Belvedere Trading", "Geneva Trading", "DV Trading", "Voleon",
        "Arrowstreet Capital", "Man Group", "Squarepoint Capital", "XTX Markets",
        "Radix Trading", "Headlands Technologies", "Flow Traders",
        "Wolverine Trading", "Peak6", "Garda Capital Partners", "Quantbot Technologies",
        "Brevan Howard", "Walleye Capital", "Verition Fund Management",
        "JPMorgan Chase", "Goldman Sachs", "Morgan Stanley", "Citi", "Citigroup",
        "Bank of America", "Wells Fargo", "Barclays", "Deutsche Bank", "UBS", "RBC",
        "State Street", "BNY", "Northern Trust", "BlackRock", "Fidelity Investments",
        "Charles Schwab", "Vanguard", "American Express", "Capital One", "Discover",
        "Visa", "Mastercard", "PayPal", "Stripe", "Block", "Coinbase", "Robinhood",
        "Plaid", "Affirm", "Chime", "Brex", "Ramp", "FIS", "Fiserv", "Nasdaq",
        "CME Group", "Intercontinental Exchange", "Cboe", "Tradeweb", "LSEG",
        "Bloomberg", "S&P Global", "Kensho", "Moody's", "MSCI", "Enova", "Ally",
        "Synchrony", "Lazard", "LPL Financial", "OCC", "Finastra", "Talos",
        "Kalshi", "Nubank", "SoFi", "Marqeta", "Adyen", "Lazard",
    ],
    "finance_words": r"\bcapital\b|\btrading\b|\bsecurities\b|\bfinancial\b|\bbank\w*\b"
                     r"|\bpayments?\b|\bfund\b|\binvestments?\b|\basset management\b"
                     r"|\bexchange\b|\bmarkets\b|\bcredit\b",
    "cities": {
        # track -> {metro: points}
        "swe": {"bay_area": 3, "seattle": 3, "nyc": 1, "boston": 1, "chicago": 1},
        "finance": {"nyc": 3, "chicago": 3, "boston": 3, "bay_area": 1, "seattle": 1},
    },
    "large_points": 3,
    "robotics_points": -1,
    "masters_points": 1,
    "bachelors_tag_points": -1,
    "flag_points": {"texas": -1, "coop": -1, "multi_term": -1, "location_unknown": -1},
    "fresh_days": 3,
    "fresh_points": 0.5,
}


def _key(s):
    s = (s or "").casefold().replace("’", "'")
    s = re.sub(r"[^\w\s&'.-]", " ", s)
    return " ".join(s.split())


def _in(company, names, company_key=None):
    k = company_key or _key
    ck = k(company)
    return any(k(n) == ck for n in names)


def is_finance(listing_or_verdict, prefs=None, company_key=None):
    prefs = prefs or DEFAULT_PREFS
    company = listing_or_verdict.get("company") or ""
    cat = listing_or_verdict.get("category")
    if cat is None and listing_or_verdict.get("variants"):
        cat = listing_or_verdict["variants"][0].get("category")
    if cat == "Quant":
        return True
    if _in(company, prefs["finance_names"], company_key):
        return True
    return bool(_T(prefs["finance_words"]).search(company))


# --------------------------------------------------------------------------
# resume variant

_R_LLM = _T(r"\bllm\b|\blarge language\b|\bmultimodal\b|\bgenerative\b|\bgenai\b"
            r"|\bevaluation\b|\bbenchmark|\bfoundation model|\bvision[- ]language\b")
_R_ML = _T(r"\bmachine learning\b|\bml\b|\bdeep learning\b|\bai\b|\bartificial intelligence\b"
           r"|\bapplied scientist\b|\bresearch (engineer|scientist)\b|\bperception\b"
           r"|\bcomputer vision\b|\bnlp\b|\bautonomy\b")
_R_DATA = _T(r"\bdata\b|\banalytics\b|\banalyst\b|\bbusiness intelligence\b"
             r"|\bquantitative research\b|\bquant(itative)? researcher\b|\bresearch\b|\bstatistic")
_R_FIN_RESEARCH = _T(r"\bresearch\b|\banalyst\b|\bquant\w*\b|\banalytics\b|\bmodel(l)?ing\b"
                     r"|\binvesting\b|\bportfolio\b")
_R_BUILD = _T(r"\bsoftware\b|\bswe\b|\bdeveloper\b|\bdevelopment\b|\bengineer\w*\b")
_R_SWE = _T(r"\bsoftware\b|\bswe\b|\bdeveloper\b|\bbackend\b|\bfull[- ]?stack\b"
            r"|\binfrastructure\b|\bplatform\b|\bengineer\b|\btechnology\b|\bdevelopment\b")


def resume_for(verdict, prefs=None, company_key=None):
    """(variant, why). One of SWE, SWE-fin, Data, AIML, MLLM.

    SWE-fin carries the payments/KYC-AML and regulatory bullets plain SWE
    does not; it is the pick for software roles at trading firms, banks,
    exchanges and payments companies. Quant *research* at those firms goes
    to Data (the BlackRock precedent), quant *development* to SWE-fin.
    """
    title = verdict.get("title") or ""
    fin = is_finance(verdict, prefs, company_key)
    if _R_LLM.search(title):
        return "MLLM", "LLM / multimodal / evaluation wording"
    if _R_ML.search(title):
        return "AIML", "machine-learning role"
    if fin and _R_SWE.search(title) and not _R_FIN_RESEARCH.search(title):
        return "SWE-fin", "software role at a finance or payments firm"
    if fin and _R_FIN_RESEARCH.search(title) and not _R_BUILD.search(title):
        return "Data", "quant research / modeling at a finance firm"
    if _R_DATA.search(title):
        return "Data", ("quant research at a finance firm" if fin else "data / analytics role")
    if fin:
        return "SWE-fin", "finance or payments firm"
    return "SWE", "software role"


# --------------------------------------------------------------------------
# scoring

def _part(points, text):
    return {"points": points, "text": text}


def _pick_city(locations, track, prefs):
    """(location, points, text). The preferred city of a multi-city req."""
    table = prefs["cities"][track]
    best, best_pts = None, None
    for loc in locations:
        pts = table.get(loc.get("metro"), 0)
        if best is None or pts > best_pts:
            best, best_pts = loc, pts
    if best is None:
        return None, 0, "No US location to pick."
    where = best.get("raw") or best.get("city") or "?"
    if best_pts > 0:
        others = len(locations) - 1
        tail = " (chosen from %d cities)" % len(locations) if others else ""
        return best, best_pts, "%s is a preferred city for %s roles%s." % (
            where, "finance" if track == "finance" else "SWE", tail)
    return best, 0, "%s is outside his preferred cities." % where


def score(verdict, prefs=None, company_key=None, now=None):
    """One eligible verdict -> an entry with score, parts, city, resume."""
    prefs = prefs or DEFAULT_PREFS
    title, company = verdict.get("title") or "", verdict.get("company") or ""
    parts = []

    for pts, label, pat in prefs["work"]:
        if _T(pat).search(title):
            parts.append(_part(pts, "Work type: %s." % label))
            break
    else:
        parts.append(_part(0, "Work type: not recognised from the title."))

    large = _in(company, prefs["large"], company_key)
    if large:
        parts.append(_part(prefs["large_points"], "Large firm."))
    elif _T(prefs["robotics"]).search(company + " " + title):
        parts.append(_part(prefs["robotics_points"], "Small robotics company."))

    fin = is_finance(verdict, prefs, company_key)
    track = "finance" if fin else "swe"
    loc, pts, text = _pick_city(verdict.get("locations") or [], track, prefs)
    parts.append(_part(pts, text))

    sig = verdict.get("signals") or {}
    if sig.get("masters_title"):
        parts.append(_part(prefs["masters_points"], "Title targets Master's students."))
    for f in verdict.get("flags") or []:
        if f["code"] == "bachelors_tag":
            parts.append(_part(prefs["bachelors_tag_points"], "Tagged Bachelor's only."))
        elif f["code"] in prefs["flag_points"]:
            parts.append(_part(prefs["flag_points"][f["code"]],
                               "Needs a check: %s" % f["text"]))

    posted = max([v.get("posted") or 0 for v in verdict.get("variants") or []] or [0])
    if now is not None and posted and now - posted <= prefs["fresh_days"] * 86400:
        parts.append(_part(prefs["fresh_points"], "Posted in the last %d days."
                           % prefs["fresh_days"]))

    resume, why = resume_for(verdict, prefs, company_key)
    variant = None
    if loc is not None:
        variant = next((v for v in verdict.get("variants") or []
                        if v["id"] == loc.get("variant")), None)
    variant = variant or (verdict.get("variants") or [None])[0]
    return {
        "key": verdict.get("key"),
        "company": company,
        "title": title,
        "score": round(sum(p["points"] for p in parts), 2),
        "parts": parts,
        "track": track,
        "city": loc.get("raw") if loc else None,
        "location": loc,
        "variant_id": variant["id"] if variant else None,
        "url": (loc or {}).get("url") or (variant or {}).get("url") or "",
        "resume": resume,
        "resume_why": why,
        "posted": posted,
        "flags": verdict.get("flags") or [],
    }


def rank(verdicts, prefs=None, company_key=None, now=None):
    """Eligible verdicts -> entries, best first. Ties: newer first, then
    company and title, so the order is stable run to run."""
    entries = [score(v, prefs, company_key, now) for v in verdicts if v.get("ok", True)]
    entries.sort(key=lambda e: (-e["score"], -e["posted"], e["company"].casefold(),
                                e["title"].casefold()))
    return entries
