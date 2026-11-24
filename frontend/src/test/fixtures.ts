import type { FeedIncident, IncidentCounts, IncidentProviderState, IncidentProviderStatus, IncidentsResponse, IncidentRefreshResponse } from '@/types';

export function provider(state: IncidentProviderState, over: Partial<IncidentProviderStatus> = {}): IncidentProviderStatus {
  const live = state === 'live' || state === 'live_empty';
  return {
    provider: 'tomtom', state, message: `message for ${state}`, enabled: state !== 'disabled', configured: state !== 'disabled_no_key',
    is_live: live, using_cached_data: state === 'cached', stale: false, incident_count: 0,
    last_attempt_at: null, last_success_at: live ? '2026-11-22T06:30:00+00:00' : null, data_age_seconds: live ? 12 : null,
    last_error: null, consecutive_failures: 0, poll_interval_seconds: 60, min_refresh_seconds: 10, stale_after_seconds: 900,
    next_allowed_refresh_at: null, bbox: [85.74, 20.22, 85.92, 20.37], attribution: 'TomTom', ...over,
  };
}

export function tomtomIncident(over: Partial<FeedIncident> = {}): FeedIncident {
  return {
    id: 'tomtom:abc', source: 'tomtom', kind: 'accident', description: 'Multi-vehicle accident', severity: 0.9, severity_source: 'provider_magnitude',
    status: 'applied', matched: true, routing_applied: true, confirmed: true, stale: false,
    start_time: '2026-11-22T05:00:00+00:00', end_time: '2026-11-22T09:00:00+00:00', expires_at: null, created_at: null,
    last_seen_at: '2026-11-22T06:30:00+00:00', provider_id: 'abc',
    edges: [{ u: '1', v: '2', road_name: 'NH16 Service Road' }], bidirectional: false,
    geometry: { type: 'LineString', coordinates: [[85.82, 20.29], [85.83, 20.3]] }, latitude: 20.295, longitude: 85.825,
    match: { status: 'matched', confidence: 0.9, reason: null, reason_text: null, direction_used: true },
    provider: { category: 'Accident', magnitude_label: 'major', from: 'Jaydev Vihar', to: 'Nandankanan Road', road_numbers: ['NH16'], probability_of_occurrence: 'certain' },
    ...over,
  };
}

export function localIncident(source: 'manual' | 'simulation', id = `${source}-1`): FeedIncident {
  return {
    ...tomtomIncident(), id, source, kind: 'road_closure', description: `${source} closure`, severity: 0.7, severity_source: 'user_defined',
    provider_id: null, geometry: null, latitude: null, longitude: null, match: null, provider: null, start_time: '2026-11-22T06:00:00+00:00',
    end_time: null, expires_at: '2026-11-22T07:00:00+00:00', edges: [{ u: '10', v: '11' }],
  };
}

export function countsOf(feed: FeedIncident[]): IncidentCounts {
  const t = feed.filter((f) => f.source === 'tomtom');
  return {
    manual: feed.filter((f) => f.source === 'manual').length, simulation: feed.filter((f) => f.source === 'simulation').length,
    tomtom: t.length, tomtom_matched: t.filter((f) => f.matched).length, tomtom_unmatched: t.filter((f) => !f.matched).length,
    tomtom_applied_to_routing: t.filter((f) => f.routing_applied).length, total: feed.length,
  };
}

export function snapshot(state: IncidentProviderState, feed: FeedIncident[] = [], over: Partial<IncidentProviderStatus> = {}): IncidentsResponse {
  return { incidents: [], source_types: ['manual', 'simulation', 'tomtom'], feed, counts: countsOf(feed), provider: provider(state, { incident_count: feed.filter((f) => f.source === 'tomtom').length, ...over }), data_notice: '' };
}

export function refreshed(s: IncidentsResponse, refreshedFlag = true): IncidentRefreshResponse {
  return { refreshed: refreshedFlag, throttled: !refreshedFlag, provider: s.provider, counts: s.counts, feed: s.feed };
}
