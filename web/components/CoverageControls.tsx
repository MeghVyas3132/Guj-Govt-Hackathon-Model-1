"use client";

import { useEffect, useState } from "react";

import { apiFetch } from "@/lib/session";

export type CoverageRun = {
  id: string;
  boundary_name: string | null;
  status: string;
  hex_edge_m: number;
  total_cells: number;
  installed_coverage_pct: number;
  effective_coverage_pct: number;
  camera_count: number;
  online_camera_count: number;
  assumed_omnidirectional_count: number;
  district_located_camera_count: number;
};

type Boundary = { id: string; name: string };
type Estimate = { estimated_cells: number; max_cells: number; within_budget: boolean };

export function CoverageControls({
  onRun,
}: {
  onRun: (run: CoverageRun) => void;
}) {
  const [boundaries, setBoundaries] = useState<Boundary[]>([]);
  const [boundaryId, setBoundaryId] = useState("");
  const [edge, setEdge] = useState(300);
  const [estimate, setEstimate] = useState<Estimate | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    apiFetch("/api/v1/boundaries?level=district")
      .then((r) => r.json())
      .then((data: Boundary[]) => {
        if (!active) return;
        setBoundaries(data);
        if (data.length) setBoundaryId(data[0].id);
      })
      .catch(() => active && setError("Could not load districts."));
    return () => {
      active = false;
    };
  }, []);

  // Estimating before running is what stops someone picking a resolution that
  // would take minutes and look like a hang.
  useEffect(() => {
    if (!boundaryId) return;
    let active = true;
    const timer = setTimeout(() => {
      apiFetch(
        `/api/v1/coverage/estimate?boundary_id=${boundaryId}&hex_edge_m=${edge}`,
      )
        .then((r) => r.json())
        .then((data) => active && setEstimate(data))
        .catch(() => active && setEstimate(null));
    }, 200);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [boundaryId, edge]);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      const response = await apiFetch("/api/v1/coverage/runs", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ boundary_id: boundaryId, hex_edge_m: edge }),
      });
      const body = await response.json();
      if (!response.ok) {
        setError(typeof body.detail === "string" ? body.detail : "Run failed.");
        return;
      }
      onRun(body);
    } catch {
      setError("Could not reach the registry.");
    } finally {
      setBusy(false);
    }
  }

  const overBudget = estimate ? !estimate.within_budget : false;

  return (
    <div className="mb-6 rounded-[6px] border border-line bg-surface p-4">
      <div className="flex flex-wrap items-end gap-5">
        <label className="text-sm">
          <span className="mb-1 block text-[length:var(--text-xs)] font-medium text-ink-muted">
            District
          </span>
          <select
            className="rounded border px-2 py-1.5"
            value={boundaryId}
            onChange={(e) => setBoundaryId(e.target.value)}
          >
            {boundaries.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}
              </option>
            ))}
          </select>
        </label>

        <label className="text-sm">
          <span className="mb-1 block text-[length:var(--text-xs)] font-medium text-ink-muted">
            Hexagon edge · {edge} m
          </span>
          <input
            type="range"
            min={50}
            max={1000}
            step={50}
            value={edge}
            onChange={(e) => setEdge(Number(e.target.value))}
            className="w-56"
          />
        </label>

        <button
          onClick={run}
          disabled={busy || !boundaryId || overBudget}
          className="inline-flex h-8 items-center rounded-[4px] bg-[var(--brand)] px-3 text-[length:var(--text-sm)] font-medium text-white transition-colors duration-[var(--duration)] hover:bg-[var(--brand-hover)] disabled:opacity-40"
        >
          {busy ? "Running…" : "Run gap analysis"}
        </button>
      </div>

      {estimate && (
        <p
          className={`mt-3 text-xs ${overBudget ? "text-[var(--state-offline-ink)]" : "text-ink-muted"}`}
        >
          {estimate.estimated_cells.toLocaleString()} cells estimated
          {overBudget
            ? ` — over the ${estimate.max_cells.toLocaleString()} budget. Increase the edge length.`
            : ""}
        </p>
      )}

      {error && <p className="mt-3 text-sm text-[var(--state-offline-ink)]">{error}</p>}
    </div>
  );
}
