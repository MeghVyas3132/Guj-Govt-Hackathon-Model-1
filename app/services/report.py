"""The gap-analysis report.

Written to be read by someone who will act on it. The headline is the delta
between coverage that exists on paper and coverage that is actually watching,
because that is the number a police officer can do something about this week.

Every limitation the method has is stated in the document rather than omitted.
A coverage figure presented without its assumptions invites a decision the
figure cannot support.
"""

from html import escape

from app.models.coverage import CoverageRun

_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Gap Analysis — {boundary}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
          margin: 0; color: #1e293b; background: #fff; }}
  .page {{ max-width: 860px; margin: 0 auto; padding: 48px 32px 72px; }}
  header {{ border-bottom: 2px solid #0f2d5e; padding-bottom: 16px; margin-bottom: 28px; }}
  .eyebrow {{ font-size: 11px; letter-spacing: .08em; text-transform: uppercase;
              color: #0f2d5e; font-weight: 700; }}
  h1 {{ font-size: 26px; margin: 6px 0 4px; }}
  .sub {{ color: #64748b; font-size: 13px; }}
  .grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin: 28px 0; }}
  .tile {{ border: 1px solid #e2e8f0; border-radius: 10px; padding: 16px; }}
  .tile .label {{ font-size: 10px; letter-spacing: .06em; text-transform: uppercase;
                  color: #64748b; font-weight: 600; }}
  .tile .value {{ font-size: 30px; font-weight: 700; margin: 6px 0 2px;
                  font-variant-numeric: tabular-nums; }}
  .tile .foot {{ font-size: 11px; color: #64748b; }}
  .delta {{ background: #fffbeb; border-color: #fde68a; }}
  .delta .value {{ color: #b45309; }}
  h2 {{ font-size: 15px; margin: 32px 0 10px; }}
  p {{ font-size: 14px; line-height: 1.65; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; margin-bottom: 8px; }}
  th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid #e2e8f0; }}
  th {{ font-size: 10px; letter-spacing: .06em; text-transform: uppercase; color: #64748b; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .method {{ background: #f8fafc; border-left: 3px solid #0f2d5e;
             padding: 18px 22px; font-size: 13px; line-height: 1.6; margin-top: 8px; }}
  .method h2 {{ margin-top: 0; }}
  .method li {{ margin-bottom: 7px; }}
  .warn {{ background: #fef2f2; border-left-color: #dc2626; }}
  footer {{ margin-top: 40px; padding-top: 16px; border-top: 1px solid #e2e8f0;
            font-size: 11px; color: #94a3b8; }}
  @media print {{ .page {{ padding: 16px; }} .tile {{ break-inside: avoid; }} }}
</style>
</head>
<body>
<div class="page">
  <header>
    <div class="eyebrow">Sentinel CCTV Registry &middot; Coverage gap analysis</div>
    <h1>{boundary}</h1>
    <p class="sub">Run {run_id} &middot; {finished} &middot; hexagon edge {edge:.0f}&nbsp;m
       &middot; {cells:,} cells analysed</p>
  </header>

  <div class="grid">
    <div class="tile">
      <div class="label">Installed coverage</div>
      <div class="value">{installed:.1f}%</div>
      <div class="foot">all {cameras:,} cameras</div>
    </div>
    <div class="tile">
      <div class="label">Effective coverage</div>
      <div class="value">{effective:.1f}%</div>
      <div class="foot">{online:,} currently online</div>
    </div>
    <div class="tile delta">
      <div class="label">Lost to outages</div>
      <div class="value">{delta:.1f}<span style="font-size:18px">pp</span></div>
      <div class="foot">{offline:,} cameras down</div>
    </div>
  </div>

  <h2>What this says</h2>
  <p>
    {boundary} has cameras installed to cover <strong>{installed:.1f}%</strong> of its
    area under the assumptions below. Because <strong>{offline:,}</strong> of
    <strong>{cameras:,}</strong> cameras are currently offline, only
    <strong>{effective:.1f}%</strong> is actually being watched right now &mdash; a live
    shortfall of <strong>{delta:.1f} percentage points</strong>.
    {recovery}
  </p>

  <h2>Cells by coverage band</h2>
  <table>
    <thead><tr><th>Band</th><th>Definition</th><th class="num">Cells</th>
      <th class="num">Share</th></tr></thead>
    <tbody>{bands}</tbody>
  </table>

  <h2>Where repair recovers the most</h2>
  <p class="sub" style="margin-top:-4px">
    Cells with coverage on paper that nothing is currently watching, worst first.
    These are repair decisions, not purchase decisions.
  </p>
  <table>
    <thead><tr><th>#</th><th class="num">Installed</th><th class="num">Effective</th>
      <th class="num">Lost</th><th class="num">Cameras reaching cell</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
  <p class="sub">{zero_note}</p>

  {caveat}

  <div class="method">
    <h2>Method, and what it cannot tell you</h2>
    <p>
      The boundary is tessellated into hexagons of {edge:.0f}&nbsp;m edge length. Each
      camera contributes a footprint: types marked omnidirectional contribute a full
      circle of their nominal range, and directional types a sector spanning their
      recorded bearing plus or minus half their field of view. Ranges and fields of view
      come from the camera-type vocabulary, so they can be corrected without changing
      code. A cell's coverage is the area of its intersection with the union of all
      footprints, divided by the cell area. Cells at or above {covered:.0%} are counted
      covered, at or above {gap:.0%} partial, and below that a gap.
    </p>
    <p>This estimate is deliberately conservative about what it claims. It is:</p>
    <ul>
      <li><strong>Two-dimensional.</strong> Elevation is not modelled.</li>
      <li><strong>Without terrain or building occlusion.</strong> A wall between a camera
          and a cell does not reduce the estimate, so real coverage is <em>lower</em>
          than shown here.</li>
      <li><strong>Based on nominal range</strong> per camera type, not on optics, sensor
          size, mounting height or lighting.</li>
      <li><strong>Dependent on the recorded bearing.</strong> {omni_note}</li>
    </ul>
    <p>
      Treat these figures as a comparative planning aid &mdash; which zones are worse
      than others, and how much is recoverable by repair rather than purchase &mdash;
      not as a measurement of what any individual camera can see.
    </p>
  </div>

  <footer>
    Generated by the Sentinel CCTV Registry from run {run_id}.
    Figures reflect camera status at the time of the run.
  </footer>
</div>
</body>
</html>
"""

_BANDS = [
    ("covered", "at or above the covered threshold"),
    ("partial", "between the gap and covered thresholds"),
    ("gap", "below the gap threshold"),
]


def render_coverage_report(
    run: CoverageRun,
    outage_cells: list[dict],
    band_counts: dict[str, int] | None = None,
    zero_coverage_cells: int = 0,
) -> str:
    band_counts = band_counts or {}
    total_cells = run.total_cells or 0

    def band_row(name: str, definition: str) -> str:
        count = band_counts.get(name, 0)
        share = (count / total_cells * 100) if total_cells else 0.0
        return (
            f"<tr><td><strong>{name}</strong></td><td>{definition}</td>"
            f'<td class="num">{count:,}</td>'
            f'<td class="num">{share:.1f}%</td></tr>'
        )

    bands = "".join(band_row(name, definition) for name, definition in _BANDS)

    rows = "".join(
        f"<tr><td>{index}</td>"
        f'<td class="num">{cell["installed_fraction"]:.1%}</td>'
        f'<td class="num">{cell["effective_fraction"]:.1%}</td>'
        f'<td class="num">{cell["lost"]:.1%}</td>'
        f'<td class="num">{cell["camera_count"]}</td></tr>'
        for index, cell in enumerate(outage_cells, start=1)
    ) or (
        '<tr><td colspan="5">No cell in this area loses coverage to an outage. '
        "Every gap here needs a camera, not a repair.</td></tr>"
    )

    zero_note = (
        f"A further <strong>{zero_coverage_cells:,}</strong> cells "
        f"({zero_coverage_cells / total_cells * 100:.0f}% of the area) have no camera "
        "reaching them at all. Those are gaps no repair can close."
        if zero_coverage_cells and total_cells
        else ""
    )

    offline = max(run.camera_count - run.online_camera_count, 0)
    delta = run.installed_coverage_pct - run.effective_coverage_pct

    recovery = (
        f"Restoring those {offline:,} cameras recovers that coverage without "
        "installing a single new device."
        if offline
        else "Every camera in this area is currently reporting, so there is no "
        "coverage to recover by repair alone."
    )

    omni = run.assumed_omnidirectional_count
    omni_note = (
        f"<strong>{omni:,}</strong> directional cameras here have no recorded azimuth "
        "or field of view and were treated as omnidirectional, which "
        "<em>overstates</em> their contribution. Recording bearings for those cameras "
        "is the cheapest way to make this report more accurate."
        if omni
        else "Every directional camera in this area has a recorded bearing."
    )

    district_located = getattr(run, "district_located_camera_count", 0) or 0
    caveat = ""
    if district_located:
        share = district_located / run.camera_count * 100 if run.camera_count else 0
        caveat = (
            '<div class="method warn"><h2>Read this before acting on the figures</h2>'
            f"<p><strong>{district_located:,} of {run.camera_count:,} cameras "
            f"({share:.0f}%)</strong> in this area have no surveyed position. Their "
            "location was derived from a place name in the source data and resolves to "
            "a single representative point for the whole district, so their coverage "
            "appears concentrated at one spot where no camera physically stands.</p>"
            "<p>The totals above are therefore reliable as a count of cameras and as a "
            "measure of how much coverage is lost to outages, but the <em>spatial "
            "distribution</em> of coverage in this area is not. Supplying surveyed "
            "coordinates for those cameras corrects it; the registry updates them in "
            "place without re-onboarding.</p></div>"
        )

    return _TEMPLATE.format(
        boundary=escape(run.boundary_name or "Selected area"),
        run_id=run.id,
        finished=(run.finished_at or run.created_at).strftime("%d %B %Y, %H:%M UTC"),
        edge=run.hex_edge_m,
        cells=total_cells,
        installed=run.installed_coverage_pct,
        effective=run.effective_coverage_pct,
        delta=delta,
        cameras=run.camera_count,
        online=run.online_camera_count,
        offline=offline,
        covered=run.covered_threshold,
        gap=run.gap_threshold,
        bands=bands,
        rows=rows,
        zero_note=zero_note,
        recovery=recovery,
        omni_note=omni_note,
        caveat=caveat,
    )
