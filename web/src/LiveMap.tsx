import { useEffect, useRef } from "react";
import * as maplibregl from "maplibre-gl";
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import type { GeoJSONSource, Map as MapLibreMap, Marker } from "maplibre-gl";
import type { Snapshot } from "./types";

maplibregl.setWorkerUrl(`${maplibreWorkerUrl}?module=1`);
const emptyCollection: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };

export default function LiveMap({ snapshot, followVehicle = false }: { snapshot: Snapshot | null; followVehicle?: boolean }) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<MapLibreMap | null>(null);
  const ambulance = useRef<Marker | null>(null);
  const incident = useRef<Marker | null>(null);
  const hospital = useRef<Marker | null>(null);
  const latest = useRef(snapshot);
  latest.current = snapshot;

  const sync = (instance: MapLibreMap, data: Snapshot | null) => {
    if (!data || !instance.getSource("routes")) return;
    const features: GeoJSON.Feature[] = data.routes.map((route) => ({
      type: "Feature",
      properties: { selected: route.selected, feasible: route.feasible, id: route.route_id },
      geometry: { type: "LineString", coordinates: route.geometry.map((point) => [point.lon, point.lat]) },
    }));
    (instance.getSource("routes") as GeoJSONSource).setData({ type: "FeatureCollection", features });
    const position: [number, number] = [data.vehicle.position.lon, data.vehicle.position.lat];
    if (!ambulance.current) {
      const element = document.createElement("div");
      element.className = "ambulance-marker";
      element.textContent = "+";
      ambulance.current = new maplibregl.Marker({ element }).setLngLat(position).addTo(instance);
    } else ambulance.current.setLngLat(position);
    const destination = data.routes[0]?.geometry.at(-1);
    if (destination && !hospital.current) {
      const element = document.createElement("div");
      element.className = "map-place-marker hospital-marker";
      element.textContent = "H";
      hospital.current = new maplibregl.Marker({ element }).setLngLat([destination.lon, destination.lat]).addTo(instance);
    }
    if (data.controls.accident && data.incident) {
      if (!incident.current) {
        const element = document.createElement("div");
        element.className = "map-place-marker incident-marker";
        element.textContent = "!";
        element.title = "Staged incident on mapped road";
        incident.current = new maplibregl.Marker({ element }).setLngLat([data.incident.lon, data.incident.lat]).addTo(instance);
      }
    } else {
      incident.current?.remove();
      incident.current = null;
    }
    if (followVehicle) instance.easeTo({ center: position, duration: 250 });
  };

  useEffect(() => {
    if (!container.current || map.current) return;
    const instance = new maplibregl.Map({
      container: container.current,
      center: [80.244, 13.0605], zoom: followVehicle ? 15.7 : 14.5,
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
      instance.addSource("routes", { type: "geojson", data: emptyCollection });
      instance.addLayer({ id: "route-halo", type: "line", source: "routes", paint: {
        "line-color": "#ffffff", "line-width": ["case", ["get", "selected"], 10, 7], "line-opacity": 0.95,
      } });
      instance.addLayer({ id: "route-lines", type: "line", source: "routes", paint: {
        "line-color": ["case", ["==", ["get", "feasible"], false], "#bf443b", ["get", "selected"], "#2a9b37", "#515f56"],
        "line-width": ["case", ["get", "selected"], 6, 4], "line-opacity": 0.95,
      } });
      sync(instance, latest.current);
    });
    map.current = instance;
    return () => {
      ambulance.current?.remove(); incident.current?.remove(); hospital.current?.remove();
      ambulance.current = incident.current = hospital.current = null;
      instance.remove(); map.current = null;
    };
  }, []);

  useEffect(() => { if (map.current) sync(map.current, snapshot); }, [snapshot, followVehicle]);
  return <div className="map" ref={container} aria-label="Interactive Chennai road-network map with routed ambulance path" />;
}
