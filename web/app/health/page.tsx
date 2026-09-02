"use client";

import { useState } from "react";

import { OfflineTable } from "@/components/OfflineTable";
import { Button, Notice, PageHeader } from "@/components/ui";
import { apiFetch } from "@/lib/session";

export default function HealthPage() {
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Bumped after a probe so the table refetches rather than showing the
  // readings the probe just replaced.
  const [reload, setReload] = useState(0);

  async function probe() {
    setBusy(true);
    setNotice(null);
    setError(null);
    try {
      const response = await apiFetch("/api/v1/health/probe", { method: "POST" });
      if (!response.ok) {
        throw new Error(
          response.status === 403
            ? "Probing needs the health:write scope."
            : `Probe failed (${response.status})`,
        );
      }
      const body = await response.json();
      setNotice(
        body.checked === 0
          ? "No cameras with a reachable endpoint to probe."
          : `Checked ${body.checked} camera${body.checked === 1 ? "" : "s"}; ` +
              `${body.changed} changed state.`,
      );
      setReload((n) => n + 1);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Probe failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-[76rem] p-6">
      <PageHeader
        title="Camera health"
        description="Which cameras are not watching, and how long they have been down. Sorted by the longest outage, because that is the one somebody has stopped noticing."
        actions={
          <Button busy={busy} onClick={probe}>
            Probe now
          </Button>
        }
      />

      {/* Stated on the page, because "every camera is unknown" otherwise looks
          like a fleet-wide outage rather than a worker that has not run. */}
      <p className="mb-4 text-[length:var(--text-xs)] text-ink-faint">
        Cameras are probed on a schedule by the background worker. A registry
        showing every camera as <strong className="font-medium">unknown</strong>{" "}
        has simply not been probed yet — start the worker, or press Probe now.
      </p>

      {notice && (
        <div className="mb-4">
          <Notice tone="success">{notice}</Notice>
        </div>
      )}
      {error && (
        <div className="mb-4">
          <Notice tone="error">{error}</Notice>
        </div>
      )}

      <OfflineTable key={reload} />
    </div>
  );
}
