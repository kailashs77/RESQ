import { useEffect, useRef } from "react";
import * as maplibregl from "maplibre-gl";
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import type { Map as MapLibreMap, Marker } from "maplibre-gl";
import type { GhostReplay } from "./types";

maplibregl.setWorkerUrl(`${maplibreWorkerUrl}?module=1`);

export default function ReplayMap({ replay, frameIndex }: { replay: GhostReplay; frameIndex: number }) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<MapLibreMap | null>(null);
  const vehicles = useRef<Record<string, Marker>>({});
  const places = useRef<Marker[]>([]);
  const latestIndex = useRef(frameIndex);
  latestIndex.current = frameIndex;

  const syncVehicles = (instance: MapLibreMap, index: number) => {
    for (const vehicle of replay.frames[index].vehicles) {
      const position: [number, number] = [vehicle.position.lon, vehicle.position.lat];
      if (!vehicles.current[vehicle.baseline]) {
        const element = document.createElement("div");
        element.className = `ghost-vehicle ${vehicle.baseline === "B5" ? "resq" : "regular"}`;
        element.textContent = vehicle.baseline === "B5" ? "R" : "A";
        element.title = vehicle.baseline === "B5" ? "ResQ demonstration" : "Regular ambulance";
        vehicles.current[vehicle.baseline] = new maplibregl.Marker({ element }).setLngLat(position).addTo(instance);
      } else vehicles.current[vehicle.baseline].setLngLat(position);
    }
  };

  useEffect(() => {
    if (!container.current || map.current) return;
    const instance = new maplibregl.Map({
      container: container.current, center: [80.244, 13.0605], zoom: 14.4,
      attributionControl: false,
      style: {
        version: 8,
        sources: { osm: {
          type: "raster", tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], tileSize: 256,
          attribution: '<a href="https://www.openstreetmap.org/copyright">© OpenStreetMap contributors</a>',
        } },
        layers: [{ id: "osm", type: "raster", source: "osm", paint: { "raster-opacity": 0.68, "raster-saturation": -0.82 } }],
      },
    });
    instance.addControl(new maplibregl.AttributionControl({ compact: true }));
    instance.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    instance.on("load", () => {
      instance.addSource("road-network", { type: "geojson", data: "/api/roads" });
      instance.addLayer({ id: "road-network", type: "line", source: "road-network", paint: {
        "line-color": "#718575", "line-width": 1.5, "line-opacity": 0.65,
      } });
      instance.addSource("demo-routes", { type: "geojson", data: {
        type: "FeatureCollection", features: replay.routes.map((route) => ({
          type: "Feature", properties: { id: route.route_id },
          geometry: { type: "LineString", coordinates: route.geometry.map((point) => [point.lon, point.lat]) },
        })),
      } });
      instance.addLayer({ id: "route-halo", type: "line", source: "demo-routes", paint: {
        "line-color": "#fff", "line-width": 11, "line-opacity": 0.97,
      } });
      instance.addLayer({ id: "route-regular", type: "line", source: "demo-routes", filter: ["==", ["get", "id"], "route-a"], paint: {
        "line-color": "#535f59", "line-width": 7, "line-opacity": 0.95,
      } });
      instance.addLayer({ id: "route-resq", type: "line", source: "demo-routes", filter: ["==", ["get", "id"], "route-b"], paint: {
        "line-color": "#269d38", "line-width": 5, "line-opacity": 0.98,
      } });
      for (const [point, label, className] of [
        [replay.origin, "S", "start-marker"], [replay.hospital, "H", "hospital-marker"],
        [replay.incident, "!", "incident-marker"],
      ] as const) {
        if (!point) continue;
        const element = document.createElement("div");
        element.className = `map-place-marker ${className}`;
        element.textContent = label;
        element.title = label === "!" ? "Staged incident" : label === "H" ? "Hospital destination" : "Ambulance start";
        places.current.push(new maplibregl.Marker({ element }).setLngLat([point.lon, point.lat]).addTo(instance));
      }
      const bounds = new maplibregl.LngLatBounds();
      replay.routes.forEach((route) => route.geometry.forEach((point) => bounds.extend([point.lon, point.lat])));
      instance.fitBounds(bounds, { padding: 56, duration: 0, maxZoom: 15.4 });
      syncVehicles(instance, latestIndex.current);
    });
    map.current = instance;
    return () => {
      Object.values(vehicles.current).forEach((marker) => marker.remove()); vehicles.current = {};
      places.current.forEach((marker) => marker.remove()); places.current = [];
      instance.remove(); map.current = null;
    };
  }, [replay]);

  useEffect(() => { if (map.current?.getSource("demo-routes")) syncVehicles(map.current, frameIndex); }, [frameIndex]);
  return <div className="ghost-map" ref={container} aria-label="Interactive road map comparing regular and ResQ ambulance routes" />;
}
