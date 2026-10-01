"""The Simplify top-N tracker: a daily ranked list of internship postings he is eligible for.

One slice of the Today app. batch.py is the only module here that talks to the rest of the app (tracker,
report, env, store) and the only one the Api calls; the others are leaves it composes:

  listings.py   fetch + cache the feed, parse, normalize locations, merge duplicate rows
  filters.py    eligibility from metadata, with a reason for every drop      (pure)
  rank.py       order, explain, pick the city and the resume variant         (pure)
  verify.py     per-posting eligibility read from the posting itself; the only place an LLM key is used
  state.py      SQLite: what the feed has shown us, what we have shown him, exclusions, verdict cache
  batch.py      wires the five together

Data lives in <data folder>/jobs-data/, never next to this source.
"""
