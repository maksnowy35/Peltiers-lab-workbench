"""Every page renders, in both themes and at phone width."""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

PAGES = ["/", "/import", "/pipeline", "/analysis", "/model", "/optimizer", "/materials", "/lab"]


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_every_page_renders(tab, scheme):
    t = tab(scheme=scheme)
    for path in PAGES:
        page = t.open(path)
        expect(page.locator("main h1").first).to_be_visible()
        assert page.title().endswith("teglab"), path


@pytest.mark.parametrize("path", ["/", "/import", "/pipeline", "/pipeline?tab=nodal", "/pipeline?tab=overlay"])
def test_phone_width_has_no_horizontal_scroll(tab, path):
    page = tab(width=390).open(path)
    page.wait_for_timeout(500)
    scroll_width, client_width = page.evaluate(
        "[document.documentElement.scrollWidth, document.documentElement.clientWidth]"
    )
    assert scroll_width <= client_width + 1


def test_tabs_are_deep_links(tab):
    page = tab().open("/pipeline?tab=nodal")
    expect(page.get_by_role("tab", name="Nodal fields")).to_have_attribute("aria-selected", "true")
    page.get_by_role("tab", name="Overlay").click()
    expect(page).to_have_url(re.compile(r"/pipeline\?tab=overlay$"))
