from src.utils import canonical_url


def test_arxiv_abs_bare():
    assert canonical_url("https://arxiv.org/abs/2510.22977") == "arxiv:2510.22977"


def test_arxiv_abs_with_version():
    assert canonical_url("https://arxiv.org/abs/2510.22977v2") == "arxiv:2510.22977"


def test_arxiv_pdf_form():
    assert canonical_url("https://arxiv.org/pdf/2510.22977") == "arxiv:2510.22977"


def test_arxiv_html_form():
    assert canonical_url("https://arxiv.org/html/2510.22977v1") == "arxiv:2510.22977"


def test_arxiv_with_query_and_uppercase_host():
    assert (
        canonical_url("https://ARXIV.org/abs/2510.22977?utm_source=x")
        == "arxiv:2510.22977"
    )


def test_arxiv_http_scheme_collapses():
    assert canonical_url("http://arxiv.org/abs/2510.22977/") == "arxiv:2510.22977"


def test_non_arxiv_forces_https():
    assert (
        canonical_url("http://Example.com/Path/To/Article")
        == "https://example.com/Path/To/Article"
    )


def test_non_arxiv_preserves_path_case():
    # RFC 3986: path is case-sensitive. Two distinct paths must not collapse.
    a = canonical_url("https://github.com/OWNER/Repo")
    b = canonical_url("https://github.com/owner/repo")
    assert a != b


def test_non_arxiv_strips_query_and_fragment():
    assert (
        canonical_url("https://example.com/post?utm_source=twitter#top")
        == "https://example.com/post"
    )


def test_non_arxiv_strips_trailing_slash():
    assert (
        canonical_url("https://example.com/path/")
        == "https://example.com/path"
    )


def test_empty_input():
    assert canonical_url("") == ""


def test_whitespace_trimmed():
    assert (
        canonical_url("  https://example.com/post  ")
        == "https://example.com/post"
    )
