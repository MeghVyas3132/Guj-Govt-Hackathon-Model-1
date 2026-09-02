"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { CameraPlayer } from "@/components/CameraPlayer";
import { Button, Mono, Notice } from "@/components/ui";
import { apiFetch, apiJson } from "@/lib/session";

/** What `enrich` writes back onto the camera. Every field is optional: whatever
 *  the stream did not state stays absent rather than becoming a default. */
type StreamMetadata = {
  codec?: string;
  resolution?: string;
  frame_rate?: number;
  manifest?: {
    encryption?: string;
    playlist_type?: string;
    is_live?: boolean;
    segment_count?: number;
    total_duration_s?: number;
    target_duration?: number;
  };
};

type Camera = {
  id: string;
  camera_uid: string;
  external_camera_id: string;
  name: string | null;
  latitude: number;
  longitude: number;
  address: string | null;
  camera_type: string;
  camera_technology: string;
  current_status: string;
  status_since: string | null;
  connectivity: string;
  ownership_class: string;
  site_type: string;
  azimuth_deg: number | null;
  fov_deg: number | null;
  range_m: number | null;
  resolution: string | null;
  retention_days: number | null;
  source_type: string;
  metadata: Record<string, unknown>;
};

type Stream = {
  id: string;
  protocol: string;
  url: string;
  codec: string | null;
  resolution: string | null;
  reachability: string;
  requires_auth: boolean;
  is_primary: boolean;
};

type Observation = {
  status: string;
  observed_at: string;
  source: string;
  latency_ms: number | null;
};

type AuditEntry = {
  action: string;
  at: string;
  actor_label: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
};

const REACHABILITY_HINT: Record<string, string> = {
  public_cdn: "works on any network",
  direct_ip: "needs gateway ports open",
  lan_only: "reachable only on the local network",
};

