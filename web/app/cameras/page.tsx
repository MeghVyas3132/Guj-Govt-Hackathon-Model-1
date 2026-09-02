"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import {
  EmptyState,
  Input,
  LinkButton,
  Mono,
  Notice,
  PageHeader,
  Select,
  SkeletonRows,
  StatusBadge,
  TD,
  TH,
  THead,
  TR,
  Table,
  Toolbar,
} from "@/components/ui";
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
    // Debounced: a keystroke per request against eighty thousand rows is rude.
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
  const filtered = Boolean(query || status);

  return (
    <div className="mx-auto max-w-[76rem] p-6">
      <PageHeader
        title="Cameras"
        description={
          page
            ? `${total.toLocaleString()} camera${total === 1 ? "" : "s"}${filtered ? " matching this filter" : " on the registry"}`
            : "Loading the registry"
        }
        actions={
          <>
            {canExport && (
              <LinkButton href={`${API}/api/v1/cameras/export.csv?${params()}`}>
                Export CSV
              </LinkButton>
            )}
            {canWrite && (
              <LinkButton href="/cameras/new" variant="primary">
                Add camera
              </LinkButton>
            )}
          </>
        }
      />

      <Toolbar>
        <label className="block">
          <span className="mb-1 block text-[length:var(--text-xs)] font-medium text-ink-muted">
            Search
          </span>
          <Input
            className="w-72"
            placeholder="uid, name, address, external id"
            value={query}
            onChange={(e) => {
              setOffset(0);
              setQuery(e.target.value);
            }}
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-[length:var(--text-xs)] font-medium text-ink-muted">
            Status
          </span>
          <Select
            className="w-40"
            value={status}
            onChange={(e) => {
              setOffset(0);
              setStatus(e.target.value);
            }}
          >
            <option value="">Any</option>
            <option value="online">online</option>
            <option value="offline">offline</option>
            <option value="maintenance">maintenance</option>
            <option value="unknown">unknown</option>
          </Select>
        </label>
      </Toolbar>

      {error && (
        <div className="mb-4">
          <Notice tone="error">{error}</Notice>
        </div>
      )}

      <Table>
        <THead>
          <tr>
            <TH>Camera</TH>
            <TH>External id</TH>
            <TH>Name</TH>
            <TH>Type</TH>
            <TH>Site</TH>
            <TH>Status</TH>
          </tr>
        </THead>
        <tbody>
          {page === null && <SkeletonRows rows={8} cols={6} />}

          {page?.items.map((camera) => (
            <TR key={camera.id}>
              <TD>
                <Link
                  href={`/cameras/${camera.id}`}
                  className="whitespace-nowrap font-mono text-[length:var(--text-xs)] text-[var(--brand)] underline-offset-2 hover:underline"
                >
                  {camera.camera_uid}
                </Link>
              </TD>
              <TD>
                <Mono className="text-ink-muted">{camera.external_camera_id}</Mono>
              </TD>
              <TD>{camera.name ?? <span className="text-ink-faint">—</span>}</TD>
              <TD className="text-ink-muted">{camera.camera_type}</TD>
              <TD className="text-ink-muted">{camera.site_type}</TD>
              <TD>
                <StatusBadge status={camera.current_status} />
              </TD>
            </TR>
          ))}

          {page?.items.length === 0 && (
            <tr>
              <td colSpan={6}>
                <EmptyState title="No cameras match this filter">
                  {filtered
                    ? "Try a broader search, or clear the status filter."
                    : "Once a department is onboarded its cameras appear here."}
                </EmptyState>
              </td>
            </tr>
          )}
        </tbody>
      </Table>

      {total > PAGE_SIZE && (
        <div className="mt-3 flex items-center gap-3 text-[length:var(--text-sm)]">
          <button
            className="rounded-[4px] border border-line-strong bg-surface px-3 py-1.5 text-[length:var(--text-sm)] transition-colors duration-[var(--duration)] hover:bg-sunken disabled:opacity-40"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
          >
            Previous
          </button>
          <span className="tabular text-ink-muted">
            {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of{" "}
            {total.toLocaleString()}
          </span>
          <button
            className="rounded-[4px] border border-line-strong bg-surface px-3 py-1.5 text-[length:var(--text-sm)] transition-colors duration-[var(--duration)] hover:bg-sunken disabled:opacity-40"
            disabled={offset + PAGE_SIZE >= total}
            onClick={() => setOffset(offset + PAGE_SIZE)}
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}
