"use client";

import { type Filters, isEmpty } from "@/lib/filters";

// Mirrors CameraStatus and CameraType in app/core/enums.py. A value that is not a
// member there is rejected by FastAPI with a 422 before it reaches any query builder,
// so these two lists have to stay in step with the enums.
const STATUSES = ["online", "offline", "unknown", "maintenance"];
const TYPES = ["fixed", "ptz", "dome", "bullet", "anpr", "thermal"];

function toggle(list: string[], value: string): string[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}

function Chips({
  values,
  selected,
  onToggle,
  group,
}: {
  values: string[];
  selected: string[];
  onToggle: (value: string) => void;
  group: string;
}) {
  return (
    <div className="flex flex-wrap gap-1">
      {values.map((value) => {
        const active = selected.includes(value);
        return (
          <button
            key={value}
            type="button"
            aria-pressed={active}
            data-testid={`filter-${group}-${value}`}
            onClick={() => onToggle(value)}
            className={`rounded px-2 py-1 text-xs transition-colors ${
              active
                ? "bg-slate-800 text-white"
                : "bg-sunken text-ink hover:bg-slate-200"
            }`}
          >
            {value}
          </button>
        );
      })}
    </div>
  );
}

export function FilterPanel({
  filters,
  onChange,
  matchCount,
}: {
  filters: Filters;
  onChange: (next: Filters) => void;
  matchCount: number | null;
}) {
  return (
    <div
      data-testid="filter-panel"
      className="absolute left-4 top-4 z-10 w-72 rounded-lg bg-white/95 p-4 shadow-lg backdrop-blur"
    >
      <div className="mb-3 flex items-baseline justify-between">
        <span data-testid="match-count" className="text-sm font-semibold text-ink">
          {matchCount === null ? "…" : `${matchCount} cameras`}
        </span>
        {!isEmpty(filters) && (
          <button
            type="button"
            data-testid="clear-filters"
            onClick={() => onChange({ statuses: [], cameraTypes: [], departmentIds: [], q: "" })}
            className="text-[length:var(--text-xs)] text-ink-muted underline hover:text-ink"
          >
            clear
          </button>
        )}
      </div>

      <input
        data-testid="filter-q"
        className="mb-3 w-full rounded border px-2 py-1 text-sm"
        placeholder="Search uid, name, address…"
        value={filters.q}
        onChange={(e) => onChange({ ...filters, q: e.target.value })}
      />

      <p className="mb-1 text-[length:var(--text-xs)] font-medium text-ink-muted">Status</p>
      <div className="mb-3">
        <Chips
          group="status"
          values={STATUSES}
          selected={filters.statuses}
          onToggle={(value) =>
            onChange({ ...filters, statuses: toggle(filters.statuses, value) })
          }
        />
      </div>

      <p className="mb-1 text-[length:var(--text-xs)] font-medium text-ink-muted">Camera type</p>
      <Chips
        group="type"
        values={TYPES}
        selected={filters.cameraTypes}
        onToggle={(value) =>
          onChange({ ...filters, cameraTypes: toggle(filters.cameraTypes, value) })
        }
      />
    </div>
  );
}
