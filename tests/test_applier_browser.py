"""Browser bootstrap. The agent must degrade with a usable instruction when
Playwright is absent, not traceback — it is an optional heavy dependency
(MEASURED at 344 MB for the Chromium build it installs, not the "~150MB" this
docstring and `browser.py`'s both used to say) and the rest of the platform must
keep working without it."""
from __future__ import annotations
import builtins
import pytest
from agents.job_applier import browser


def test_is_available_false_when_import_fails(monkeypatch):
    real_import = builtins.__import__

    def fake(name, *a, **k):
        if name.startswith("playwright"):
            raise ImportError("no playwright")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    assert browser.is_available() is False


def test_hint_names_both_install_steps():
    hint = browser.PLAYWRIGHT_MISSING_HINT
    assert ".venv/bin/pip install playwright" in hint
    assert "playwright install chromium" in hint
    assert "uv" not in hint, "uv is not installed on this machine"


def test_launch_raises_a_clear_error_when_unavailable(monkeypatch):
    monkeypatch.setattr(browser, "is_available", lambda: False)
    with pytest.raises(RuntimeError) as exc:
        browser.launch_context()
    assert "playwright install chromium" in str(exc.value)


def test_close_quietly_really_does_have_the_callers_its_docstring_claims():
    """`close_quietly` justifies living in this module by counting its callers, so
    the count is measured rather than asserted.

    It said "two modules … at four call sites"; it is THREE modules and FIVE:
    `graph.release_browser`, `session.hand_over`, `session._close`, and both
    cleanup paths in `nodes/fetch_form.fetch_form_node` (the `Exception` one and
    the `BaseException` one). The undercount mattered in the direction that gets a
    helper inlined: "only two callers" is the argument for pushing it back into the
    graph, which is where the identical-semantics requirement came from.
    """
    import ast
    import pathlib

    package = pathlib.Path(browser.__file__).parent
    sites: dict[str, int] = {}
    for path in sorted(package.rglob("*.py")):
        tree = ast.parse(path.read_text())
        n = sum(
            1 for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "close_quietly"
        )
        if n:
            sites[str(path.relative_to(package))] = n
    assert sites == {"graph.py": 1, "nodes/fetch_form.py": 2, "session.py": 2}
    assert len(sites) == 3 and sum(sites.values()) == 5
    source = pathlib.Path(browser.__file__).read_text()
    assert "THREE modules need identical semantics at FIVE call sites" in source


def test_the_chromium_download_size_in_the_docstring_is_the_measured_one():
    """The docstring's job is to tell a reader why the import is lazy, and "~150MB"
    understated it by more than half — MEASURED at 344 MB for the Chromium build
    Playwright installs (`chromium-1228`). Asserted as prose rather than by
    stat-ing the cache, because the cache is not present on every machine and a
    test that skipped itself there would let the number rot again.
    """
    import pathlib

    source = pathlib.Path(browser.__file__).read_text()
    assert "344 MB" in source
    # The old figure survives in the sentence that corrects it, deliberately, so
    # this asserts the correction is stated rather than that the number is gone.
    assert 'not the "~150MB" this' in source


def test_profile_dir_is_under_data_and_gitignored():
    assert browser.PROFILE_DIR.name == "browser_profile"
    assert browser.PROFILE_DIR.parent.name == "data"


def test_missing_chromium_binary_is_a_distinct_error_from_missing_package(monkeypatch):
    """Package present but Chromium binary absent is a different failure from
    the package itself being absent — it is fixed by a different (shorter)
    command, so the two error messages must not be identical."""
    import playwright.sync_api as pw_sync_api

    monkeypatch.setattr(browser, "is_available", lambda: True)

    class FakeChromium:
        @staticmethod
        def launch_persistent_context(*_a, **_k):
            raise pw_sync_api.Error(
                "BrowserType.launch_persistent_context: Executable doesn't exist at "
                "/fake/path/chromium-1228/chrome-mac/Chromium.app\n"
                "Please run the following command to download new browsers:\n"
                "    playwright install"
            )

    class FakePlaywright:
        chromium = FakeChromium

        def stop(self):
            pass

    class FakeContextManager:
        def start(self):
            return FakePlaywright()

    monkeypatch.setattr(pw_sync_api, "sync_playwright", lambda: FakeContextManager())

    with pytest.raises(RuntimeError) as exc:
        browser.launch_context()

    message = str(exc.value)
    assert "playwright install chromium" in message
    assert message != browser.PLAYWRIGHT_MISSING_HINT
    assert ".venv/bin/pip install playwright" not in message, (
        "chromium-missing message should not repeat the pip-install step — "
        "the package is already there, only the browser binary is missing"
    )


def _fake_sync_playwright():
    """Build a fake `sync_playwright()` call chain that records context-close
    and driver-stop calls, without touching a real browser or subprocess."""
    calls = {"context_closed": 0, "driver_stopped": 0}

    class FakeContext:
        def close(self):
            calls["context_closed"] += 1

    class FakeChromium:
        @staticmethod
        def launch_persistent_context(*_a, **_k):
            return FakeContext()

    class FakePlaywright:
        chromium = FakeChromium

        def stop(self):
            calls["driver_stopped"] += 1

    class FakeContextManager:
        def start(self):
            return FakePlaywright()

    return FakeContextManager, calls


def _patch_fake_playwright(monkeypatch):
    import playwright.sync_api as pw_sync_api

    monkeypatch.setattr(browser, "is_available", lambda: True)
    fake_cm, calls = _fake_sync_playwright()
    monkeypatch.setattr(pw_sync_api, "sync_playwright", lambda: fake_cm())
    return calls


def test_context_manager_closes_context_and_stops_driver_on_success(monkeypatch):
    calls = _patch_fake_playwright(monkeypatch)

    with browser.launch_context() as ctx:
        assert ctx is not None

    assert calls["context_closed"] == 1
    assert calls["driver_stopped"] == 1


def test_context_manager_tears_down_when_body_raises(monkeypatch):
    calls = _patch_fake_playwright(monkeypatch)

    with pytest.raises(ValueError):
        with browser.launch_context():
            raise ValueError("boom")

    assert calls["context_closed"] == 1
    assert calls["driver_stopped"] == 1


def test_close_is_idempotent(monkeypatch):
    calls = _patch_fake_playwright(monkeypatch)

    ctx = browser.launch_context()
    ctx.close()
    ctx.close()  # must not explode, must not double-close/double-stop

    assert calls["context_closed"] == 1
    assert calls["driver_stopped"] == 1


def test_close_stops_the_playwright_driver(monkeypatch):
    """Dedicated mutation-test anchor for the driver-leak finding: closing a
    ManagedBrowserContext must stop the Playwright driver process, not just
    close the visible browser context. If `ManagedBrowserContext.close()` is
    ever changed to close only `self._context` and drop the
    `self._playwright.stop()` call, this test must fail."""
    calls = _patch_fake_playwright(monkeypatch)

    ctx = browser.launch_context()
    ctx.close()

    assert calls["driver_stopped"] == 1, (
        "the Playwright driver process was not stopped on close() — "
        "it will leak a driver subprocess per launch_context() call"
    )
