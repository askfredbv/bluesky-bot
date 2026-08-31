# Configure pytest-asyncio to automatically handle async test functions.
# Without this, async tests are silently skipped or throw a RuntimeError.
import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "asyncio: mark test as async"
    )


@pytest.fixture(autouse=True)
def _stub_post_image(monkeypatch):
    """Default every test to NO real image generation. broadcasting_stage now
    always attempts a post image (probability 1.0) and the Curator path falls
    back to a generated card image, both of which call the live Gemini image
    API — the suite must never hit the network. Tests that exercise image
    behaviour override this with their own stub."""
    async def _no_image(*args, **kwargs):
        return None

    monkeypatch.setattr("main.generate_post_image", _no_image, raising=False)
