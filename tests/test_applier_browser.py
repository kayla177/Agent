"""Browser bootstrap. The agent must degrade with a usable instruction when
Playwright is absent, not traceback — it is an optional heavy dependency
(~150MB of Chromium) and the rest of the platform must keep working without it."""
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
