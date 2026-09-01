from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.models.coverage import CoverageRun
from app.services.report import render_coverage_report


def make_run(**overrides) -> CoverageRun:
    values = {
        "id": uuid4(),
        "boundary_name": "Bhavnagar",
        "hex_edge_m": 500.0,
        "covered_threshold": 0.6,
        "gap_threshold": 0.2,
        "status": "done",
        "total_cells": 11_843,
        "installed_coverage_pct": 4.28,
        "effective_coverage_pct": 2.72,
        "camera_count": 4_305,
        "online_camera_count": 2_671,
        "assumed_omnidirectional_count": 312,
        "district_located_camera_count": 0,
        "finished_at": datetime(2026, 9, 1, 10, 30, tzinfo=UTC),
    }
    values.update(overrides)
    run = CoverageRun(**values)
    run.created_at = values["finished_at"]
    return run


BANDS = {"covered": 120, "partial": 480, "gap": 11_243}
OUTAGE_CELLS = [
    {"installed_fraction": 0.82, "effective_fraction": 0.10, "camera_count": 4, "lost": 0.72},
    {"installed_fraction": 0.31, "effective_fraction": 0.12, "camera_count": 2, "lost": 0.19},
]


# ---- the headline ----

def test_both_coverage_figures_appear():
    html = render_coverage_report(make_run(), OUTAGE_CELLS, BANDS)
    assert "4.3%" in html or "4.28" in html
    assert "2.7%" in html or "2.72" in html
    assert "Bhavnagar" in html


def test_the_outage_delta_is_stated():
    html = render_coverage_report(make_run(), OUTAGE_CELLS, BANDS)
    assert "1.6pp" in html or "1.6<" in html


def test_the_offline_count_is_stated():
    html = render_coverage_report(make_run(), OUTAGE_CELLS, BANDS)
    assert "1,634" in html  # 4305 - 2671


def test_a_fully_online_area_does_not_promise_recoverable_coverage():
    """Telling an officer to repair cameras when none are broken wastes their time."""
    html = render_coverage_report(
        make_run(camera_count=100, online_camera_count=100), OUTAGE_CELLS, BANDS
    )
    assert "no coverage to recover" in html
    assert "Restoring those" not in html


# ---- honesty about method ----

@pytest.mark.parametrize(
    "phrase",
    ["two-dimensional", "occlusion", "nominal range", "recorded bearing", "lower"],
)
def test_the_limitations_are_disclosed(phrase):
    html = render_coverage_report(make_run(), OUTAGE_CELLS, BANDS).lower()
    assert phrase.lower() in html


def test_cameras_without_a_bearing_are_flagged_as_overstating_coverage():
    html = render_coverage_report(make_run(), OUTAGE_CELLS, BANDS)
    assert "312" in html
    assert "overstates" in html


def test_an_area_where_every_bearing_is_known_says_so_instead():
    html = render_coverage_report(
        make_run(assumed_omnidirectional_count=0), OUTAGE_CELLS, BANDS
    )
    assert "has a recorded bearing" in html
    assert "overstates" not in html


# ---- the district-centroid caveat ----

def test_district_located_cameras_trigger_a_prominent_caveat():
    """Their coverage lands where no camera stands. A reader acting on the spatial
    distribution must be told before they act, not in a footnote."""
    html = render_coverage_report(
        make_run(camera_count=11, online_camera_count=6, district_located_camera_count=10),
        OUTAGE_CELLS,
        BANDS,
    )
    assert "no surveyed position" in html
    assert "10 of 11" in html
    assert "91%" in html
    assert "spatial" in html


def test_no_caveat_when_every_camera_is_surveyed():
    html = render_coverage_report(make_run(), OUTAGE_CELLS, BANDS)
    assert "no surveyed position" not in html


# ---- structure ----

def test_band_counts_and_shares_are_rendered():
    html = render_coverage_report(make_run(), OUTAGE_CELLS, BANDS)
    assert "11,243" in html
    assert "94.9%" in html  # 11243 / 11843


def test_an_area_with_no_outage_losses_says_gaps_need_cameras_not_repairs():
    html = render_coverage_report(make_run(), [], BANDS)
    assert "needs a camera, not a repair" in html


def test_the_repair_table_ranks_by_coverage_lost():
    html = render_coverage_report(make_run(), OUTAGE_CELLS, BANDS)
    assert "Where repair recovers the most" in html
    assert "72.0%" in html  # the largest loss, listed first
    assert html.index("72.0%") < html.index("19.0%")


def test_zero_coverage_cells_are_reported_as_a_count_not_a_list():
    """Listing 10,785 identical zero rows is noise; the count is the information."""
    html = render_coverage_report(make_run(), OUTAGE_CELLS, BANDS, zero_coverage_cells=10_785)
    assert "10,785" in html
    assert "no repair can close" in html


def test_no_zero_coverage_note_when_there_are_none():
    html = render_coverage_report(make_run(), OUTAGE_CELLS, BANDS, zero_coverage_cells=0)
    assert "no repair can close" not in html


def test_a_run_with_no_cells_does_not_divide_by_zero():
    html = render_coverage_report(make_run(total_cells=0), [], {})
    assert "0.0%" in html


def test_the_boundary_name_is_escaped():
    html = render_coverage_report(
        make_run(boundary_name="<script>alert(1)</script>"), OUTAGE_CELLS, BANDS
    )
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_the_document_is_well_formed_html():
    html = render_coverage_report(make_run(), OUTAGE_CELLS, BANDS)
    assert html.startswith("<!doctype html>")
    assert html.rstrip().endswith("</html>")
    assert html.count("<table") == html.count("</table>")
