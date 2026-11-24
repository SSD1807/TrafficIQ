import { useEffect, useMemo, type ReactNode } from 'react';
import { MapContainer, TileLayer, CircleMarker, Popup, Polyline, useMap, useMapEvents } from 'react-leaflet';
import L from 'leaflet';

// Avoid default marker icon path issues with bundlers (only circle markers/polylines are used).
delete (L.Icon.Default.prototype as unknown as { _getIconUrl: unknown })._getIconUrl;

const BHUBANESWAR_CENTER: [number, number] = [20.2960, 85.8245];

export interface MapPoint {
  id: string;
  lat: number;
  lon: number;
  color: string;
  radius?: number;
  popup?: ReactNode;
}
export interface MapLine {
  positions: [number, number][];
  color: string;
  weight?: number;
  opacity?: number;
  dashArray?: string;
  label?: ReactNode;
}

interface TrafficMapProps {
  points?: MapPoint[];
  lines?: MapLine[];
  /** Context layer (e.g. the OSM road network): drawn under everything, not clickable, ignored when fitting the view. */
  backgroundLines?: MapLine[];
  height?: string;
  /** When true (default) the view fits the lines/points; otherwise stays on the city centre. */
  autoFit?: boolean;
  /** Receives map clicks as (lat, lon) — used for coordinate-based route endpoints. */
  onMapClick?: (lat: number, lon: number) => void;
  /** Optional traffic raster overlay served by the backend proxy (never a URL containing an API key). */
  trafficTileUrl?: string;
}

// 1x1 transparent GIF: failed/empty (HTTP 204) traffic tiles render as nothing instead of a broken-image icon.
const BLANK_TILE = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';

function FitView({ bounds }: { bounds: [number, number][] }) {
  const map = useMap();
  const key = useMemo(() => JSON.stringify(bounds), [bounds]);
  useEffect(() => {
    if (bounds.length >= 2) map.fitBounds(L.latLngBounds(bounds), { padding: [30, 30], maxZoom: 16, animate: false });
    else if (bounds.length === 1) map.setView(bounds[0], 14, { animate: false });
    else map.setView(BHUBANESWAR_CENTER, 12, { animate: false });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, key]);
  return null;
}

function ClickCapture({ onClick }: { onClick: (lat: number, lon: number) => void }) {
  useMapEvents({ click: (e) => onClick(e.latlng.lat, e.latlng.lng) });
  return null;
}

export function TrafficMap({ points = [], lines = [], backgroundLines = [], height = '420px', autoFit = true, onMapClick, trafficTileUrl }: TrafficMapProps) {
  const bounds = useMemo<[number, number][]>(() => {
    if (!autoFit) return [];
    const b: [number, number][] = [];
    lines.forEach((l) => l.positions.forEach((p) => b.push(p)));
    if (b.length === 0) points.forEach((p) => b.push([p.lat, p.lon]));
    return b;
  }, [autoFit, lines, points]);

  return (
    <div className="overflow-hidden rounded-xl border border-navy-700/60" style={{ height }}>
      <MapContainer center={BHUBANESWAR_CENTER} zoom={12} style={{ height: '100%', width: '100%' }} scrollWheelZoom={false} zoomAnimation={false} fadeAnimation={false} markerZoomAnimation={false}>
        <FitView bounds={bounds} />
        {onMapClick && <ClickCapture onClick={onMapClick} />}
        <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            maxZoom={19}
         />
        {trafficTileUrl && <TileLayer url={trafficTileUrl} opacity={0.9} zIndex={5} errorTileUrl={BLANK_TILE} attribution="Traffic: HERE" />}
        {backgroundLines.map((l, idx) => (
          <Polyline
            key={`bg-${idx}`}
            positions={l.positions}
            interactive={false}
            pathOptions={{ color: l.color, weight: l.weight ?? 1.5, opacity: l.opacity ?? 0.5, dashArray: l.dashArray }}
          />
        ))}
        {lines.map((l, idx) => (
          <Polyline
            key={`line-${idx}`}
            positions={l.positions}
            pathOptions={{ color: l.color, weight: l.weight ?? 5, opacity: l.opacity ?? 0.8, dashArray: l.dashArray }}
          >
            {l.label && <Popup>{l.label}</Popup>}
          </Polyline>
        ))}
        {points.map((p) => (
          <CircleMarker
            key={p.id}
            center={[p.lat, p.lon]}
            radius={p.radius ?? 9}
            pathOptions={{ color: p.color, fillColor: p.color, fillOpacity: 0.6, weight: 2 }}
          >
            {p.popup && <Popup>{p.popup}</Popup>}
          </CircleMarker>
        ))}
      </MapContainer>
    </div>
  );
}
