import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: subprocess integration tests (may be slow)")


@pytest.fixture(autouse=True)
def fail_on_legacy_check_failures(request):
    """Make legacy check()/FAIL harnesses behave like pytest assertions."""
    module = getattr(request, "module", None)
    if module is None or not hasattr(module, "FAIL"):
        yield
        return

    before = getattr(module, "FAIL", 0)
    yield
    after = getattr(module, "FAIL", 0)
    if after <= before:
        return

    details = []
    for attr in ("FAILURES", "ERRORS"):
        entries = getattr(module, attr, None)
        if entries:
            details.extend(str(entry) for entry in entries[-(after - before):])
    detail_text = "\n".join(details) if details else f"{after - before} legacy check() failure(s)"
    pytest.fail(detail_text)
