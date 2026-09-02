"use client";

// Named imports, not the `import maplibregl from "maplibre-gl"` default the docs
// still show: maplibre-gl's type definitions declare named exports only, so the
// default form fails typecheck with TS1192.
import {
  type ExpressionSpecification,
  Map as MapLibreMap,
  NavigationControl,
  type VectorTileSource,
} from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useMemo, useRef, useState } from "react";

import { CameraDrawer, type SelectedCamera } from "@/components/CameraDrawer";
import { FilterPanel } from "@/components/FilterPanel";
import { EMPTY_FILTERS, type Filters, toQueryString } from "@/lib/filters";

import { API, apiFetch, getToken } from "@/lib/session";

// Matches CameraStatus in app/core/enums.py. The trailing bare colour is the
// fallback `match` requires, and is what `unknown` renders as.
const STATUS_COLOURS: ExpressionSpecification = [
  "match",
  ["get", "status"],
  "online",
  "#16a34a",
  "offline",
  "#dc2626",
  "maintenance",
  "#d97706",
  "#64748b",
];

function tileUrl(query: string): string {
  return `${API}/api/v1/tiles/cameras/{z}/{x}/{y}.mvt${query ? `?${query}` : ""}`;
}

function coverageTileUrl(runId: string): string {
  return `${API}/api/v1/coverage/runs/${runId}/tiles/{z}/{x}/{y}.mvt`;
}

/**
 * Coverage shading. Driven by `installed_fraction`, so the ramp reads as "how
 * much of this cell can any camera see" rather than as a category.
 *
 * Deliberately not a red-to-green ramp: red/green is the one pairing that
 * disappears for the commonest form of colour blindness, and this layer sits
 * under status dots that are already red and green. A single-hue ramp keeps the
 * two readings independent.
 */
const COVERAGE_FILL: ExpressionSpecification = [
  "interpolate",
  ["linear"],
  ["get", "installed_fraction"],
  0,
  "#f8d5c8",
  0.25,
  "#e8a98d",
  0.5,
  "#c97a52",
  0.75,
  "#9c4f28",
  1,
  "#6b2d0f",
];

