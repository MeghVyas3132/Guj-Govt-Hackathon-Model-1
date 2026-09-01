"use client";

import { useState } from "react";

import { CoverageControls, type CoverageRun } from "@/components/CoverageControls";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export default function CoveragePage() {
  const [run, setRun] = useState<CoverageRun | null>(null);
  const delta = run
    ? run.installed_coverage_pct - run.effective_coverage_pct
    : null;
  const offline = run ? run.camera_count - run.online_camera_count : 0;

  return (
    <main className="mx-auto max-w-5xl p-8">
      <h1 className="mb-1 text-2xl font-semibold">Coverage gap analysis</h1>
      <p className="mb-6 text-sm text-slate-500">
        How much of a district has camera coverage, and how much of the shortfall is
        broken cameras rather than absent ones.
      </p>

      <CoverageControls onRun={setRun} />

      {run && (
        <>
          <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-3">
            <div className="rounded-lg border p-4">
              <p className="text-xs font-semibold uppercase text-slate-500">
                Installed coverage
              </p>
              <p className="mt-1 text-3xl font-bold tabular-nums">
                {run.installed_coverage_pct.toFixed(1)}%
              </p>
              <p className="text-xs text-slate-500">
                all {run.camera_count.toLocaleString()} cameras
              </p>
            </div>
            <div className="rounded-lg border p-4">
              <p className="text-xs font-semibold uppercase text-slate-500">
                Effective coverage
              </p>
              <p className="mt-1 text-3xl font-bold tabular-nums">
                {run.effective_coverage_pct.toFixed(1)}%
              </p>
              <p className="text-xs text-slate-500">
                {run.online_camera_count.toLocaleString()} currently online
              </p>
            </div>
            <div className="rounded-lg border border-amber-200 bg-amber-50 p-4">
              <p className="text-xs font-semibold uppercase text-amber-700">
                Lost to outages
              </p>
              <p className="mt-1 text-3xl font-bold tabular-nums text-amber-800">
                {delta?.toFixed(1)}
                <span className="text-lg">pp</span>
              </p>
              <p className="text-xs text-amber-700">
                {offline.toLocaleString()} cameras down
              </p>
            </div>
          </div>

          {run.district_located_camera_count > 0 && (
            <p className="mb-6 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-800">
              <strong>
                {run.district_located_camera_count.toLocaleString()} of{" "}
                {run.camera_count.toLocaleString()} cameras
              </strong>{" "}
              have no surveyed position — their location came from a place name and
              resolves to one point for the whole district. The totals hold, but the
              spatial distribution of coverage does not.
            </p>
          )}

          <div className="flex items-center gap-4">
            <a
              href={`${API}/api/v1/coverage/runs/${run.id}/report.html`}
              target="_blank"
              rel="noreferrer"
              className="inline-block rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white"
            >
              Open full report
            </a>
            <span className="text-xs text-slate-500">
              {run.total_cells.toLocaleString()} cells at {run.hex_edge_m}m edge
            </span>
          </div>
        </>
      )}
    </main>
  );
}
