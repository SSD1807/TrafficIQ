import type { FeedIncident, IncidentProviderState, IncidentSource } from '@/types';

export const MIN_POLL_SECONDS = 30;
export const POLL_CHOICES_SECONDS = [30, 60, 120, 300];

/** Polling interval used for auto-refresh: VITE_INCIDENT_POLL_SECONDS (default 60), never below the safe minimum. */
export function clampPollSeconds(value: unknown, fallback = 60): number {
  const n = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(n) || n <= 0) return Math.max(MIN_POLL_SECONDS, fallback);
  return Math.max(MIN_POLL_SECONDS, Math.round(n));
}

export const DEFAULT_POLL_SECONDS = clampPollSeconds(import.meta.env.VITE_INCIDENT_POLL_SECONDS, 60);

export type StatusTone = 'success' | 'info' | 'warning' | 'danger' | 'neutral';

/** How each backend provider state is shown. `live` is true ONLY for a recent successful TomTom response. */
export function describeProviderState(state: IncidentProviderState): { label: string; tone: StatusTone; live: boolean } {
  switch (state) {
    case 'live': return { label: 'Live', tone: 'success', live: true };
    case 'live_empty': return { label: 'Live · no incidents reported', tone: 'success', live: true };
    case 'cached': return { label: 'Cached data (not live)', tone: 'warning', live: false };
    case 'disabled_no_key': return { label: 'Disabled · no TomTom API key', tone: 'neutral', live: false };
    case 'disabled': return { label: 'Disabled in configuration', tone: 'neutral', live: false };
    case 'unavailable': return { label: 'Temporarily unavailable', tone: 'danger', live: false };
    default: return { label: 'Waiting for first update', tone: 'info', live: false };
  }
}

export const SOURCE_LABEL: Record<IncidentSource, string> = { tomtom: 'TomTom live', manual: 'Manual', simulation: 'Simulated' };

export function describeFeedStatus(i: Pick<FeedIncident, 'status' | 'match' | 'routing_skip_reason'>): { label: string; tone: StatusTone; detail?: string } {
  switch (i.status) {
    case 'applied': return { label: 'Applied to routing', tone: 'success' };
    case 'unmatched': return { label: 'Not matched to a road', tone: 'warning', detail: i.match?.reason_text ?? undefined };
    case 'stale': return { label: 'Stale · not applied', tone: 'neutral' };
    default: return { label: 'Matched · not applied', tone: 'info', detail: i.routing_skip_reason ?? undefined };
  }
}

/** [lat, lon] pairs for the map from GeoJSON [lon, lat] coordinates; invalid data yields an empty list. */
export function geometryToLatLon(g: FeedIncident['geometry']): [number, number][] {
  if (!g) return [];
  const pts = g.type === 'Point' ? [g.coordinates as number[]] : (g.coordinates as number[][]);
  return pts.filter((p) => Array.isArray(p) && p.length >= 2 && Number.isFinite(p[0]) && Number.isFinite(p[1])).map((p) => [p[1], p[0]] as [number, number]);
}

export function severityText(i: Pick<FeedIncident, 'severity' | 'severity_source' | 'provider'>): string {
  if (i.severity == null) return 'severity not provided';
  if (i.severity_source === 'provider_magnitude' || i.severity_source === 'provider_closure') {
    const label = i.provider?.magnitude_label;
    return `severity ${label && label !== 'undefined' ? `${label} ` : ''}(${i.severity.toFixed(2)}, from provider)`;
  }
  if (i.severity_source === 'default_for_kind') return `severity not provided by TomTom · default ${i.severity.toFixed(2)} used for routing`;
  return `severity ${i.severity.toFixed(2)}`;
}

/** Human-readable place of an incident: matched road names, TomTom from/to, road numbers, else coordinates. */
export function locationText(i: Pick<FeedIncident, 'edges' | 'provider' | 'latitude' | 'longitude'> & { location_label?: string | null }): string {
  const roads = Array.from(new Set(i.edges.map((e) => e.road_name).filter((n): n is string => !!n)));
  const parts: string[] = [];
  // The backend's readable label (OSM road name / nearest named road / junction) leads when it exists; raw ids stay a last resort.
  if (!roads.length && i.location_label) parts.push(i.location_label);
  if (roads.length) parts.push(roads.slice(0, 2).join(', '));
  if (i.provider?.from && i.provider?.to) parts.push(`between ${i.provider.from} and ${i.provider.to}`);
  else if (i.provider?.from) parts.push(`near ${i.provider.from}`);
  if (i.provider?.road_numbers?.length) parts.push(`(${i.provider.road_numbers.join('/')})`);
  if (!parts.length && i.latitude != null && i.longitude != null) parts.push(`${i.latitude.toFixed(4)}, ${i.longitude.toFixed(4)}`);
  if (!parts.length && i.edges.length) parts.push(`${i.edges[0].u} → ${i.edges[0].v}`);
  return parts.join(' ') || 'location not specified';
}

export const IMPACT_LABEL: Record<string, string> = {
  confirmed_closure: 'Confirmed road closure', unconfirmed_closure: 'Closure (unconfirmed)', major_accident: 'Major accident',
  moderate_accident: 'Moderate accident', minor_accident: 'Minor accident', construction: 'Road construction',
  lane_restriction: 'Lane restriction', obstruction: 'Obstruction', traffic_jam: 'Traffic jam', hazard: 'Hazard',
};

/** Map colours shared by both pages: closure red, accident orange, construction yellow; everything else neutral violet/grey. */
export function impactColor(impactClass: string | null | undefined, fallback = '#f43f5e'): string {
  switch (impactClass) {
    case 'confirmed_closure': return '#dc2626';
    case 'major_accident': case 'moderate_accident': case 'minor_accident': return '#f97316';
    case 'construction': case 'lane_restriction': return '#eab308';
    case 'obstruction': case 'hazard': case 'traffic_jam': return '#fb7185';
    default: return fallback;
  }
}