export default function CameraDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id;

  const [camera, setCamera] = useState<Camera | null>(null);
  const [streams, setStreams] = useState<Stream[]>([]);
  const [history, setHistory] = useState<Observation[]>([]);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [enriching, setEnriching] = useState(false);
  const [enrichResult, setEnrichResult] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    let active = true;
    Promise.all([
      apiJson<Camera>(`/api/v1/cameras/${id}`),
      apiJson<Stream[]>(`/api/v1/cameras/${id}/streams`),
      apiJson<Observation[]>(`/api/v1/health/cameras/${id}/history?limit=20`).catch(
        () => [],
      ),
      apiJson<AuditEntry[]>(`/api/v1/cameras/${id}/audit`).catch(() => []),
    ])
      .then(([cam, s, h, a]) => {
        if (!active) return;
        setCamera(cam);
        setStreams(s);
        setHistory(h);
        setAudit(a);
      })
      .catch((e) => active && setError(e.message));
    return () => {
      active = false;
    };
  }, [id]);

  if (error) {
    return (
      <main className="mx-auto max-w-[56rem] p-6">
        <Notice tone="error" title="Could not load this camera">
          {error}
        </Notice>
      </main>
    );
  }

  if (!camera) return <main className="p-8 text-sm text-ink-faint">Loading…</main>;

  const geocoded = camera.metadata?.geocode_precision === "district";
  const unmapped = Object.entries(camera.metadata ?? {}).filter(([k]) =>
    k.startsWith("unmapped_"),
  );
  const stream = camera.metadata?.stream as StreamMetadata | undefined;

  return (
    <main className="mx-auto max-w-[56rem] p-6">
      <Link href="/cameras" className="text-[length:var(--text-sm)] text-ink-muted hover:text-ink">
        ← All cameras
      </Link>

      <div className="mt-3 mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="font-mono text-[length:var(--text-xl)] font-semibold text-ink">
            {camera.camera_uid}
          </h1>
          <p className="text-[length:var(--text-sm)] text-ink-muted">
            {camera.name ?? "Unnamed"} · {camera.camera_type} · {camera.current_status}
          </p>
        </div>
        <Button
          busy={enriching}
          disabled={!streams.some((s) => s.protocol === "hls")}
          title={
            streams.some((s) => s.protocol === "hls")
              ? undefined
              : "This camera has no HLS endpoint to read metadata from"
          }
          onClick={async () => {
            setEnriching(true);
            setEnrichResult(null);
            try {
              const response = await apiFetch(`/api/v1/cameras/${id}/enrich`, {
                method: "POST",
              });
              const body = await response.json();
              const result = body.results?.[0];
              setEnrichResult(
                result?.error
                  ? `Could not read the stream: ${result.error}`
                  : body.updated
                    ? "Updated from the stream."
                    : "Already matches the stream.",
              );
              // Re-read rather than patching state by hand: the server decides
              // what actually changed, and guessing here is how the two drift.
              const fresh = await apiFetch(`/api/v1/cameras/${id}`);
              if (fresh.ok) setCamera(await fresh.json());
              const s = await apiFetch(`/api/v1/cameras/${id}/streams`);
              if (s.ok) setStreams(await s.json());
            } catch (cause) {
              setEnrichResult(
                cause instanceof Error ? cause.message : "Enrichment failed",
              );
            } finally {
              setEnriching(false);
            }
          }}
        >
          Read metadata from stream
        </Button>
      </div>

      {enrichResult && (
        <div className="mb-6">
          <Notice
            tone={enrichResult.startsWith("Could not") ? "warn" : "success"}
          >
            {enrichResult}
          </Notice>
        </div>
      )}

      <section className="mb-8">
        <h2 className="mb-2 text-[length:var(--text-lg)] font-semibold text-ink">
          Live preview
        </h2>
        <CameraPlayer
          cameraId={String(id)}
          label={camera.name ?? camera.camera_uid}
          playable={streams.some((s) => s.protocol === "hls")}
        />
      </section>

      {stream && (
        <section className="mb-8">
          <h2 className="mb-2 text-[length:var(--text-lg)] font-semibold text-ink">
            Derived from the stream
          </h2>
          <p className="mb-3 text-[length:var(--text-xs)] text-ink-muted">
            Read from the camera&rsquo;s own manifest and media, not from the source
            catalogue — which for most sources carries an identifier and nothing else.
          </p>
          <dl className="grid gap-x-8 gap-y-2 rounded-[6px] border border-line bg-surface p-4 text-sm sm:grid-cols-2">
            <Row label="Codec" value={stream.codec ?? "—"} mono />
            <Row label="Resolution" value={stream.resolution ?? "—"} mono />
            <Row
              label="Frame rate"
              value={stream.frame_rate ? `${stream.frame_rate} fps` : "—"}
              mono
            />
            <Row
              label="Feed"
              value={
                stream.manifest?.is_live === false
                  ? "recorded loop (not live)"
                  : stream.manifest?.is_live === true
                    ? "live"
                    : "—"
              }
            />
            <Row label="Encryption" value={stream.manifest?.encryption ?? "none"} />
            <Row
              label="Archive"
              value={
                stream.manifest?.total_duration_s
                  ? `${Math.round(stream.manifest.total_duration_s / 3600)} hours in ${stream.manifest.segment_count?.toLocaleString()} segments`
                  : "—"
              }
            />
          </dl>
        </section>
      )}

      {geocoded && (
        <div className="mb-6">
          <Notice tone="warn" title="Position is district-level, not surveyed">
            It was derived from{" "}
            <Mono>{String(camera.metadata.geocode_matched_on)}</Mono> in the source
            data and resolves to a representative point for{" "}
            {String(camera.metadata.geocode_district)} district. Supplying real
            coordinates updates it in place.
          </Notice>
        </div>
      )}

      {unmapped.length > 0 && (
        <div className="mb-6">
          <Notice title="Values this registry did not recognise were kept">
            {unmapped.map(([k, v]) => (
              <span key={k} className="mr-3">
                <Mono>{k.replace("unmapped_", "")}</Mono> = <Mono>{String(v)}</Mono>
              </span>
            ))}
            <span className="ml-1">Add it as a vocabulary term to classify it.</span>
          </Notice>
        </div>
      )}

      <section className="mb-8">
        <h2 className="mb-2 text-[length:var(--text-lg)] font-semibold text-ink">Details</h2>
        <dl className="grid gap-x-8 gap-y-2 rounded-[6px] border border-line bg-surface p-4 text-sm sm:grid-cols-2">
          <Row label="External id" value={camera.external_camera_id} mono />
          <Row label="Source" value={camera.source_type} />
          <Row
            label="Location"
            value={`${camera.latitude.toFixed(5)}, ${camera.longitude.toFixed(5)}`}
            mono
          />
          <Row label="Address" value={camera.address ?? "—"} />
          <Row label="Technology" value={camera.camera_technology} />
          <Row label="Connectivity" value={camera.connectivity} />
          <Row label="Ownership" value={camera.ownership_class} />
          <Row label="Site" value={camera.site_type} />
          <Row
            label="Bearing"
            value={
              camera.azimuth_deg === null
                ? "not recorded — treated as omnidirectional"
                : `${camera.azimuth_deg}° · ${camera.fov_deg ?? "?"}° FOV · ${camera.range_m ?? "?"}m`
            }
          />
          <Row label="Retention" value={camera.retention_days ? `${camera.retention_days} days` : "—"} />
        </dl>
      </section>

      <section className="mb-8">
        <h2 className="mb-2 text-[length:var(--text-lg)] font-semibold text-ink">
          Stream endpoints
        </h2>
        <p className="mb-3 text-[length:var(--text-xs)] text-ink-muted">
          What the analytics layers call. Pick the endpoint whose reachability matches
          your network.
        </p>
        {streams.length === 0 ? (
          <p className="rounded-[6px] border border-line bg-surface p-4 text-sm text-ink-faint">
            No endpoints registered for this camera.
          </p>
        ) : (
          <ul className="space-y-2">
            {streams.map((stream) => (
              <li key={stream.id} className="rounded-[6px] border border-line bg-surface p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-semibold uppercase">
                    {stream.protocol}
                  </span>
                  {stream.is_primary && (
                    <span className="rounded-[3px] bg-[var(--brand)] px-1.5 py-0.5 text-[length:var(--text-2xs)] text-white">
                      primary
                    </span>
                  )}
                  <span className="rounded bg-sunken px-1.5 py-0.5 text-xs">
                    {stream.reachability}
                  </span>
                  <span className="text-[length:var(--text-xs)] text-ink-faint">
                    {REACHABILITY_HINT[stream.reachability]}
                  </span>
                </div>
                <code className="mt-1 block break-all text-xs text-ink-muted">
                  {stream.url}
                </code>
                <p className="mt-1 text-[length:var(--text-xs)] text-ink-faint">
                  {[stream.codec, stream.resolution].filter(Boolean).join(" · ") || "—"}
                  {stream.requires_auth && (
                    <span className="ml-2 text-[var(--state-maintenance-ink)]">requires credentials</span>
                  )}
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="mb-8">
        <h2 className="mb-2 text-[length:var(--text-lg)] font-semibold text-ink">
          Recent health
        </h2>
        {history.length === 0 ? (
          <p className="rounded-[6px] border border-line bg-surface p-4 text-sm text-ink-faint">
            No observations recorded.
          </p>
        ) : (
          <div className="overflow-x-auto rounded-[6px] border border-line bg-surface">
            <table className="w-full text-sm">
              <tbody>
                {history.map((observation, index) => (
                  <tr key={index} className="border-b border-line last:border-0">
                    <td className="px-3 py-1.5">{observation.status}</td>
                    <td className="px-3 py-1.5 text-ink-muted">
                      {new Date(observation.observed_at).toLocaleString()}
                    </td>
                    <td className="px-3 py-1.5 text-[length:var(--text-xs)] text-ink-faint">
                      {observation.source}
                      {observation.latency_ms !== null &&
                        ` · ${observation.latency_ms}ms`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section>
        <h2 className="mb-2 text-[length:var(--text-lg)] font-semibold text-ink">
          Change history
        </h2>
        {audit.length === 0 ? (
          <p className="rounded-[6px] border border-line bg-surface p-4 text-sm text-ink-faint">
            No recorded changes.
          </p>
        ) : (
          <ul className="space-y-2">
            {audit.map((entry, index) => (
              <li key={index} className="rounded-[6px] border border-line bg-surface p-3 text-sm">
                <div className="flex flex-wrap items-baseline gap-2">
                  <span className="font-medium">{entry.action}</span>
                  <span className="text-[length:var(--text-xs)] text-ink-muted">
                    {new Date(entry.at).toLocaleString()}
                  </span>
                  <span className="text-[length:var(--text-xs)] text-ink-faint">
                    {entry.actor_label ?? "system"}
                  </span>
                </div>
                {entry.before && entry.after && (
                  <Diff before={entry.before} after={entry.after} />
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}

function Row({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex justify-between gap-4 border-b border-line pb-1 last:border-0">
      <dt className="text-xs uppercase text-ink-faint">{label}</dt>
      <dd className={mono ? "font-mono text-xs" : "text-ink"}>{value}</dd>
    </div>
  );
}

function Diff({
  before,
  after,
}: {
  before: Record<string, unknown>;
  after: Record<string, unknown>;
}) {
  const changed = Object.keys(after).filter(
    (key) => JSON.stringify(before[key]) !== JSON.stringify(after[key]),
  );
  if (changed.length === 0) return null;
  return (
    <ul className="mt-2 space-y-0.5 text-xs">
      {changed.map((key) => (
        <li key={key}>
          <span className="font-mono text-ink-muted">{key}</span>{" "}
          <span className="text-[var(--state-offline-ink)] line-through">{String(before[key])}</span>{" "}
          <span className="text-[var(--state-online-ink)]">{String(after[key])}</span>
        </li>
      ))}
    </ul>
  );
}
