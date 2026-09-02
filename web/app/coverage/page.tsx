"use client";

import { useState } from "react";

import { CoverageControls, type CoverageRun } from "@/components/CoverageControls";
import { LinkButton, Metric, Notice, PageHeader } from "@/components/ui";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export default function CoveragePage() {
  const [run, setRun] = useState<CoverageRun | null>(null);
  const delta = run
    ? run.installed_coverage_pct - run.effective_coverage_pct
    : null;
  const offline = run ? run.camera_count - run.online_camera_count : 0;

  return (
    <div className="mx-auto max-w-[64rem] p-6">
      <PageHeader
        title="Coverage gap analysis"
        description="How much of a district has camera coverage, and how much of the shortfall is broken cameras rather than absent ones."
      />

      <CoverageControls onRun={setRun} />

      {run && (
        <>
          <div className="mb-6 grid grid-cols-1 gap-3 sm:grid-cols-3">
            <Metric
              label="Installed coverage"
              value={`${run.installed_coverage_pct.toFixed(1)}%`}
              foot={`all ${run.camera_count.toLocaleString()} cameras`}
            />
            <Metric
              label="Effective coverage"
              value={`${run.effective_coverage_pct.toFixed(1)}%`}
              foot={`${run.online_camera_count.toLocaleString()} currently online`}
            />
            <Metric
              label="Lost to outages"
              value={
                <>
                  {delta?.toFixed(1)}
                  <span className="ml-0.5 text-[length:var(--text-lg)]">pp</span>
                </>
              }
              foot={`${offline.toLocaleString()} cameras down`}
              tone={offline > 0 ? "warn" : "default"}
            />
          </div>

          {run.district_located_camera_count > 0 && (
            <div className="mb-6">
              <Notice tone="error" title="Coverage is not spatially reliable">
                <strong>
                  {run.district_located_camera_count.toLocaleString()} of{" "}
                  {run.camera_count.toLocaleString()} cameras
                </strong>{" "}
                have no surveyed position — their location came from a place name
                and resolves to one point for the whole district. The totals hold,
                but the spatial distribution of coverage does not.
              </Notice>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-4">
            <LinkButton
              variant="primary"
              href={`${API}/api/v1/coverage/runs/${run.id}/report.html`}
              target="_blank"
              rel="noreferrer"
            >
              Open full report
            </LinkButton>
            <LinkButton href="/map">View on map</LinkButton>
            <span className="text-[length:var(--text-xs)] text-ink-muted">
              {run.total_cells.toLocaleString()} cells at {run.hex_edge_m}m edge —
              this run is now selectable as a map overlay.
            </span>
          </div>
        </>
      )}
    </div>
  );
}
