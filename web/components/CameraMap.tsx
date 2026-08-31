"use client";

// Named imports, not the `import maplibregl from "maplibre-gl"` default the docs
// still show: maplibre-gl's type definitions declare named exports only, so the
// default form fails typecheck with TS1192.
import {
  type ExpressionSpecification,
  Map as MapLibreMap,
  NavigationControl,
  Popup,
} from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef } from "react";

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

export function CameraMap() {
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!container.current) return;

    const map = new MapLibreMap({
      container: container.current,
      style: {
        version: 8,
        // A symbol layer draws nothing without a glyph source, so without this the
        // cluster counts would silently never appear. fonts.openmaptiles.org serves a
        // PBF variant this MapLibre cannot parse ("Unimplemented type: 4"); Plan 2
        // replaces this with glyphs bundled alongside the offline PMTiles basemap.
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

    map.addControl(new NavigationControl(), "top-right");

    map.on("load", () => {
      map.addSource("cameras", {
        type: "vector",
        tiles: [`${API}/api/v1/tiles/cameras/{z}/{x}/{y}.mvt`],
        minzoom: 0,
        maxzoom: 22,
      });

      // app/services/tiles.py switches the MVT layer name by zoom: `camera_clusters`
      // below z11, `cameras` at z11 and above. Both layers read the one source, and
      // whichever layer the tile actually carries is the one that draws.
      map.addLayer({
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

      map.addLayer({
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

      map.addLayer({
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

      map.on("click", "camera-points", (event) => {
        const feature = event.features?.[0];
        if (!feature) return;
        const { camera_uid, status, camera_type } = feature.properties as Record<
          string,
          string
        >;
        new Popup()
          .setLngLat(event.lngLat)
          .setHTML(`<strong>${camera_uid}</strong><br/>${camera_type} · ${status}`)
          .addTo(map);
      });

      map.on("mouseenter", "camera-points", () => {
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mouseleave", "camera-points", () => {
        map.getCanvas().style.cursor = "";
      });
    });

    return () => map.remove();
  }, []);

  return <div ref={container} data-testid="camera-map" className="h-screen w-full" />;
}
