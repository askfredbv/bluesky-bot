"""Tests for Bluesky rich-text facet generation.

Bluesky uses UTF-8 byte offsets, not character offsets. These tests exist
mostly to guard against off-by-one errors when non-ASCII characters (Dutch
accented letters, emoji) appear before a URL or hashtag.
"""

from src.facets import build_facets


def _find_facet_for(facets, uri_or_tag):
    """Return the first facet whose feature matches a URI or tag."""
    for f in facets:
        for feat in f.features:
            if getattr(feat, 'uri', None) == uri_or_tag or getattr(feat, 'tag', None) == uri_or_tag:
                return f
    return None


def test_empty_text_returns_empty_list():
    assert build_facets("") == []


def test_plain_text_with_no_links_returns_empty_list():
    assert build_facets("Just words, nothing clickable here.") == []


def test_url_at_start_gets_correct_byte_range():
    text = "https://askfred.be is the site"
    facets = build_facets(text)
    assert len(facets) == 1
    f = facets[0]
    assert f.index.byte_start == 0
    assert f.index.byte_end == len("https://askfred.be")
    assert f.features[0].uri == "https://askfred.be"


def test_url_in_middle_gets_correct_byte_range():
    text = "See https://askfred.be for details"
    facets = build_facets(text)
    assert len(facets) == 1
    assert text.encode('utf-8')[facets[0].index.byte_start:facets[0].index.byte_end] == b"https://askfred.be"


def test_url_at_end_with_trailing_period_excludes_period():
    text = "Source: https://askfred.be."
    facets = build_facets(text)
    assert len(facets) == 1
    assert facets[0].features[0].uri == "https://askfred.be"
    # Byte range must not include the trailing period
    assert text.encode('utf-8')[facets[0].index.byte_start:facets[0].index.byte_end] == b"https://askfred.be"


def test_hashtag_captured_without_leading_hash():
    text = "Some thoughts on #AIEthics today"
    facets = build_facets(text)
    tag_facet = _find_facet_for(facets, "AIEthics")
    assert tag_facet is not None
    # Byte range should cover "#AIEthics" (with the #)
    byte_slice = text.encode('utf-8')[tag_facet.index.byte_start:tag_facet.index.byte_end]
    assert byte_slice == b"#AIEthics"


def test_hashtag_after_dutch_accented_characters_has_correct_byte_offset():
    """Regression: ü/é take 2 UTF-8 bytes — char index != byte index."""
    text = "Früher #Nachdenken"
    facets = build_facets(text)
    assert len(facets) == 1
    byte_slice = text.encode('utf-8')[facets[0].index.byte_start:facets[0].index.byte_end]
    assert byte_slice == b"#Nachdenken"


def test_url_after_emoji_has_correct_byte_offset():
    """Emoji take 4 UTF-8 bytes — byte offset must account for this."""
    text = "🔗 https://askfred.be"
    facets = build_facets(text)
    assert len(facets) == 1
    byte_slice = text.encode('utf-8')[facets[0].index.byte_start:facets[0].index.byte_end]
    assert byte_slice == b"https://askfred.be"


def test_multiple_urls_and_one_hashtag_all_captured():
    text = "See https://a.com and https://b.com about #AI"
    facets = build_facets(text)
    assert len(facets) == 3
    # Sorted by byte_start
    assert facets[0].features[0].uri == "https://a.com"
    assert facets[1].features[0].uri == "https://b.com"
    assert facets[2].features[0].tag == "AI"


def test_numeric_only_hashtag_is_ignored():
    """Bluesky rejects tags that start with a digit."""
    text = "Posting #123 for fun"
    facets = build_facets(text)
    assert facets == []


def test_hashtag_inside_word_is_ignored():
    """Don't match C# or foo#bar — # must follow a non-word char or start."""
    text = "I love C#Sharp programming"
    facets = build_facets(text)
    assert facets == []
