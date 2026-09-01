"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { apiJson } from "@/lib/session";

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
      <main className="mx-auto max-w-4xl p-8">
        <p className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {error}
        </p>
      </main>
    );
  }

  if (!camera) return <main className="p-8 text-sm text-slate-400">Loading…</main>;

  const geocoded = camera.metadata?.geocode_precision === "district";
  const unmapped = Object.entries(camera.metadata ?? {}).filter(([k]) =>
    k.startsWith("unmapped_"),
  );

  return (
    <main className="mx-auto max-w-4xl p-8">
      <Link href="/cameras" className="text-sm text-slate-500 hover:text-slate-900">
        ← All cameras
      </Link>

      <h1 className="mt-3 font-mono text-2xl font-semibold">{camera.camera_uid}</h1>
      <p className="mb-6 text-sm text-slate-500">
        {camera.name ?? "Unnamed"} · {camera.camera_type} · {camera.current_status}
      </p>

      {geocoded && (
        <p className="mb-6 rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
          <strong>Position is district-level, not surveyed.</strong> It was derived
          from{" "}
          <span className="font-mono">
            {String(camera.metadata.geocode_matched_on)}
          </span>{" "}
          in the source data and resolves to a representative point for{" "}
          {String(camera.metadata.geocode_district)} district. Supplying real
          coordinates updates it in place.
        </p>
      )}

      {unmapped.length > 0 && (
        <p className="mb-6 rounded border border-blue-200 bg-blue-50 p-3 text-sm text-blue-800">
          <strong>Values this registry did not recognise were kept.</strong>{" "}
          {unmapped.map(([k, v]) => (
            <span key={k} className="mr-3">
              <span className="font-mono text-xs">{k.replace("unmapped_", "")}</span> ={" "}
              <span className="font-mono text-xs">{String(v)}</span>
            </span>
          ))}
          Add it as a vocabulary term to classify it.
        </p>
      )}

      <section className="mb-8">
        <h2 className="mb-2 text-sm font-semibold uppercase text-slate-500">Details</h2>
        <dl className="grid gap-x-8 gap-y-2 rounded border p-4 text-sm sm:grid-cols-2">
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
        <h2 className="mb-2 text-sm font-semibold uppercase text-slate-500">
          Stream endpoints
        </h2>
        <p className="mb-3 text-xs text-slate-500">
          What the analytics layers call. Pick the endpoint whose reachability matches
          your network.
        </p>
        {streams.length === 0 ? (
          <p className="rounded border p-4 text-sm text-slate-400">
            No endpoints registered for this camera.
          </p>
        ) : (
          <ul className="space-y-2">
            {streams.map((stream) => (
              <li key={stream.id} className="rounded border p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-semibold uppercase">
                    {stream.protocol}
                  </span>
                  {stream.is_primary && (
                    <span className="rounded bg-slate-900 px-1.5 py-0.5 text-xs text-white">
                      primary
                    </span>
                  )}
                  <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs">
                    {stream.reachability}
                  </span>
                  <span className="text-xs text-slate-400">
                    {REACHABILITY_HINT[stream.reachability]}
                  </span>
                </div>
                <code className="mt-1 block break-all text-xs text-slate-600">
                  {stream.url}
                </code>
                <p className="mt-1 text-xs text-slate-400">
                  {[stream.codec, stream.resolution].filter(Boolean).join(" · ") || "—"}
                  {stream.requires_auth && (
                    <span className="ml-2 text-amber-600">requires credentials</span>
                  )}
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="mb-8">
        <h2 className="mb-2 text-sm font-semibold uppercase text-slate-500">
          Recent health
        </h2>
        {history.length === 0 ? (
          <p className="rounded border p-4 text-sm text-slate-400">
            No observations recorded.
          </p>
        ) : (
          <div className="overflow-x-auto rounded border">
            <table className="w-full text-sm">
              <tbody>
                {history.map((observation, index) => (
                  <tr key={index} className="border-b last:border-0">
                    <td className="px-3 py-1.5">{observation.status}</td>
                    <td className="px-3 py-1.5 text-slate-500">
                      {new Date(observation.observed_at).toLocaleString()}
                    </td>
                    <td className="px-3 py-1.5 text-xs text-slate-400">
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
        <h2 className="mb-2 text-sm font-semibold uppercase text-slate-500">
          Change history
        </h2>
        {audit.length === 0 ? (
          <p className="rounded border p-4 text-sm text-slate-400">
            No recorded changes.
          </p>
        ) : (
          <ul className="space-y-2">
            {audit.map((entry, index) => (
              <li key={index} className="rounded border p-3 text-sm">
                <div className="flex flex-wrap items-baseline gap-2">
                  <span className="font-medium">{entry.action}</span>
                  <span className="text-xs text-slate-500">
                    {new Date(entry.at).toLocaleString()}
                  </span>
                  <span className="text-xs text-slate-400">
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
    <div className="flex justify-between gap-4 border-b border-slate-100 pb-1 last:border-0">
      <dt className="text-xs uppercase text-slate-400">{label}</dt>
      <dd className={mono ? "font-mono text-xs" : "text-slate-700"}>{value}</dd>
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
          <span className="font-mono text-slate-500">{key}</span>{" "}
          <span className="text-red-600 line-through">{String(before[key])}</span>{" "}
          <span className="text-green-700">{String(after[key])}</span>
        </li>
      ))}
    </ul>
  );
}
