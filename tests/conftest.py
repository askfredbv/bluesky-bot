# Configure pytest-asyncio to automatically handle async test functions.
# Without this, async tests are silently skipped or throw a RuntimeError.
import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "asyncio: mark test as async"
    )


@pytest.fixture(autouse=True)
def _stub_post_image(monkeypatch):
    """Default every test to NO real image generation. broadcasting_stage attempts
    a post image, and the Curator path falls back to a generated card image, both
    of which call the live Gemini image API — the suite must never hit the
    network. Tests that exercise image behaviour override this with their own
    stub."""
    async def _no_image(*args, **kwargs):
        return None

    monkeypatch.setattr("main.generate_post_image", _no_image, raising=False)


@pytest.fixture(autouse=True)
def _stub_broadcast_reservation(monkeypatch):
    """Default every test to an in-memory pre-broadcast reservation.

    broadcasting_stage reserves the Curator's story before it broadcasts, and the
    real reservation is a locked read-modify-write of seen_articles.json — so
    without this stub a dozen existing broadcast tests would start writing (and
    locking) the repo's live state file. Reservation behaviour itself is covered
    by the dedicated tests in test_broadcast_reservation.py, which override these
    with their own stubs."""
    def _reserved(link):
        return True

    def _settled(link, *, delivered):
        return None

    monkeypatch.setattr("main.reserve_broadcast", _reserved, raising=False)
    monkeypatch.setattr("main.settle_broadcast", _settled, raising=False)


@pytest.fixture(autouse=True)
def _deterministic_image_gate(monkeypatch):
    """Pin the image probability to 1.0 so the suite is deterministic.

    IMAGE_GENERATION_PROBABILITY was 1.0, which made `random() < p` always true —
    every image-path test passed reliably by accident. Dropping it to 0.85 turned
    those into ~15%-flaky tests without touching them (a broadcasting_stage test
    duly started failing about one run in seven). A CI suite that fails
    intermittently is worse than none, so image attempts are forced on by default
    here and the probability itself is covered by the dedicated
    `_should_attempt_image` / `_should_attempt_curator_fallback_image` unit tests,
    which monkeypatch the constant themselves and so override this."""
    monkeypatch.setattr("main.IMAGE_GENERATION_PROBABILITY", 1.0, raising=False)
