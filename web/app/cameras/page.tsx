"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { API, apiJson, useCurrentUser } from "@/lib/session";

type Camera = {
  id: string;
  camera_uid: string;
  external_camera_id: string;
  name: string | null;
  camera_type: string;
  current_status: string;
  site_type: string;
};

type Page = { items: Camera[]; total: number };

const STATUS_STYLE: Record<string, string> = {
  online: "bg-green-100 text-green-800",
  offline: "bg-red-100 text-red-800",
  maintenance: "bg-amber-100 text-amber-800",
  unknown: "bg-slate-100 text-slate-700",
};

const PAGE_SIZE = 50;

export default function CamerasPage() {
  const user = useCurrentUser();
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<Page | null>(null);
  const [error, setError] = useState<string | null>(null);

  const params = useCallback(() => {
    const search = new URLSearchParams({
      limit: String(PAGE_SIZE),
      offset: String(offset),
    });
    if (query) search.set("q", query);
    if (status) search.append("statuses", status);
    return search.toString();
  }, [query, status, offset]);

  useEffect(() => {
    let active = true;
    // Debounced: typing in the search box should not fire a request per keystroke
    // against a table with 80,000 rows.
    const timer = setTimeout(() => {
      apiJson<Page>(`/api/v1/cameras?${params()}`)
        .then((data) => active && setPage(data))
        .catch((e) => active && setError(e.message));
    }, 200);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [params]);

  const canExport = user?.scopes.includes("cameras:export") ?? false;
  const canWrite = user?.scopes.includes("cameras:write") ?? false;
  const total = page?.total ?? 0;

  return (
    <main className="mx-auto max-w-6xl p-8">
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Cameras</h1>
          <p className="text-sm text-slate-500">
            {total.toLocaleString()} matching the current filter
          </p>
        </div>
        <div className="flex gap-2">
          {canExport && (
            <a
              href={`${API}/api/v1/cameras/export.csv?${params()}`}
              className="rounded border px-3 py-2 text-sm hover:bg-slate-50"
            >
              Export CSV
            </a>
          )}
          {canWrite && (
            <Link
              href="/cameras/new"
              className="rounded bg-slate-900 px-3 py-2 text-sm font-medium text-white"
            >
              Add camera
            </Link>
          )}
        </div>
      </div>

      <div className="mb-4 flex flex-wrap gap-3">
        <input
          className="w-72 rounded border px-3 py-2 text-sm"
          placeholder="Search uid, name, address, external id…"
          value={query}
          onChange={(e) => {
            setOffset(0);
            setQuery(e.target.value);
          }}
        />
        <select
          className="rounded border px-2 py-2 text-sm"
          value={status}
          onChange={(e) => {
            setOffset(0);
            setStatus(e.target.value);
          }}
        >
          <option value="">Any status</option>
          <option value="online">online</option>
          <option value="offline">offline</option>
          <option value="maintenance">maintenance</option>
          <option value="unknown">unknown</option>
        </select>
      </div>

      {error && (
        <p className="mb-4 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {error}
        </p>
      )}

      <div className="overflow-x-auto rounded border">
        <table className="w-full text-sm">
          <thead className="border-b bg-slate-50 text-left text-xs uppercase text-slate-500">
            <tr>
              <th className="px-3 py-2">Camera</th>
              <th className="px-3 py-2">External id</th>
              <th className="px-3 py-2">Name</th>
              <th className="px-3 py-2">Type</th>
              <th className="px-3 py-2">Site</th>
              <th className="px-3 py-2">Status</th>
            </tr>
          </thead>
          <tbody>
            {(page?.items ?? []).map((camera) => (
              <tr key={camera.id} className="border-b last:border-0 hover:bg-slate-50">
                <td className="px-3 py-2 font-mono text-xs">
                  <Link
                    href={`/cameras/${camera.id}`}
                    className="underline-offset-2 hover:underline"
                  >
                    {camera.camera_uid}
                  </Link>
                </td>
                <td className="px-3 py-2 font-mono text-xs text-slate-500">
                  {camera.external_camera_id}
                </td>
                <td className="px-3 py-2">{camera.name ?? "—"}</td>
                <td className="px-3 py-2 text-slate-600">{camera.camera_type}</td>
                <td className="px-3 py-2 text-slate-600">{camera.site_type}</td>
                <td className="px-3 py-2">
                  <span
                    className={`rounded px-2 py-0.5 text-xs ${
                      STATUS_STYLE[camera.current_status] ?? STATUS_STYLE.unknown
                    }`}
                  >
                    {camera.current_status}
                  </span>
                </td>
              </tr>
            ))}
            {page !== null && page.items.length === 0 && (
              <tr>
                <td colSpan={6} className="px-3 py-8 text-center text-slate-400">
                  No cameras match this filter.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="mt-4 flex items-center gap-3 text-sm">
        <button
          className="rounded border px-3 py-1.5 disabled:opacity-40"
          disabled={offset === 0}
          onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
        >
          Previous
        </button>
        <span className="text-slate-500">
          {total === 0 ? 0 : offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of{" "}
          {total.toLocaleString()}
        </span>
        <button
          className="rounded border px-3 py-1.5 disabled:opacity-40"
          disabled={offset + PAGE_SIZE >= total}
          onClick={() => setOffset(offset + PAGE_SIZE)}
        >
          Next
        </button>
      </div>
    </main>
  );
}
