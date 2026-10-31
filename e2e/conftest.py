"""Browser smoke tests of the running app (Playwright, headless Chromium).

The app must already run: ``./dev.sh serve`` (backend + built UI on :8000), ``docker compose up``, or the
development pair ``./dev.sh api`` + ``./dev.sh ui`` with ``TEGLAB_URL=http://localhost:4200``.
Every test fails on a browser console error, an uncaught exception or an HTTP error response.
"""

from __future__ import annotations

import os
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

BASE_URL = os.environ.get("TEGLAB_URL", "http://127.0.0.1:8000").rstrip("/")
# software WebGL, so the overlay's WebGL scatter renders the same with or without a GPU
CHROMIUM_ARGS = ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"]


@dataclass
class Tab:
    page: Page
    errors: list[str] = field(default_factory=list)

    def open(self, path: str) -> Page:
        self.page.goto(BASE_URL + path, wait_until="networkidle")
        return self.page


@pytest.fixture(scope="session", autouse=True)
def app_is_running() -> None:
    try:
        urllib.request.urlopen(BASE_URL + "/api/health", timeout=5)
    except OSError as exc:
        pytest.exit(
            f"teglab is not reachable at {BASE_URL} ({exc}). Start it first - ./dev.sh serve or "
            "docker compose up - or point TEGLAB_URL at it.",
            returncode=2,
        )


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    with sync_playwright() as p:
        b = p.chromium.launch(args=CHROMIUM_ARGS)
        yield b
        b.close()


@pytest.fixture()
def tab(browser: Browser) -> Iterator[Callable[..., Tab]]:
    """Factory: ``tab(width=1440, scheme="light")`` opens a fresh browser context that records errors."""
    contexts: list[tuple[BrowserContext, Tab]] = []

    def make(width: int = 1440, scheme: str = "light") -> Tab:
        ctx = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme, accept_downloads=True)
        t = Tab(ctx.new_page())
        t.page.set_default_timeout(30_000)
        t.page.on("console", lambda m: t.errors.append(m.text) if m.type == "error" else None)
        t.page.on("pageerror", lambda e: t.errors.append(f"uncaught: {e}"))
        t.page.on("response", lambda r: t.errors.append(f"HTTP {r.status} {r.url}") if r.status >= 400 else None)
        contexts.append((ctx, t))
        return t

    yield make
    failures = [e for _, t in contexts for e in t.errors]
    for ctx, _ in contexts:
        ctx.close()
    assert not failures, "browser errors:\n" + "\n".join(failures)
