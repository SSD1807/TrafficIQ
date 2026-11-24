import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { MapLine, MapPoint } from '@/components/map/TrafficMap';
import { countsOf, localIncident, provider, refreshed, snapshot, tomtomIncident } from '@/test/fixtures';
import type { IncidentImpactResponse, IncidentsResponse } from '@/types';

const mapProps: { lines: MapLine[]; points: MapPoint[] } = { lines: [], points: [] };
vi.mock('@/components/map/TrafficMap', () => ({
  TrafficMap: (p: { lines?: MapLine[]; points?: MapPoint[] }) => { mapProps.lines = p.lines ?? []; mapProps.points = p.points ?? []; return <div data-testid="map" />; },
}));

const apiMock = vi.hoisted(() => ({
  getIncidents: vi.fn(), refreshIncidents: vi.fn(), getRoutingNodes: vi.fn(), calculateRoutes: vi.fn(), getIncidentImpact: vi.fn(),
  createIncident: vi.fn(), deleteIncident: vi.fn(), clearIncidents: vi.fn(),
}));
vi.mock('@/lib/api', async (orig) => ({ ...(await orig<typeof import('@/lib/api')>()), api: apiMock }));

import { IncidentManagement } from '@/pages/IncidentManagement';

const NODES = { nodes: [
  { id: '1', name: 'Alpha', latitude: 20.29, longitude: 85.82 }, { id: '2', name: 'Beta', latitude: 20.3, longitude: 85.83 },
  { id: '10', name: 'Gamma', latitude: 20.31, longitude: 85.84 }, { id: '11', name: 'Delta', latitude: 20.32, longitude: 85.85 },
], graph_source: 'openstreetmap_cached', is_real_road_geometry: true };

const route = (seq: string[]) => ({ origin_node: seq[0], destination_node: seq[seq.length - 1], algorithm: 'dijkstra', graph_source: 'x', route_count: 1, active_incident_count: 0,
  routes: [{ node_sequence: seq, polyline_latlon: [[20.29, 85.82], [20.3, 85.83]], distance_km: 1.2, estimated_travel_time_min: 4.5, segments: [] }] });

async function mount(snap: IncidentsResponse) {
  apiMock.getIncidents.mockResolvedValue(snap);
  apiMock.refreshIncidents.mockResolvedValue(refreshed(snap));
  const utils = render(<IncidentManagement />);
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  return utils;
}

