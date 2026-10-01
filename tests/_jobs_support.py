"""Shared helpers for the jobs/ tests: synthetic feed rows and the saved slice of the real feed."""
import os

from jobs import listings

FEED_FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "simplify_slice.json")


def V(company="Acme", title="Software Engineer Intern", locs=("NYC",),
      terms=("Summer 2027",), degrees=("Bachelor's", "Master's"),
      category="Software", vid=None, active=True):
    vid = vid or "simplify:%s-%s-%s" % (company, title, "-".join(locs))
    return {
        "id": vid, "source": "simplify", "company": company, "title": title,
        "category": category, "url": "https://example.com/" + vid,
        "locations": [listings.normalize_location(l) for l in locs],
        "terms": list(terms), "degrees": list(degrees), "sponsorship": "Other",
        "active": active, "visible": True, "posted": 1, "posted_date": "1970-01-01",
        "updated": 1,
    }


def one(*variants):
    ls = listings.dedupe(list(variants))
    assert len(ls) == 1, ls
    return ls[0]
