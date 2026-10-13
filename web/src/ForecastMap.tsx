import { useEffect, useRef } from "react";
import * as maplibregl from "maplibre-gl";
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import type { GeoJSONSource, Map as MapLibreMap } from "maplibre-gl";
import type { ForecastSegments } from "./types";

maplibregl.setWorkerUrl(maplibreWorkerUrl);

type Properties = Record<string, unknown>;

export default function ForecastMap({
  segments,
  selected,
  onSelect,
}: {
  segments: ForecastSegments | null;
  selected: string | null;
  onSelect: (segmentId: string) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<MapLibreMap | null>(null);
  const geometry = useRef<GeoJSON.FeatureCollection | null>(null);
  const latest = useRef(segments);
  latest.current = segments;

  const paint = (instance: MapLibreMap, data: ForecastSegments | null) => {
    const shapes = geometry.current;
    if (!shapes || !instance.getSource("corridor")) return;
    const values = new Map(data?.segments.map((item) => [item.segment_id, item]) ?? []);
    const features = shapes.features.map((feature) => {
      const properties = feature.properties as Properties;
      const reading = values.get(String(properties.segment_id));
      return {
        ...feature,
        properties: {
          ...properties,
          congestion: reading?.congestion ?? 0,
          forecast_mph: reading?.forecast_mph ?? null,
          monitored: reading !== undefined,
        },
      };
    });
    (instance.getSource("corridor") as GeoJSONSource).setData({ type: "FeatureCollection", features });
  };

  useEffect(() => {
    if (!container.current || map.current) return;
    const instance = new maplibregl.Map({
      container: container.current,
      center: [80.2455, 13.0605],
      zoom: 14.2,
      attributionControl: false,
      style: {
        version: 8,
        sources: {
          osm: {
            type: "raster",
            tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
            tileSize: 256,
            attribution: '<a href="https://www.openstreetmap.org/copyright">© OpenStreetMap contributors</a>',
          },
        },
        layers: [{ id: "osm", type: "raster", source: "osm", paint: { "raster-opacity": 0.6, "raster-saturation": -0.85 } }],
      },
    });
    instance.addControl(new maplibregl.AttributionControl({ compact: true }));
    instance.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    instance.on("load", async () => {
      try {
        const response = await fetch("/api/forecast/geometry");
        if (!response.ok) return;
        geometry.current = await response.json();
      } catch {
        return;
      }
      instance.addSource("corridor", { type: "geojson", data: geometry.current! });
      instance.addLayer({
        id: "corridor-casing",
        type: "line",
        source: "corridor",
        paint: { "line-color": "#ffffff", "line-width": ["case", ["get", "on_ambulance_route"], 9, 6], "line-opacity": 0.85 },
      });
      instance.addLayer({
        id: "corridor-speed",
        type: "line",
        source: "corridor",
        paint: {
          "line-color": [
            "interpolate", ["linear"], ["coalesce", ["get", "congestion"], 0],
            0, "#2a9b37", 0.35, "#8fae2a", 0.55, "#d8a222", 0.75, "#c0392b",
          ],
          "line-width": ["case", ["get", "on_ambulance_route"], 6, 3.4],
          "line-opacity": 0.95,
        },
      });
      instance.addLayer({
        id: "corridor-selected",
        type: "line",
        source: "corridor",
        filter: ["==", ["get", "segment_id"], ""],
        paint: { "line-color": "#111111", "line-width": 3, "line-dasharray": [1.4, 1.2] },
      });
      instance.on("click", "corridor-speed", (event) => {
        const feature = event.features?.[0];
        if (feature) onSelect(String((feature.properties as Properties).segment_id));
      });
      instance.on("mouseenter", "corridor-speed", () => { instance.getCanvas().style.cursor = "pointer"; });
      instance.on("mouseleave", "corridor-speed", () => { instance.getCanvas().style.cursor = ""; });
      paint(instance, latest.current);
    });
    map.current = instance;
    return () => { instance.remove(); map.current = null; };
  }, []);

  useEffect(() => { if (map.current) paint(map.current, segments); }, [segments]);
  useEffect(() => {
    const instance = map.current;
    if (instance?.getLayer("corridor-selected")) {
      instance.setFilter("corridor-selected", ["==", ["get", "segment_id"], selected ?? ""]);
    }
  }, [selected]);

  return <div className="map" ref={container} aria-label="Chennai corridor map coloured by predicted speed" />;
}