describe('IncidentManagement page', () => {
  beforeEach(() => { Object.values(apiMock).forEach((m) => m.mockReset()); apiMock.getRoutingNodes.mockResolvedValue(NODES); mapProps.lines = []; mapProps.points = []; });
  afterEach(() => cleanup());

  it('shows a live indicator, counts per source, source badges and incident details', async () => {
    await mount(snapshot('live', [tomtomIncident(), localIncident('manual'), localIncident('simulation')]));
    expect(screen.getByTestId('provider-state').textContent).toBe('Live');
    expect(screen.getByTestId('tomtom-count').textContent).toBe('1');
    const items = screen.getAllByTestId('incident-item');
    expect(items.map((i) => i.getAttribute('data-source')).sort()).toEqual(['manual', 'simulation', 'tomtom']);
    const tom = items.find((i) => i.getAttribute('data-source') === 'tomtom')!;
    expect(within(tom).getByText('TomTom live')).toBeTruthy();
    expect(within(tom).getByText('Multi-vehicle accident')).toBeTruthy();
    expect(within(tom).getByText(/NH16 Service Road between Jaydev Vihar and Nandankanan Road/)).toBeTruthy();
    expect(within(tom).getByText(/major .*0\.90, from provider/)).toBeTruthy();
    expect(within(tom).getByText('Applied to routing')).toBeTruthy();
    expect(within(tom).getByText(/started/)).toBeTruthy();
    expect(within(tom).getByText(/expected end/)).toBeTruthy();
    const badgeOf = (src: string) => within(items.find((i) => i.getAttribute('data-source') === src)!);
    expect(badgeOf('manual').getByText('Manual')).toBeTruthy();
    expect(badgeOf('simulation').getByText('Simulated')).toBeTruthy();
    expect(screen.getAllByText(/not a live report/).length).toBe(2);
    expect(screen.getByTestId('last-success').textContent).not.toBe('—');
  });

  it('Refresh now calls the backend refresh endpoint and is disabled while refreshing', async () => {
    await mount(snapshot('live', []));
    expect(apiMock.refreshIncidents).toHaveBeenCalledTimes(1);   // the initial load
    let release: () => void = () => {};
    apiMock.refreshIncidents.mockImplementation(() => new Promise((res) => { release = () => res(refreshed(snapshot('live', [tomtomIncident()]))); }));
    const btn = screen.getByRole('button', { name: /refresh now/i });
    await act(async () => { fireEvent.click(btn); });
    expect(apiMock.refreshIncidents).toHaveBeenCalledTimes(2);
    const busy = screen.getByRole('button', { name: /refreshing/i }) as HTMLButtonElement;
    expect(busy.disabled).toBe(true);
    await act(async () => { fireEvent.click(busy); });
    expect(apiMock.refreshIncidents).toHaveBeenCalledTimes(2);   // no overlapping request
    await act(async () => { release(); await Promise.resolve(); });
    expect(screen.getByTestId('tomtom-count').textContent).toBe('1');
  });

  it('missing API key: shows disabled status and never presents anything as live', async () => {
    await mount(snapshot('disabled_no_key', [localIncident('manual')]));
    expect(screen.getByTestId('provider-state').textContent).toBe('Disabled · no TomTom API key');
    expect(screen.getByTestId('provider-message').textContent).toContain('message for disabled_no_key');
    expect(screen.getByTestId('tomtom-count').textContent).toBe('0');
    expect(screen.queryByText('TomTom live', { selector: 'span' })).toBeNull();
    expect(screen.getAllByTestId('incident-item')).toHaveLength(1);
  });

  it('live but empty is shown as a valid result', async () => {
    await mount(snapshot('live_empty', []));
    expect(screen.getByTestId('provider-state').textContent).toContain('no incidents reported');
    expect(screen.getByTestId('empty-feed').textContent).toContain('TomTom currently reports no incidents');
  });

  it('provider error and cached data are shown with the failure reason', async () => {
    const snap = snapshot('cached', [tomtomIncident()], { last_error: { type: 'server_error', message: 'TomTom server error (HTTP 503)', http_status: 503 }, consecutive_failures: 3 });
    await mount(snap);
    expect(screen.getByTestId('provider-state').textContent).toBe('Cached data (not live)');
    expect(screen.getByTestId('provider-error').textContent).toContain('TomTom server error (HTTP 503)');
    expect(screen.getByTestId('provider-error').textContent).toContain('3 consecutive failures');
    expect(screen.getAllByTestId('incident-item')).toHaveLength(1);   // cached incidents stay visible
  });

  it('unavailable state without any data shows no incidents and explains why', async () => {
    await mount(snapshot('unavailable', [], { last_error: { type: 'auth', message: 'TomTom rejected the API key (HTTP 403)', http_status: 403 } }));
    expect(screen.getByTestId('provider-state').textContent).toBe('Temporarily unavailable');
    expect(screen.getByTestId('empty-feed').textContent).toContain('Provider status: Temporarily unavailable');
    expect(screen.getByTestId('empty-feed').textContent).not.toContain('reports no incidents');
  });

  it('a failing backend request keeps the last data and shows a warning', async () => {
    await mount(snapshot('live', [tomtomIncident()]));
    apiMock.refreshIncidents.mockRejectedValue(new Error('network'));
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /refresh now/i })); await Promise.resolve(); });
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    expect(screen.getByTestId('request-error')).toBeTruthy();
    expect(screen.getAllByTestId('incident-item')).toHaveLength(1);
  });

  it('shows unmatched TomTom incidents separately (dashed on the map) and matched ones as solid lines', async () => {
    const unmatched = tomtomIncident({ id: 'tomtom:u', provider_id: 'u', description: 'Debris', status: 'unmatched', matched: false, routing_applied: false, edges: [],
      geometry: { type: 'Point', coordinates: [85.83, 20.3] }, match: { status: 'unmatched', confidence: 0, reason: 'no_nearby_road', reason_text: 'No road segment lies close enough.', direction_used: false } });
    await mount(snapshot('live', [tomtomIncident(), unmatched]));
    expect(screen.getByText('Not matched to a road')).toBeTruthy();
    expect(screen.getByText('No road segment lies close enough.')).toBeTruthy();
    expect(screen.getByTestId('tomtom-count').textContent).toBe('2');
    expect(mapProps.lines.some((l) => l.color === '#f43f5e' && !l.dashArray)).toBe(true);      // matched & applied
    expect(mapProps.points.some((p) => p.id === 'tomtom:u' && p.color === '#f59e0b')).toBe(true); // unmatched point marker
    expect(screen.getByTestId('map-legend').textContent).toContain('unmatched');
  });

  it('keeps manual creation and only allows deleting manual/simulated incidents', async () => {
    await mount(snapshot('live', [tomtomIncident(), localIncident('manual', 'm-1')]));
    expect(screen.getByRole('button', { name: /create incident/i })).toBeTruthy();
    expect(screen.getAllByRole('button', { name: /delete incident/i })).toHaveLength(1);
    expect(screen.getByRole('button', { name: 'Delete incident m-1' })).toBeTruthy();
    apiMock.deleteIncident.mockResolvedValue({ removed: true, incident_id: 'm-1' });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Delete incident m-1' })); await Promise.resolve(); });
    expect(apiMock.deleteIncident).toHaveBeenCalledWith('m-1');
  });

  it('creates a manual incident through the existing form', async () => {
    await mount(snapshot('live_empty', []));
    apiMock.createIncident.mockResolvedValue({ id: 'abcdefgh-1', edge_u: '1', edge_v: '2' });
    fireEvent.change(screen.getByLabelText('Edge from (node ID)'), { target: { value: '1' } });
    fireEvent.change(screen.getByLabelText('Edge to (node ID)'), { target: { value: '2' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /create incident/i })); await Promise.resolve(); });
    expect(apiMock.createIncident).toHaveBeenCalledWith(expect.objectContaining({ edge_u: '1', edge_v: '2', source: 'manual', kind: 'accident' }));
  });

  it('filters the feed by source', async () => {
    await mount(snapshot('live', [tomtomIncident(), localIncident('manual')]));
    fireEvent.change(screen.getByLabelText('Show'), { target: { value: 'manual' } });
    expect(screen.getAllByTestId('incident-item').map((i) => i.getAttribute('data-source'))).toEqual(['manual']);
  });

  it('shows the alternative route after incident recalculation', async () => {
    await mount(snapshot('live', [tomtomIncident({ kind: 'road_closure', description: 'Closed for repairs' })]));
    const impact: IncidentImpactResponse = {
      rerouted: true, reason: 'The fastest route without live incidents crosses 1 active incident(s); a different route is now fastest.',
      without_live_incidents: route(['1', '2']) as never, with_incidents: route(['1', '10', '2']) as never,
      incidents_on_original_route: [tomtomIncident({ description: 'Closed for repairs' })], incidents_on_new_route: [],
      delta: { travel_time_min: 2.4, distance_km: 0.35 }, provider: provider('live'),
    };
    apiMock.getIncidentImpact.mockResolvedValue(impact);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /show alternative around incidents/i })); await Promise.resolve(); });
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    const panel = screen.getByTestId('impact-panel');
    expect(panel.textContent).toContain('Rerouted around incident(s)');
    expect(panel.textContent).toContain('+2.4 min');
    expect(panel.textContent).toContain('Closed for repairs');
    expect(mapProps.lines.some((l) => l.color === '#94a3b8')).toBe(true);   // original route (dotted grey)
    expect(mapProps.lines.some((l) => l.color === '#22d3ee')).toBe(true);   // alternative route
  });

  it('sends the chosen algorithm (A*) to the impact endpoint', async () => {
    await mount(snapshot('live_empty', []));
    apiMock.getIncidentImpact.mockResolvedValue({ rerouted: false, reason: 'No active incident affects the fastest route.', without_live_incidents: route(['1', '2']),
      with_incidents: route(['1', '2']), incidents_on_original_route: [], incidents_on_new_route: [], delta: { travel_time_min: 0, distance_km: 0 }, provider: provider('live_empty') });
    fireEvent.change(screen.getByLabelText('Algorithm'), { target: { value: 'astar' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: /show alternative around incidents/i })); await Promise.resolve(); });
    expect(apiMock.getIncidentImpact).toHaveBeenCalledWith(expect.objectContaining({ algorithm: 'astar', origin_node: '1' }));
  });

  it('counts come from the backend per source', async () => {
    const feed = [tomtomIncident(), tomtomIncident({ id: 'tomtom:2', provider_id: '2' }), localIncident('manual'), localIncident('simulation')];
    expect(countsOf(feed)).toMatchObject({ tomtom: 2, manual: 1, simulation: 1 });
    await mount(snapshot('live', feed));
    expect(screen.getAllByTestId('incident-item')).toHaveLength(4);
  });

  it('auto-refresh toggle and interval controls are present and respect the 30 s minimum', async () => {
    await mount(snapshot('live', []));
    const toggle = screen.getByLabelText('Auto-refresh') as HTMLInputElement;
    expect(toggle.checked).toBe(true);
    const interval = screen.getByLabelText('Every') as HTMLSelectElement;
    expect(interval.value).toBe('60');
    expect(Array.from(interval.options).map((o) => Number(o.value)).every((v) => v >= 30)).toBe(true);
    fireEvent.click(toggle);
    expect(toggle.checked).toBe(false);
  });
});