export function CameraMap() {
  const container = useRef<HTMLDivElement>(null);
  // The map lives in a ref, not state: it is a mutable imperative object and
  // re-rendering must never rebuild it. `styleReady` is the state flag the effects
  // below wait on, because getSource() returns undefined until the style has loaded.
  const map = useRef<MapLibreMap | null>(null);
  const [styleReady, setStyleReady] = useState(false);
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [selected, setSelected] = useState<SelectedCamera | null>(null);
  const [matchCount, setMatchCount] = useState<number | null>(null);
  const [coverageRun, setCoverageRun] = useState<CoverageRunOption | null>(null);
  const [runs, setRuns] = useState<CoverageRunOption[]>([]);

  const query = useMemo(() => toQueryString(filters), [filters]);

  // Debounced, because `q` changes on every keystroke and each change re-requests
  // every visible tile. The chips feel instant at 250ms; the search box stops
  // firing a round of tile requests per character.
  const [appliedQuery, setAppliedQuery] = useState("");
  useEffect(() => {
    const timer = setTimeout(() => setAppliedQuery(query), 250);
    return () => clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    if (!container.current || map.current) return;

    // MapLibre fetches tiles itself, so apiFetch never sees those requests. This is
    // the only hook it offers for attaching a header to them; without it every tile
    // returns 401 and the map renders an empty basemap with no explanation.
    const transformRequest = (
      url: string,
    ): { url: string; headers?: Record<string, string> } => {
      if (!url.startsWith(API)) return { url };
      const token = getToken();
      return token ? { url, headers: { Authorization: `Bearer ${token}` } } : { url };
    };

    const instance = new MapLibreMap({
      container: container.current,
      transformRequest,
      style: {
        version: 8,
        // A symbol layer draws nothing without a glyph source, so without this the
        // cluster counts would silently never appear. fonts.openmaptiles.org serves a
        // PBF variant this MapLibre cannot parse ("Unimplemented type: 4").
        glyphs: "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf",
        sources: {
          osm: {
            type: "raster",
            tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
            tileSize: 256,
            attribution: "© OpenStreetMap contributors",
          },
        },
        layers: [{ id: "osm", type: "raster", source: "osm" }],
      },
      // A starting view, immediately replaced by fitBounds once the extent of
      // the actual data is known. Hardcoding Ahmedabad at z12 meant a registry
      // whose cameras are elsewhere opened onto an empty map, which reads as a
      // broken tile layer rather than as the wrong viewport.
      center: [72.5714, 23.0225],
      zoom: 6,
    });
    map.current = instance;

    instance.addControl(new NavigationControl(), "top-right");

    instance.on("load", () => {
      instance.addSource("cameras", {
        type: "vector",
        tiles: [tileUrl("")],
        minzoom: 0,
        maxzoom: 22,
      });

      // Added empty and pointed at a run later. Declaring it up front fixes the
      // layer order: coverage must sit under every camera layer, and layers can
      // only be inserted relative to ones that already exist.
      instance.addSource("coverage", {
        type: "vector",
        tiles: [`${API}/api/v1/coverage/runs/none/tiles/{z}/{x}/{y}.mvt`],
        minzoom: 0,
        maxzoom: 22,
      });

      instance.addLayer({
        id: "coverage-fill",
        type: "fill",
        source: "coverage",
        "source-layer": "coverage",
        layout: { visibility: "none" },
        paint: { "fill-color": COVERAGE_FILL, "fill-opacity": 0.55 },
      });

      instance.addLayer({
        id: "coverage-outline",
        type: "line",
        source: "coverage",
        "source-layer": "coverage",
        layout: { visibility: "none" },
        // Hairline, and only once the cells are big enough on screen for an
        // outline to describe a shape rather than to fill it in.
        paint: {
          "line-color": "#6b2d0f",
          "line-width": 0.5,
          "line-opacity": ["interpolate", ["linear"], ["zoom"], 9, 0, 11, 0.35],
        },
      });

      // app/services/tiles.py switches the MVT layer name by zoom: `camera_clusters`
      // below z11, `cameras` at z11 and above. Both layers read the one source, and
      // whichever layer the tile actually carries is the one that draws.
      instance.addLayer({
        id: "camera-clusters",
        type: "circle",
        source: "cameras",
        "source-layer": "camera_clusters",
        paint: {
          "circle-radius": [
            "interpolate",
            ["linear"],
            ["get", "camera_count"],
            1,
            8,
            100,
            18,
            5000,
            34,
          ],
          "circle-color": "#0f2d5e",
          "circle-opacity": 0.85,
          "circle-stroke-width": 2,
          "circle-stroke-color": "#ffffff",
        },
      });

      instance.addLayer({
        id: "cluster-count",
        type: "symbol",
        source: "cameras",
        "source-layer": "camera_clusters",
        layout: {
          // camera_count arrives as a number; text-field rejects one uncoerced.
          "text-field": ["to-string", ["get", "camera_count"]],
          "text-font": ["Noto Sans Regular"],
          "text-size": 12,
        },
        paint: { "text-color": "#ffffff" },
      });

      instance.addLayer({
        id: "camera-points",
        type: "circle",
        source: "cameras",
        "source-layer": "cameras",
        paint: {
          "circle-radius": 6,
          "circle-color": STATUS_COLOURS,
          "circle-stroke-width": 1.5,
          "circle-stroke-color": "#ffffff",
        },
      });

      // Opens the drawer rather than a popup: the popup could only show what the
      // tile already carries, and the useful answer -- how do I actually open this
      // stream -- needs a fetch.
      instance.on("click", "camera-points", (event) => {
        const feature = event.features?.[0];
        if (!feature) return;
        const { id, camera_uid, status, camera_type } = feature.properties as Record<
          string,
          string
        >;
        setSelected({ id, camera_uid, status, camera_type });
      });

      instance.on("mouseenter", "camera-points", () => {
        instance.getCanvas().style.cursor = "pointer";
      });
      instance.on("mouseleave", "camera-points", () => {
        instance.getCanvas().style.cursor = "";
      });

      setStyleReady(true);
    });

    return () => {
      instance.remove();
      map.current = null;
      setStyleReady(false);
    };
  }, []);

  // Rebuild the source's tile URL when the filter changes. setTiles() drops the
  // source's cached tiles and re-requests them, so the markers on screen always
  // came from the current filter rather than a stale response.
  useEffect(() => {
    if (!styleReady) return;
    const source = map.current?.getSource("cameras") as VectorTileSource | undefined;
    source?.setTiles([tileUrl(appliedQuery)]);
  }, [appliedQuery, styleReady]);

  // Frame the map on whatever the current filter actually matches. Runs on the
  // filter, not just on mount, so narrowing to one district moves the view to
  // that district instead of leaving the operator to hunt for it.
  useEffect(() => {
    if (!styleReady) return;
    let cancelled = false;
    const suffix = appliedQuery ? `?${appliedQuery}` : "";

    apiFetch(`/api/v1/cameras/bounds${suffix}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((b) => {
        if (cancelled || !b || !b.count || b.west === null) return;
        // A single point, or several cameras sharing one district-level
        // position, gives a zero-area box. fitBounds on that zooms to maximum,
        // so it is padded into something a person can actually read.
        const pad = 0.05;
        const flat = b.east - b.west < 1e-6 && b.north - b.south < 1e-6;
        map.current?.fitBounds(
          [
            [b.west - (flat ? pad : 0), b.south - (flat ? pad : 0)],
            [b.east + (flat ? pad : 0), b.north + (flat ? pad : 0)],
          ],
          { padding: 60, maxZoom: 13, duration: 600 },
        );
      })
      .catch(() => {});

    return () => {
      cancelled = true;
    };
  }, [appliedQuery, styleReady]);

  // The runs a coverage overlay can be drawn from. Fetched once: a run is
  // immutable, and the list only grows when someone visits the coverage page.
  useEffect(() => {
    apiFetch("/api/v1/coverage/runs?limit=20")
      .then((r) => (r.ok ? r.json() : []))
      .then((rows: CoverageRunOption[]) => setRuns(Array.isArray(rows) ? rows : []))
      .catch(() => setRuns([]));
  }, []);

  // Point the coverage source at the selected run, and show or hide the layers.
  useEffect(() => {
    if (!styleReady) return;
    const instance = map.current;
    if (!instance) return;

    const visibility = coverageRun ? "visible" : "none";
    for (const id of ["coverage-fill", "coverage-outline"]) {
      if (instance.getLayer(id)) {
        instance.setLayoutProperty(id, "visibility", visibility);
      }
    }
    if (coverageRun) {
      const source = instance.getSource("coverage") as VectorTileSource | undefined;
      source?.setTiles([coverageTileUrl(coverageRun.id)]);
    }
  }, [coverageRun, styleReady]);

  // The same query string against the list endpoint. It is the shared CameraFilter on
  // the server, so this count and the markers cannot disagree about what matches.
  useEffect(() => {
    let cancelled = false;
    const suffix = appliedQuery ? `&${appliedQuery}` : "";
    apiFetch(`/api/v1/cameras?limit=1${suffix}`)
      .then((r) => r.json())
      .then((page) => {
        if (!cancelled) setMatchCount(page.total ?? 0);
      })
      .catch(() => {
        if (!cancelled) setMatchCount(null);
      });
    return () => {
      cancelled = true;
    };
  }, [appliedQuery]);

  return (
    // h-full, not flex-1: <main> supplies a definite height, and flex-1 outside a
    // flex container collapses to zero -- which renders as a black rectangle with
    // the absolutely-positioned filter panel still visible over it. Not h-screen
    // either: a full viewport height inside the column body would push the page
    // into a scrollbar exactly as tall as the nav.
    <div className="relative h-full w-full">
      <div ref={container} data-testid="camera-map" className="h-full w-full" />
      <FilterPanel filters={filters} onChange={setFilters} matchCount={matchCount} />
      <CoverageOverlayControl
        runs={runs}
        selected={coverageRun}
        onSelect={setCoverageRun}
      />
      <CameraDrawer camera={selected} onClose={() => setSelected(null)} />
    </div>
  );
}

type CoverageRunOption = {
  id: string;
  hex_edge_m: number;
  installed_coverage_pct: number;
  effective_coverage_pct: number;
  created_at?: string;
};

/**
 * Bottom-left so it never collides with the filter panel or the navigation
 * controls, and collapsed to a single line until a run is chosen — an overlay
 * nobody has asked for should not occupy the map.
 */
function CoverageOverlayControl({
  runs,
  selected,
  onSelect,
}: {
  runs: CoverageRunOption[];
  selected: CoverageRunOption | null;
  onSelect: (run: CoverageRunOption | null) => void;
}) {
  if (runs.length === 0) return null;

  return (
    <div className="absolute bottom-4 left-4 z-[var(--z-sticky)] w-[17rem] rounded-[6px] border border-line bg-surface/95 p-3 shadow-[0_2px_12px_rgba(0,0,0,0.10)] backdrop-blur-sm">
      <label className="mb-1.5 block text-[length:var(--text-2xs)] font-semibold uppercase tracking-[0.04em] text-ink-faint">
        Coverage overlay
      </label>
      <select
        value={selected?.id ?? ""}
        onChange={(e) =>
          onSelect(runs.find((r) => r.id === e.target.value) ?? null)
        }
        className="h-8 w-full rounded-[4px] border border-line-strong bg-surface px-2 text-[length:var(--text-sm)] text-ink"
      >
        <option value="">Off</option>
        {runs.map((run) => (
          <option key={run.id} value={run.id}>
            {run.installed_coverage_pct.toFixed(1)}% · {run.hex_edge_m}m cells
            {run.created_at ? ` · ${run.created_at.slice(0, 10)}` : ""}
          </option>
        ))}
      </select>

      {selected && (
        <>
          {/* The legend is the ramp itself, so the reader maps colour to number
              without a lookup table. */}
          <div
            aria-hidden
            className="mt-2.5 h-2 rounded-[2px]"
            style={{
              background:
                "linear-gradient(to right, #f8d5c8, #e8a98d, #c97a52, #9c4f28, #6b2d0f)",
            }}
          />
          <div className="mt-1 flex justify-between text-[length:var(--text-2xs)] tabular-nums text-ink-faint">
            <span>0%</span>
            <span>50%</span>
            <span>100%</span>
          </div>
          <p className="mt-2 text-[length:var(--text-2xs)] leading-snug text-ink-muted">
            Share of each cell any camera can see. Effective coverage is{" "}
            <strong className="font-semibold text-ink">
              {selected.effective_coverage_pct.toFixed(1)}%
            </strong>{" "}
            once offline cameras are excluded.
          </p>
        </>
      )}
    </div>
  );
}
