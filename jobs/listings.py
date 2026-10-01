"""Internship feeds: fetch, cache, parse, normalize, dedupe.

Text in, dicts out. Standard library only. Malformed input returns empty
rather than raising, the same contract as icsparse.py.

Pure apart from fetch(). Nothing here knows about eligibility, ranking or
what the owner has applied to; those live in filters.py, rank.py and
batch.py.

A *variant* is one upstream row: one req, one apply link, one or more
cities. A *listing* is what the owner thinks of as one posting: every live
variant with the same company and title, merged. SimplifyJobs files the
same role as several rows for different cities (Optiver SWE Intern is three
rows: Chicago, Austin, London), and a single row can list several cities
including non-US ones. Keeping the variants lets the ranker pick the
preferred city *and its own apply link* instead of whichever row came first.
"""

import datetime
import json
import os
import re
import tempfile
import urllib.error
import urllib.request

__all__ = [
    "FEEDS",
    "fetch",
    "load_cached",
    "parse",
    "normalize_location",
    "dedupe",
    "load",
]


# Every feed this module knows how to read. Only Simplify today; Speedyapply
# and vanshb03 are candidates (proposal §3.5), and each would add a parser.
FEEDS = {
    "simplify": {
        "url": "https://raw.githubusercontent.com/SimplifyJobs/"
               "Summer2027-Internships/dev/.github/scripts/listings.json",
        "format": "simplify-json",
    },
}

_UA = "today-jobs/1 (+https://github.com/JasonYao-QY/JobsToday)"


# --------------------------------------------------------------------------
# fetch + cache

def _atomic_write(path, data):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _meta_path(path):
    return path + ".meta.json"


def _read_meta(path):
    try:
        with open(_meta_path(path), encoding="utf-8") as f:
            m = json.load(f)
        return m if isinstance(m, dict) else {}
    except (OSError, ValueError):
        return {}


