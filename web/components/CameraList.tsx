"use client";

/**
 * The list that drives the map.
 *
 * A map of clustered dots is a picture, not a way to find anything: an operator
 * looking for one camera has to guess which cluster it is in and zoom until it
 * splits. The list is the index — click a row and the map flies to it.
 *
 * Paged rather than complete. The registry is designed for 80,000 cameras and
 * this panel must not try to render them, so it loads a page at a time and says
 * how many more there are.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { apiFetch } from "@/lib/session";

const PAGE = 50;

export type CameraListItem = {
  id: string;
  camera_uid: string;
  name: string | null;
  camera_type: string;
  current_status: string;
  latitude: number;
  longitude: number;
  department_code: string | null;
};

const STATUS_DOT: Record<string, string> = {
  online: "var(--state-online-ink)",
  offline: "var(--state-offline-ink)",
  maintenance: "var(--state-maintenance-ink)",
};

export function CameraList({
  query,
  selectedId,
  onSelect,
}: {
  /** The already-debounced filter query string, shared with the tiles. */
  query: string;
  selectedId: string | null;
  onSelect: (camera: CameraListItem) => void;
}) {
  const [items, setItems] = useState<CameraListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const listRef = useRef<HTMLUListElement>(null);

  const load = useCallback(
    async (offset: number) => {
      setLoading(true);
      setError(null);
      try {
        const suffix = query ? `&${query}` : "";
        const response = await apiFetch(
          `/api/v1/cameras?limit=${PAGE}&offset=${offset}${suffix}`,
        );
        if (!response.ok) throw new Error(`Could not list cameras (${response.status})`);
        const page = await response.json();
        setTotal(page.total ?? 0);
        setItems((prev) =>
          offset === 0 ? page.items : [...prev, ...(page.items ?? [])],
        );
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "Failed to load");
      } finally {
        setLoading(false);
      }
    },
    [query],
  );

  // Reset to the first page whenever the filter changes, and scroll back to the
  // top -- keeping the old scroll position over a different result set puts the
  // operator in the middle of a list they have not seen.
  useEffect(() => {
    setItems([]);
    void load(0);
    listRef.current?.scrollTo({ top: 0 });
  }, [load]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {error && (
        <p className="border-b border-line px-3 py-2 text-[length:var(--text-xs)] text-[var(--state-offline-ink)]">
          {error}
        </p>
      )}

      <ul ref={listRef} data-testid="camera-list" className="min-h-0 flex-1 overflow-y-auto">
        {items.map((camera) => {
          const active = camera.id === selectedId;
          return (
            <li key={camera.id}>
              <button
                type="button"
                onClick={() => onSelect(camera)}
                aria-current={active ? "true" : undefined}
                className={`flex w-full items-start gap-2 border-b border-line px-3 py-2 text-left
                  transition-colors duration-[var(--duration)] ${
                    active
                      ? "bg-[var(--brand-tint)]"
                      : "hover:bg-sunken"
                  }`}
              >
                <span
                  aria-hidden
                  className="mt-1.5 size-2 shrink-0 rounded-full"
                  style={{
                    background: STATUS_DOT[camera.current_status] ?? "var(--ink-faint)",
                  }}
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[length:var(--text-sm)] text-ink">
                    {camera.name ?? "Unnamed"}
                  </span>
                  <span className="mt-0.5 flex items-center gap-1.5 text-[length:var(--text-2xs)] text-ink-faint">
                    <span className="font-mono">{camera.camera_uid}</span>
                    <span>·</span>
                    <span>{camera.camera_type}</span>
                    {camera.department_code && (
                      <>
                        <span>·</span>
                        <span>{camera.department_code}</span>
                      </>
                    )}
                  </span>
                </span>
              </button>
            </li>
          );
        })}

        {!loading && items.length === 0 && (
          <li className="px-3 py-8 text-center text-[length:var(--text-xs)] text-ink-faint">
            No cameras match this filter.
          </li>
        )}

        {loading && (
          <li className="px-3 py-3 text-[length:var(--text-xs)] text-ink-faint">
            Loading…
          </li>
        )}

        {!loading && items.length < total && (
          <li className="p-2">
            <button
              type="button"
              onClick={() => void load(items.length)}
              className="w-full rounded-[4px] border border-line-strong bg-surface py-1.5
                text-[length:var(--text-xs)] text-ink-muted transition-colors
                duration-[var(--duration)] hover:bg-sunken hover:text-ink"
            >
              Load {Math.min(PAGE, total - items.length)} more
              <span className="ml-1 text-ink-faint">
                ({(total - items.length).toLocaleString()} left)
              </span>
            </button>
          </li>
        )}
      </ul>
    </div>
  );
}
