"""Explore: governed, specific questions over Demonstration University.

A question becomes a plan of reviewed analyses (``catalog``), the plan runs in
code against a read-only connection to the school database (``execute``),
and the answer is written from the computed tables (``answer``). The model,
when one is configured, only ever sees the catalog (to plan) and the
computed aggregate tables (to reword the answer). It never computes a number
and never sees a student row. See docs/EXPLORE.md.
"""
