
# Configure pytest-asyncio to automatically handle async test functions.
# Without this, async tests are silently skipped or throw a RuntimeError.
def pytest_configure(config):
    config.addinivalue_line(
        "markers", "asyncio: mark test as async"
    )
