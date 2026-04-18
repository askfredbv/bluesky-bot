"""Bluesky rich-text facets — byte-offset annotations that make URLs and
hashtags clickable in the AT Protocol record.

Bluesky counts facet offsets in UTF-8 bytes, not Unicode code points.
This matters for posts with accented characters (Dutch, French), emoji,
or any non-ASCII content — the naive character-index approach produces
offsets that are off by one or more bytes.
"""

import re
from typing import List
from atproto import models


# URL regex — stops at whitespace, closing brackets, and trailing punctuation
# that is almost never part of a URL.
_URL_PATTERN = re.compile(r'https?://[^\s)\]]+')

# Trailing characters we strip from a URL match because they are virtually
# always sentence punctuation rather than part of the URL itself.
_URL_TRAILING_STRIP = '.,;:!?'

# Hashtag regex — Bluesky requires the first character to be alphabetic
# (numeric-only hashtags are rejected) and the tag body to be alphanumeric.
# (?<!\w) prevents matching inside words like "C#" or email fragments.
_HASHTAG_PATTERN = re.compile(r'(?<!\w)#([A-Za-z][A-Za-z0-9]{1,63})')


def _byte_offset(text: str, char_index: int) -> int:
    """Return the UTF-8 byte offset corresponding to a character index."""
    return len(text[:char_index].encode('utf-8'))


def build_facets(text: str) -> List[models.AppBskyRichtextFacet.Main]:
    """Scan text for URLs and hashtags, return facets with UTF-8 byte offsets.

    Returns an empty list when no matches are found — callers should pass
    `facets=None` to send_post when the list is empty, not an empty array.
    """
    facets: List[models.AppBskyRichtextFacet.Main] = []

    for match in _URL_PATTERN.finditer(text):
        url = match.group(0).rstrip(_URL_TRAILING_STRIP)
        if not url:
            continue
        start = match.start()
        end = start + len(url)  # character offset after stripping trailing punct
        facets.append(models.AppBskyRichtextFacet.Main(
            index=models.AppBskyRichtextFacet.ByteSlice(
                byte_start=_byte_offset(text, start),
                byte_end=_byte_offset(text, end),
            ),
            features=[models.AppBskyRichtextFacet.Link(uri=url)],
        ))

    for match in _HASHTAG_PATTERN.finditer(text):
        tag = match.group(1)
        facets.append(models.AppBskyRichtextFacet.Main(
            index=models.AppBskyRichtextFacet.ByteSlice(
                byte_start=_byte_offset(text, match.start()),
                byte_end=_byte_offset(text, match.end()),
            ),
            features=[models.AppBskyRichtextFacet.Tag(tag=tag)],
        ))

    facets.sort(key=lambda f: f.index.byte_start)
    return facets
