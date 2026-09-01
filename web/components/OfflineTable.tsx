"use client";

import { useEffect, useState } from "react";

import { apiFetch } from "@/lib/session";

// The subset of OfflineCamera in app/schemas/health.py that the table renders. The
// server has already ordered these by status_since ascending, which is longest
// downtime first, so nothing here re-sorts them.
type OfflineCamera = {
  camera_id: string;
  camera_uid: string;
  name: string | null;
  department_code: string | null;
  status_since: string | null;
  downtime_seconds: number;
};

function formatDowntime(seconds: number): string {
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} hr`;
  return `${Math.floor(seconds / 86400)} days`;
}

function severity(seconds: number): string {
  if (seconds > 7 * 86400) return "bg-red-100 text-red-800";
  if (seconds > 86400) return "bg-amber-100 text-amber-800";
  return "bg-slate-100 text-slate-700";
}

export function OfflineTable() {
  const [rows, setRows] = useState<OfflineCamera[]>([]);

  useEffect(() => {
    // downtime_seconds is computed server-side against now(), so it only advances
    // when we re-ask. Thirty seconds keeps the clock honest without hammering the
    // registry, and the interval is cleared on unmount so a navigation away does not
    // leave a timer fetching into a dead component.
    let cancelled = false;
    const load = () =>
      apiFetch("/api/v1/health/offline")
        .then((r) => r.json())
        .then((page) => {
          if (!cancelled) setRows(page.items);
        })
        .catch(() => {
          if (!cancelled) setRows([]);
        });
    load();
    const timer = setInterval(load, 30_000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  return (
    <table data-testid="offline-table" className="w-full text-sm">
      <thead className="border-b text-left text-xs uppercase text-slate-500">
        <tr>
          <th className="py-2">Camera</th>
          <th>Department</th>
          <th>Name</th>
          <th>Offline since</th>
          <th className="text-right">Down for</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.camera_id} data-testid="offline-row" className="border-b last:border-0">
            <td className="py-2 font-mono text-xs">{row.camera_uid}</td>
            <td>{row.department_code ?? "—"}</td>
            <td className="text-slate-600">{row.name ?? "—"}</td>
            <td className="text-slate-500">
              {row.status_since ? new Date(row.status_since).toLocaleString() : "—"}
            </td>
            <td className="text-right">
              <span
                data-testid="offline-downtime"
                className={`rounded px-2 py-0.5 text-xs ${severity(row.downtime_seconds)}`}
              >
                {formatDowntime(row.downtime_seconds)}
              </span>
            </td>
          </tr>
        ))}
        {rows.length === 0 && (
          <tr>
            <td colSpan={5} className="py-6 text-center text-slate-400">
              No cameras currently offline.
            </td>
          </tr>
        )}
      </tbody>
    </table>
  );
}
