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

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

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

    const instance = new MapLibreMap({
      container: container.current,
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
      center: [72.5714, 23.0225],
      zoom: 12,
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

  // The same query string against the list endpoint. It is the shared CameraFilter on
  // the server, so this count and the markers cannot disagree about what matches.
  useEffect(() => {
    let cancelled = false;
    const suffix = appliedQuery ? `&${appliedQuery}` : "";
    fetch(`${API}/api/v1/cameras?limit=1${suffix}`)
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
      <CameraDrawer camera={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