def fetch(feed, cache_dir, timeout=60, opener=None, now=None):
    """Download one feed into cache_dir, conditionally on its ETag.

    Returns {feed, path, changed, fetched_at, bytes}. On "304 Not Modified"
    the cached file is kept and changed is False. Network errors propagate:
    the caller (a BackgroundTask) is where failure is reported, and the
    cached copy, if any, stays usable through load_cached().

    `opener` is injectable for tests; it takes a urllib Request and returns
    a response-like object with .status, .headers and .read().
    """
    spec = FEEDS[feed]
    path = os.path.join(cache_dir, feed + ".json")
    meta = _read_meta(path) if os.path.exists(path) else {}
    req = urllib.request.Request(spec["url"], headers={"User-Agent": _UA})
    if meta.get("etag"):
        req.add_header("If-None-Match", meta["etag"])
    opener = opener or urllib.request.urlopen
    now = now or datetime.datetime.now(datetime.timezone.utc)
    stamp = now.isoformat(timespec="seconds")
    try:
        resp = opener(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code == 304:
            meta["checked_at"] = stamp
            _atomic_write(_meta_path(path), json.dumps(meta).encode())
            return {"feed": feed, "path": path, "changed": False,
                    "fetched_at": meta.get("fetched_at"),
                    "bytes": os.path.getsize(path)}
        raise
    with resp:
        status = getattr(resp, "status", 200)
        if status == 304:
            body = None
        else:
            body = resp.read()
        etag = resp.headers.get("ETag") if resp.headers else None
    if body is None:
        meta["checked_at"] = stamp
        _atomic_write(_meta_path(path), json.dumps(meta).encode())
        return {"feed": feed, "path": path, "changed": False,
                "fetched_at": meta.get("fetched_at"),
                "bytes": os.path.getsize(path)}
    # Refuse to replace a good cache with something that does not parse.
    if not parse(body, spec["format"]):
        raise ValueError("%s: downloaded feed did not parse; cache kept" % feed)
    _atomic_write(path, body)
    meta = {"etag": etag, "fetched_at": stamp, "checked_at": stamp,
            "url": spec["url"]}
    _atomic_write(_meta_path(path), json.dumps(meta).encode())
    return {"feed": feed, "path": path, "changed": True,
            "fetched_at": stamp, "bytes": len(body)}


def load_cached(feed, cache_dir):
    """Parsed variants from the cached copy of one feed, [] if none."""
    path = os.path.join(cache_dir, feed + ".json")
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return []
    return parse(raw, FEEDS[feed]["format"])


# --------------------------------------------------------------------------
# locations

_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut",
    "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida",
    "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky",
    "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "PR": "Puerto Rico", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
_STATE_BY_NAME = {v.lower(): k for k, v in _STATES.items()}

# Simplify's shorthand for a few cities.
_ALIASES = {
    "sf": ("San Francisco", "CA"),
    "south sf": ("South San Francisco", "CA"),
    "nyc": ("New York", "NY"),
    "la": ("Los Angeles", "CA"),
}

_US_WORDS = {"united states", "usa", "us", "u.s.", "united states of america"}

_CA_PROVINCES = {"AB", "BC", "MB", "NB", "NL", "NS", "ON", "PE", "QC", "SK"}

# Metro areas the owner states preferences about (proposal §6). Anything
# else is metro None, which is a fact, not a penalty; rank.py decides.
_METROS = {
    "bay_area": {
        ("CA", c) for c in (
            "San Francisco", "South San Francisco", "San Jose", "Santa Clara",
            "Mountain View", "Palo Alto", "Sunnyvale", "Menlo Park",
            "Redwood City", "San Mateo", "Cupertino", "Oakland", "Berkeley",
            "Fremont", "Milpitas", "Foster City", "Emeryville", "Burlingame",
            "San Bruno", "Los Gatos", "Pleasanton", "Hayward", "Newark",
            "Los Altos", "Belmont", "San Carlos", "Brisbane", "Alameda",
            "Campbell", "Livermore", "Dublin", "San Ramon", "Walnut Creek",
            "Millbrae", "Daly City", "Half Moon Bay",
        )
    },
    "seattle": {
        ("WA", c) for c in (
            "Seattle", "Bellevue", "Redmond", "Kirkland", "Bothell",
            "Issaquah", "Renton", "Everett", "Tukwila",
        )
    },
    "nyc": {("NY", c) for c in (
        "New York", "Brooklyn", "Queens", "Long Island City", "Bronx",
    )} | {("NJ", c) for c in ("Jersey City", "Hoboken", "Newark")},
    "chicago": {("IL", c) for c in ("Chicago", "Evanston", "Oak Brook")},
    "boston": {
        ("MA", c) for c in (
            "Boston", "Cambridge", "Somerville", "Waltham", "Burlington",
            "Lexington", "Watertown", "Woburn", "Needham", "Newton",
            "Bedford", "Quincy", "Wellesley", "Brookline",
        )
    },
}
# Newark NJ is NYC metro; Newark CA is Bay Area. Keyed by (state, city) so
# the two never collide.


def _metro(state, city):
    for name, members in _METROS.items():
        if (state, city) in members:
            return name
    return None


def normalize_location(raw):
    """One upstream location string -> a dict.

    {raw, city, state, country, us, remote, metro}

    `us` is True, False, or None when the string cannot be placed. None is
    not False: filters.py treats an unplaceable location as unknown, not as
    non-US, so a garbled city never silently drops a real role.
    """
    out = {"raw": raw, "city": None, "state": None, "country": None,
           "us": None, "remote": False, "metro": None}
    if not isinstance(raw, str) or not raw.strip():
        return out
    s = " ".join(raw.split())
    low = s.lower()

    if low.startswith("remote"):
        out["remote"] = True
        rest = re.sub(r"^remote\s*(in|-|,)?\s*", "", low).strip()
        if not rest or rest in _US_WORDS:
            out.update(country="US", us=True)
        elif rest in _STATE_BY_NAME:
            out.update(country="US", us=True, state=_STATE_BY_NAME[rest])
        else:
            out.update(country=rest.title(), us=False)
        return out

    if low in _ALIASES:
        city, st = _ALIASES[low]
        out.update(city=city, state=st, country="US", us=True,
                   metro=_metro(st, city))
        return out

    if low in _US_WORDS:
        out.update(country="US", us=True)
        return out

    if low in _STATE_BY_NAME:                       # "Texas", "Washington"
        out.update(state=_STATE_BY_NAME[low], country="US", us=True)
        return out

    # "Dubai - United Arab Emirates"
    if " - " in s and "," not in s:
        city, country = [p.strip() for p in s.split(" - ", 1)]
        out.update(city=city, country=country, us=country.lower() in _US_WORDS)
        return out

    parts = [p.strip() for p in s.split(",") if p.strip()]
    if len(parts) == 1:
        # A bare country or region we do not recognise: "Canada", "United
        # Kingdom". A bare US city with no state would also land here; we
        # cannot tell those apart, so mark it non-US only for known foreign
        # names and unknown otherwise.
        if low in {"canada", "united kingdom", "uk", "ireland", "india",
                   "germany", "france", "singapore", "japan", "china",
                   "mexico", "netherlands", "switzerland", "australia",
                   "israel", "poland", "spain", "italy"}:
            out.update(country=s, us=False)
        else:
            out["city"] = s
        return out

    last = parts[-1]
    up = last.upper()
    if up in _STATES:
        # "Long Island City, Queens, NY": the first part is the place; the
        # middle is a borough or county. Prefer the first, but use the middle
        # for metro lookup too, so a neighbourhood resolves to its city.
        city = parts[0]
        metro = _metro(up, city)
        if metro is None and len(parts) > 2:
            for p in parts[1:-1]:
                metro = _metro(up, p)
                if metro:
                    break
        out.update(city=city, state=up, country="US", us=True, metro=metro)
        return out
    if last.lower() in _US_WORDS:
        st = parts[-2].upper() if len(parts) > 2 else None
        if st and st not in _STATES:
            st = _STATE_BY_NAME.get(parts[-2].lower())
        out.update(city=parts[0], state=st, country="US", us=True,
                   metro=_metro(st, parts[0]) if st else None)
        return out
    if last.lower() in _STATE_BY_NAME:              # "Long Island, New York"
        st = _STATE_BY_NAME[last.lower()]
        out.update(city=parts[0], state=st, country="US", us=True,
                   metro=_metro(st, parts[0]))
        return out
    if len(parts) >= 3 and parts[-2].upper() in _CA_PROVINCES:
        out.update(city=parts[0], state=parts[-2].upper(), country=last,
                   us=False)
        return out
    # "London, UK", "Dublin, Ireland", "Melbourn, Royston, UK"
    out.update(city=parts[0], country=last, us=False)
    return out


# --------------------------------------------------------------------------
# parse

_CATEGORY = {"software engineering": "Software"}


def _date(epoch):
    if not isinstance(epoch, (int, float)) or epoch <= 0:
        return None
    try:
        return datetime.datetime.fromtimestamp(
            epoch, datetime.timezone.utc).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _str_list(v):
    if not isinstance(v, list):
        return []
    seen, out = set(), []
    for x in v:
        if isinstance(x, str) and x.strip() and x.strip() not in seen:
            seen.add(x.strip())
            out.append(x.strip())
    return out


def _simplify_variant(r):
    if not isinstance(r, dict):
        return None
    vid, company, title = r.get("id"), r.get("company_name"), r.get("title")
    if not all(isinstance(x, str) and x.strip() for x in (vid, company, title)):
        return None
    cat = r.get("category") if isinstance(r.get("category"), str) else ""
    cat = _CATEGORY.get(cat.strip().lower(), cat.strip())
    posted = r.get("date_posted") if isinstance(r.get("date_posted"), int) else 0
    updated = r.get("date_updated") if isinstance(r.get("date_updated"), int) else 0
    return {
        "id": "simplify:" + vid.strip(),
        "source": "simplify",
        "company": " ".join(company.split()),
        "title": " ".join(title.split()),
        "category": cat or None,
        "url": r.get("url") if isinstance(r.get("url"), str) else "",
        "locations": [normalize_location(x) for x in _str_list(r.get("locations"))],
        "terms": _str_list(r.get("terms")),
        "degrees": _str_list(r.get("degrees")),
        "sponsorship": r.get("sponsorship") if isinstance(r.get("sponsorship"), str) else None,
        "active": r.get("active") is True,
        "visible": r.get("is_visible") is not False,
        "posted": posted,
        "posted_date": _date(posted),
        "updated": updated,
    }


_PARSERS = {"simplify-json": _simplify_variant}


def parse(raw, fmt="simplify-json"):
    """Feed bytes or text -> list of variant dicts. Bad rows are skipped;
    unparseable input returns []."""
    row = _PARSERS.get(fmt)
    if row is None:
        return []
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    out = []
    for r in data:
        v = row(r)
        if v is not None:
            out.append(v)
    return out


# --------------------------------------------------------------------------
# dedupe

_DASHES = re.compile(r"[‐-―−]")


def _norm(s):
    s = _DASHES.sub("-", s.casefold())
    s = re.sub(r"[^\w\s/&+-]", " ", s)
    return " ".join(s.split())


def _default_company_key(name):
    return _norm(name)


def dedupe(variants, company_key=None, live_only=True):
    """Merge variants that are the same posting: same company, same title.

    company_key: the function that decides "same company". Defaults to a
    casefold-and-collapse; batch.py passes tracker.company_key so that
    cross-feed spellings (JPMorganChase / JPMorgan Chase) merge too.

    live_only drops inactive and invisible variants *before* merging, so a
    closed London row cannot drag an open NYC row's data around.

    Each listing: {key, company, title, category, variants, locations,
    terms, degrees, first_posted, last_posted, ids}. `locations` is the
    ordered union over variants, each tagged with the variant id it came
    from, so a chosen city carries its own apply link.
    """
    company_key = company_key or _default_company_key
    groups, order = {}, []
    for v in variants:
        if live_only and not (v.get("active") and v.get("visible")):
            continue
        k = (company_key(v["company"]), _norm(v["title"]))
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(v)

    out = []
    for k in order:
        vs = sorted(groups[k], key=lambda v: (v["posted"], v["id"]))
        locs, seen_loc = [], set()
        terms, degrees = [], []
        for v in vs:
            for loc in v["locations"]:
                lk = (loc["city"], loc["state"], loc["country"], loc["remote"])
                if lk in seen_loc:
                    continue
                seen_loc.add(lk)
                locs.append(dict(loc, variant=v["id"], url=v["url"]))
            for t in v["terms"]:
                if t not in terms:
                    terms.append(t)
            for d in v["degrees"]:
                if d not in degrees:
                    degrees.append(d)
        # Category: the most common among variants, ties to the earliest.
        cats = [v["category"] for v in vs if v["category"]]
        cat = max(cats, key=lambda c: (cats.count(c), -cats.index(c))) if cats else None
        out.append({
            "key": "%s|%s" % k,
            "company": vs[0]["company"],
            "title": vs[0]["title"],
            "category": cat,
            "variants": vs,
            "ids": [v["id"] for v in vs],
            "locations": locs,
            "terms": terms,
            "degrees": degrees,
            "first_posted": vs[0]["posted"],
            "last_posted": vs[-1]["posted"],
            "first_posted_date": vs[0]["posted_date"],
            "last_posted_date": vs[-1]["posted_date"],
        })
    return out


def load(cache_dir, feeds=None, company_key=None):
    """Every cached feed, parsed and deduped into listings. No network."""
    variants = []
    for feed in feeds or list(FEEDS):
        variants.extend(load_cached(feed, cache_dir))
    return dedupe(variants, company_key=company_key)
