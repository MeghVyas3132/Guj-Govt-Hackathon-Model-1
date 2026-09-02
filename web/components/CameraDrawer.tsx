"use client";

import { useEffect, useState } from "react";

import { apiFetch } from "@/lib/session";

// The subset of StreamEndpointRead the drawer renders. `reachability` is the field
// that matters operationally: public_cdn opens on any network, direct_ip only where
// the gateway lets those ports out, so it is shown next to every URL rather than
// buried.
type StreamEndpoint = {
  protocol: string;
  url: string;
  reachability: string;
  requires_auth: boolean;
  codec: string | null;
  resolution: string | null;
  is_primary: boolean;
};

export type SelectedCamera = {
  id: string;
  camera_uid: string;
  status: string;
  camera_type: string;
};

const REACHABILITY_STYLES: Record<string, string> = {
  public_cdn: "bg-[var(--state-online-bg)] text-[var(--state-online-ink)]",
  direct_ip: "bg-[var(--state-maintenance-bg)] text-[var(--state-maintenance-ink)]",
};

export function CameraDrawer({
  camera,
  onClose,
}: {
  camera: SelectedCamera | null;
  onClose: () => void;
}) {
  // The result carries the id it was fetched for rather than being cleared on the way
  // in. Clearing would mean a setState in the effect body -- a cascading render React
  // warns about -- and tagging is the stronger guard anyway: a slow response for the
  // previously selected camera can never paint into the drawer for the current one.
  const [result, setResult] = useState<{
    id: string;
    streams: StreamEndpoint[];
  } | null>(null);

  useEffect(() => {
    if (!camera) return;
    const id = camera.id;
    let cancelled = false;
    apiFetch(`/api/v1/cameras/${id}/streams`)
      .then((r) => r.json())
      .then((streams: StreamEndpoint[]) => {
        if (!cancelled) setResult({ id, streams });
      })
      .catch(() => {
        if (!cancelled) setResult({ id, streams: [] });
      });
    return () => {
      cancelled = true;
    };
  }, [camera]);

  // null means "still loading", so it does not render the same as a camera that
  // genuinely has no endpoints registered.
  const streams =
    camera && result?.id === camera.id ? result.streams : null;

  if (!camera) return null;

  return (
    <aside
      data-testid="camera-drawer"
      className="absolute right-0 top-0 z-20 h-full w-96 overflow-y-auto bg-white p-5 shadow-xl"
    >
      <button
        type="button"
        data-testid="drawer-close"
        onClick={onClose}
        className="mb-4 text-[length:var(--text-sm)] text-ink-muted hover:text-ink"
      >
        ← Close
      </button>

      <h2 className="font-mono text-lg font-semibold">{camera.camera_uid}</h2>
      <p className="mb-4 text-xs uppercase tracking-wide text-ink-muted">
        {camera.camera_type} · {camera.status}
      </p>

      <h3 className="mb-2 text-[length:var(--text-xs)] font-medium text-ink-muted">
        Stream endpoints
      </h3>

      {streams === null ? (
        <p className="text-sm text-ink-faint">Loading…</p>
      ) : streams.length === 0 ? (
        <p className="text-sm text-ink-faint">No endpoints registered.</p>
      ) : (
        <ul className="space-y-2" data-testid="stream-list">
          {streams.map((s) => (
            <li key={s.url} className="rounded border p-2 text-xs">
              <div className="flex items-center justify-between gap-2">
                <span className="font-semibold uppercase">
                  {s.protocol}
                  {s.is_primary && (
                    <span className="ml-1 font-normal text-ink-faint">primary</span>
                  )}
                </span>
                <span
                  className={`rounded px-1.5 py-0.5 ${
                    REACHABILITY_STYLES[s.reachability] ?? "bg-sunken text-ink"
                  }`}
                >
                  {s.reachability}
                </span>
              </div>
              <code className="mt-1 block break-all text-ink-muted">{s.url}</code>
              {(s.codec || s.resolution) && (
                <span className="mt-1 block text-ink-faint">
                  {[s.codec, s.resolution].filter(Boolean).join(" · ")}
                </span>
              )}
              {s.requires_auth && (
                <span className="mt-1 block text-[var(--state-maintenance-ink)]">requires credentials</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </aside>
  );
}
