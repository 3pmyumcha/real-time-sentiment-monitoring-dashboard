"""Shared English stop-word list.

Why this file exists
--------------------
The stop-word list keeps common and project-specific noise from dominating
the trend chart. Trend detection counts tokens in `clean_posts`, so the
cleaning and counting components use the same filter.

This module is the single, explicit stop list used by THREE places so the
cleaning and the counting agree:

    src/processing/cleaner.py   new posts get properly cleaned tokens
    trend.py                    retroactively filters already-stored tokens
    dashboard.py                supplementary 1h term-frequency chart

It is standard-library only (no NLTK / sklearn download), and `MIN_TOKEN_LEN`
plus the ASCII-letter check also drop single characters and punctuation.
"""
from __future__ import annotations

import re

MIN_TOKEN_LEN = 2

# Function words / extremely common terms (NLTK + sklearn style) -------------
_CORE = """
a about above after again against all am an and any are aren as at be because
been before being below between both but by can cannot could couldn did didn do
does doesn doing don down during each few for from further had hadn has hasn
have haven having he her here hers herself him himself his how however i if in
into is isn it its itself just let like me might more most must my myself no nor
not of off on once only or other ought our ours ourselves out over own same
shan she should shouldn so some such than that the their theirs them themselves
then there these they this those through to too under until up very was wasn we
were weren what when where which while who whom why will with won would wouldn
you your yours yourself yourselves
""".split()

# High-frequency weak verbs / adverbs that are not in the classic lists -----
_EXTRA = """
also always among another around become became become been before behind besides
beyond call came come coming could did does done due either else enough especially
etc even ever every everyone everything first get gets getting give gives given
go goes going gone got had happen happened happens has have having here how
however indeed instead keep keeps kept know known less let lets made make makes
making many may might much need needs never new next none nothing now often one
ones only other others otherwise perhaps quite rather really said say says see
seem seems seen several shall show shows showing since still such take takes
taken than though thus together too toward towards try tries trying two upon us
used uses using use usually via want wants was way well went were whatever whenever
whereas whether within without yet able remains remain simply
""".split()

# Hacker News boilerplate: post-type prefixes and URL fragments -------------
_DOMAIN = """
hn ask show tell jobs submit comments comment points ago news ycombinator
http https www com org net html amp quot nbsp thread story item id url
""".split()

STOP_WORDS = frozenset(set(_CORE) | set(_EXTRA) | set(_DOMAIN))

_ASCII_WORD = re.compile(r"[a-z]+")


def is_content_word(word: str) -> bool:
    """True when a token is a meaningful English word worth counting."""
    if not isinstance(word, str):
        return False
    w = word.strip().lower()
    if len(w) < MIN_TOKEN_LEN or w in STOP_WORDS:
        return False
    return bool(_ASCII_WORD.fullmatch(w))


def filter_tokens(words) -> list[str]:
    """Normalise + filter an iterable of tokens."""
    return [w.strip().lower() for w in words if is_content_word(w)]


if __name__ == "__main__":
    print("stop words:", len(STOP_WORDS))
    for probe in ("ai", "gpu", "news", "will", "give", "though", "show", "hn",
                  "claude", "openai", "with", "remains"):
        print(f"  {probe:10s} -> {is_content_word(probe)}")
