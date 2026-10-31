"""The ANSYS pipeline page end to end: parameter run, nodal contact checks, overlay calibration."""

from __future__ import annotations

import pytest
from playwright.sync_api import expect

# where the synthetic demo picture shows the unit cell (exact, from the renderer)
CELL_CORNERS_PX = ((198.8, 710.0), (1001.2, 90.0))
EXACT_PLACEMENT = (-1.390, 3.894, 6.581, 4.387)  # x, y, sizex, sizey [mm]

PLACEMENT_JS = """() => { const el = document.querySelector('app-overlay-viewer .ov-plot');
  const im = el && el.layout && el.layout.images && el.layout.images[0];
  return im ? [im.x, im.y, im.sizex, im.sizey] : null; }"""
HAS_PICTURE_JS = """() => { const el = document.querySelector('app-overlay-viewer .ov-plot');
  return !!(el && el.layout && el.layout.images && el.layout.images.length); }"""


def test_parameter_run_flags_the_inactive_contacts(tab):
    page = tab().open("/pipeline")
    panel = page.locator("app-parameters-panel")
    panel.locator(".examples li", has_text="A0/A5/A10").get_by_role("button", name="Add").click()
    expect(panel.get_by_role("heading", name="Recognized parameters")).to_be_visible()
    variants = panel.locator(".fields select")
    for k, variant in enumerate(["A0", "A5", "A10"]):
        variants.nth(k).select_option(variant)
    panel.get_by_role("button", name="Run pipeline").click()
    expect(panel.get_by_role("heading", name="Findings")).to_be_visible()
    systems = panel.locator("table.data").nth(1).locator("tbody tr")
    expect(systems).to_have_count(3)
    expect(systems.nth(2)).to_contain_text("Inactive")
    expect(panel.locator(".issues")).to_contain_text("cold leg ends read 0.75 K lower")


@pytest.mark.parametrize(
    "example, badge, delta_t",
    [("contacts applied", "As specified", "50.00"), ("bonded", "Not applied", None)],
)
def test_nodal_contact_checks(tab, example, badge, delta_t):
    page = tab().open("/pipeline?tab=nodal")
    panel = page.locator("app-nodal-panel")
    panel.locator(".examples li", has_text=example).get_by_role("button", name="Load").click()
    expect(panel.get_by_role("heading", name="Layer stack")).to_be_visible()
    panel.get_by_role("button", name="Evaluate profile").click()
    expect(panel.get_by_role("heading", name="Contact checks")).to_be_visible()
    # four leg contacts, thermal and electrical verdict each
    expect(panel.locator("table .badge", has_text=badge)).to_have_count(8)
    if delta_t:
        expect(panel.locator("app-stat").first).to_contain_text(delta_t)


def test_overlay_places_calibrates_and_follows_the_view(tab):
    t = tab()
    page = t.open("/pipeline?tab=overlay")
    page.get_by_role("button", name="Load the synthetic demo").click()
    page.wait_for_function(HAS_PICTURE_JS)
    assert page.evaluate(PLACEMENT_JS) == pytest.approx(EXACT_PLACEMENT, abs=2e-3)

    # recalibrate with two clicks on the part corners: the placement must stay (within a pixel or two)
    viewer = page.locator("app-overlay-viewer")
    viewer.get_by_role("button", name="Recalibrate").click()
    img = viewer.locator(".cal-img img")
    page.wait_for_function(
        "() => { const i = document.querySelector('app-overlay-viewer .cal-img img');"
        " return i && i.complete && i.naturalWidth > 0; }"
    )
    img.evaluate("i => i.scrollIntoView({block: 'center'})")
    box = img.bounding_box()
    natural_w, natural_h = img.evaluate("i => [i.naturalWidth, i.naturalHeight]")
    for px, py in CELL_CORNERS_PX:
        page.mouse.click(box["x"] + px * box["width"] / natural_w, box["y"] + py * box["height"] / natural_h)
    expect(viewer.locator(".status")).to_contain_text("two clicks on the part corners")
    assert page.evaluate(PLACEMENT_JS) == pytest.approx(EXACT_PLACEMENT, abs=0.05)
    viewer.get_by_role("button", name="Done").click()

    # the picture follows the view and the quantity
    view, quantity, picture = (viewer.locator("select").nth(k) for k in range(3))
    view.select_option("top")
    expect(picture).to_have_value("")  # no top-view picture in the export
    view.select_option("front")
    quantity.select_option("voltage_v")
    expect(picture).to_have_value("dataset:front__voltage.png")

    # the table view of this chart: the plotted nodes as CSV
    with page.expect_download() as download:
        viewer.get_by_role("button", name="Download nodes (CSV)").click()
    lines = open(download.value.path(), encoding="utf-8").read().splitlines()
    assert lines[0] == "body,x [mm],y [mm],Electric voltage [V]" and len(lines) > 100
