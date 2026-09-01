"use client";

import { useEffect, useState } from "react";

import {
  EmptyState,
  Metric,
  Mono,
  Notice,
  SectionTitle,
  SkeletonRows,
  TD,
  TH,
  THead,
  TR,
  Table,
} from "@/components/ui";
import { apiJson } from "@/lib/session";

type OfflineCamera = {
  camera_id: string;
  camera_uid: string;
  name: string | null;
  department_code: string | null;
  status_since: string | null;
  downtime_seconds: number;
};

type Summary = {
  total: number;
  online: number;
  offline: number;
  maintenance: number;
  offline_over_24h: number;
  offline_over_7d: number;
};

function formatDowntime(seconds: number): string {
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} hr`;
  const days = Math.floor(seconds / 86400);
  return `${days} day${days === 1 ? "" : "s"}`;
}

/** Severity by duration, not by status: everything in this table is offline. */
function severity(seconds: number) {
  if (seconds > 7 * 86400) return "offline";
  if (seconds > 86400) return "maintenance";
  return "unknown";
}

export function OfflineTable() {
  const [rows, setRows] = useState<OfflineCamera[] | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const load = () => {
      Promise.all([
        apiJson<{ items: OfflineCamera[] }>("/api/v1/health/offline"),
        apiJson<Summary>("/api/v1/health/summary"),
      ])
        .then(([page, s]) => {
          if (!active) return;
          setRows(page.items);
          setSummary(s);
        })
        .catch((e) => active && setError(e.message));
    };
    load();
    const timer = setInterval(load, 30_000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);

  return (
    <>
      {error && (
        <div className="mb-4">
          <Notice tone="error">{error}</Notice>
        </div>
      )}

      <div className="mb-6 grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(7.5rem,1fr))]">
        <Metric label="Registered" value={summary?.total.toLocaleString() ?? "—"} />
        <Metric
          label="Online"
          value={summary?.online.toLocaleString() ?? "—"}
          foot={
            summary
              ? `${Math.round((summary.online / Math.max(summary.total, 1)) * 100)}% of the fleet`
              : undefined
          }
        />
        <Metric label="Offline" value={summary?.offline.toLocaleString() ?? "—"} tone="warn" />
        <Metric label="Maintenance" value={summary?.maintenance.toLocaleString() ?? "—"} />
        <Metric
          label="Down over 24h"
          value={summary?.offline_over_24h.toLocaleString() ?? "—"}
        />
        <Metric
          label="Down over 7 days"
          value={summary?.offline_over_7d.toLocaleString() ?? "—"}
          tone="warn"
        />
      </div>

      <SectionTitle>Offline, longest first</SectionTitle>

      <Table>
        <THead>
          <tr>
            <TH>Camera</TH>
            <TH>Department</TH>
            <TH>Name</TH>
            <TH>Offline since</TH>
            <TH align="right">Down for</TH>
          </tr>
        </THead>
        <tbody>
          {rows === null && <SkeletonRows rows={6} cols={5} />}

          {rows?.map((row) => {
            const key = severity(row.downtime_seconds);
            return (
              <TR key={row.camera_id}>
                <TD>
                  <a
                    href={`/cameras/${row.camera_id}`}
                    className="whitespace-nowrap font-mono text-[length:var(--text-xs)] text-[var(--brand)] underline-offset-2 hover:underline"
                  >
                    {row.camera_uid}
                  </a>
                </TD>
                <TD className="text-ink-muted">{row.department_code ?? "—"}</TD>
                <TD>{row.name ?? <span className="text-ink-faint">—</span>}</TD>
                <TD className="text-ink-muted">
                  {/* status_since from the API, not derived from Date.now(): a value
                      computed during render changes on every paint and is impure. */}
                  <Mono>
                    {row.status_since
                      ? new Date(row.status_since).toLocaleString()
                      : "—"}
                  </Mono>
                </TD>
                <TD align="right">
                  <span
                    className="tabular rounded-full px-2 py-0.5 text-[length:var(--text-2xs)] font-medium"
                    style={{
                      color: `var(--state-${key}-ink)`,
                      background: `var(--state-${key}-bg)`,
                    }}
                  >
                    {formatDowntime(row.downtime_seconds)}
                  </span>
                </TD>
              </TR>
            );
          })}

          {rows?.length === 0 && (
            <tr>
              <td colSpan={5}>
                <EmptyState title="Every camera is reporting">
                  Nothing is offline right now. This table refreshes every 30 seconds.
                </EmptyState>
              </td>
            </tr>
          )}
        </tbody>
      </Table>
    </>
  );
}
