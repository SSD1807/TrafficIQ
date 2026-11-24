import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Siren, AlertTriangle, CheckCircle2, Trash2, Plus, Route as RouteIcon, Clock, Repeat, Layers, RefreshCw, Radio, WifiOff, MapPinOff, Navigation } from 'lucide-react';
import { PageHeader } from '@/components/ui/PageHeader';
import { Card, StatCard } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Input, Select } from '@/components/ui/Select';
import { LoadingState, ErrorState } from '@/components/ui/States';
import { ProvenanceBanner, SourceTag } from '@/components/ui/Provenance';
import { TrafficMap, type MapLine, type MapPoint } from '@/components/map/TrafficMap';
import { api, ApiError } from '@/lib/api';
import { useApi } from '@/lib/useApi';
import { useIncidentFeed } from '@/lib/useIncidentFeed';
import { useRoadLayer } from '@/lib/useRoadLayer';
import { describeFeedStatus, describeProviderState, geometryToLatLon, IMPACT_LABEL, impactColor, locationText, POLL_CHOICES_SECONDS, severityText, SOURCE_LABEL, type StatusTone } from '@/lib/incidentStatus';
import { formatDateTime, incidentSeverity } from '@/lib/utils';
import type { CalculatedRoute, FeedIncident, IncidentCreate, IncidentImpactResponse, IncidentKind, IncidentSource, RouteCalculationResponse } from '@/types';

const KINDS: { value: IncidentKind; label: string }[] = [
  { value: 'accident', label: 'Accident' }, { value: 'road_closure', label: 'Road closure' }, { value: 'road_work', label: 'Road work' },
  { value: 'obstruction', label: 'Obstruction' }, { value: 'hazard', label: 'Hazard' }, { value: 'traffic_jam', label: 'Traffic jam' },
];
const EXPIRY = [{ value: '600', label: '10 minutes' }, { value: '3600', label: '1 hour' }, { value: '21600', label: '6 hours' }, { value: '86400', label: '24 hours' }];
const ALGORITHMS = [{ value: 'dijkstra', label: 'Dijkstra' }, { value: 'astar', label: 'A*' }];
const FILTERS: { value: 'all' | IncidentSource; label: string }[] = [
  { value: 'all', label: 'All sources' }, { value: 'tomtom', label: 'TomTom live' }, { value: 'manual', label: 'Manual' }, { value: 'simulation', label: 'Simulated' },
];
const msg = (e: unknown, f: string) => (e instanceof ApiError ? e.message : f);
const TONE_VARIANT: Record<StatusTone, 'success' | 'info' | 'warning' | 'danger' | 'neutral'> = { success: 'success', info: 'info', warning: 'warning', danger: 'danger', neutral: 'neutral' };
const SOURCE_VARIANT: Record<IncidentSource, 'info' | 'neutral' | 'warning'> = { tomtom: 'info', manual: 'neutral', simulation: 'warning' };

export function IncidentManagement() {
  const live = useIncidentFeed();
  const roads = useRoadLayer('major');
  const { state, loading, refreshing, requestError, autoRefresh, setAutoRefresh, intervalSec, setIntervalSec, refreshNow, reload } = live;
  const feed = state?.feed ?? [];
  const counts = state?.counts;
  const provider = state?.provider;

  const graph = useApi(() => api.getRoutingNodes(2000), []);
  const nodeById = useMemo(() => new Map((graph.data?.nodes ?? []).map((n) => [n.id, n])), [graph.data]);
  const nodeLabel = (n: { id: string; name: string; label_source?: string }) => (n.label_source && n.label_source !== 'coordinates' ? n.name : n.name !== n.id ? `${n.name} (${n.id})` : n.id);
  const nodeOptions = (graph.data?.nodes ?? []).slice(0, 250).map((n) => ({ value: n.id, label: nodeLabel(n) }));

  // ---- route used to pick real edges and to demonstrate dynamic rerouting ----
  const [origin, setOrigin] = useState('');
  const [dest, setDest] = useState('');
  const [algorithm, setAlgorithm] = useState<'dijkstra' | 'astar'>('dijkstra');
  const [route, setRoute] = useState<RouteCalculationResponse | null>(null);
  const [prev, setPrev] = useState<CalculatedRoute | null>(null);
  const [routeLoading, setRouteLoading] = useState(false);
  const [routeError, setRouteError] = useState<string | null>(null);
  const [impact, setImpact] = useState<IncidentImpactResponse | null>(null);
  const [impactLoading, setImpactLoading] = useState(false);
  const [impactError, setImpactError] = useState<string | null>(null);
  const hasRoute = useRef(false);
  const lastBest = useRef<CalculatedRoute | null>(null);

  useEffect(() => {
    const n = graph.data?.nodes ?? [];
    if (n.length >= 2 && !origin) { setOrigin(n[0].id); setDest(n[n.length > 7 ? 7 : 1].id); }
  }, [graph.data, origin]);

  const routeBody = useCallback(() => ({ origin_node: origin, destination_node: dest, algorithm, alternatives: 1, rainfall_mm: 0, use_simulated_congestion: false }), [origin, dest, algorithm]);

  const computeRoute = useCallback(async () => {
    if (!origin || !dest || origin === dest) return;
    setRouteLoading(true); setRouteError(null);
    try {
      const res = await api.calculateRoutes(routeBody());
      setPrev(lastBest.current);
      lastBest.current = res.routes[0] ?? null;
      setRoute(res);
      hasRoute.current = true;
    } catch (e) { setRouteError(msg(e, 'Route calculation failed.')); }
    finally { setRouteLoading(false); }
  }, [origin, dest, routeBody]);

  const checkImpact = useCallback(async () => {
    if (!origin || !dest || origin === dest) return;
    setImpactLoading(true); setImpactError(null);
    try { setImpact(await api.getIncidentImpact(routeBody())); }
    catch (e) { setImpact(null); setImpactError(msg(e, 'Could not compare routes.')); }
    finally { setImpactLoading(false); }
  }, [origin, dest, routeBody]);

  const best = route?.routes[0];
  const rerouted = !!(prev && best && prev.node_sequence.join('>') !== best.node_sequence.join('>'));

  // When live incidents that affect routing change, re-plan the displayed route (never on first render).
  const appliedSignature = feed.filter((f) => f.routing_applied).map((f) => f.id).sort().join('|');
  const lastSignature = useRef<string | null>(null);
  useEffect(() => {
    if (lastSignature.current !== null && lastSignature.current !== appliedSignature && hasRoute.current) void computeRoute();
    lastSignature.current = appliedSignature;
  }, [appliedSignature, computeRoute]);

  // ---- create form ----
  const [form, setForm] = useState<IncidentCreate>({ edge_u: '', edge_v: '', kind: 'accident', severity: 0.7, source: 'manual', expires_in_seconds: 3600, bidirectional: false, description: '' });
  const [creating, setCreating] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [filter, setFilter] = useState<'all' | IncidentSource>('all');

  const create = async () => {
    setCreating(true); setFormError(null); setNotice(null);
    try {
      const rec = await api.createIncident({ ...form, edge_u: form.edge_u.trim(), edge_v: form.edge_v.trim() });
      setNotice(`Incident ${rec.id.slice(0, 8)} created on ${rec.edge_u} → ${rec.edge_v}.`);
      await reload();
      if (hasRoute.current) await computeRoute();
    } catch (e) { setFormError(msg(e, 'Could not create the incident.')); }
    finally { setCreating(false); }
  };

  const remove = async (id: string) => {
    setNotice(null);
    try {
      await api.deleteIncident(id);
      setNotice(`Incident ${id.slice(0, 8)} removed.`);
    } catch (e) { setFormError(msg(e, 'Could not delete the incident.')); }
    await reload();
    if (hasRoute.current) await computeRoute();
  };

  const clearLocal = async () => {
    if (!window.confirm('Remove ALL manual and simulated incidents? TomTom incidents are provider-managed and are not affected.')) return;
    try { const r = await api.clearIncidents(); setNotice(`${r.removed_count} manual/simulated incident(s) removed.`); }
    catch (e) { setFormError(msg(e, 'Could not clear incidents.')); }
    await reload();
    if (hasRoute.current) await computeRoute();
  };

  const visible = feed.filter((f) => filter === 'all' || f.source === filter);
  const localCount = (counts?.manual ?? 0) + (counts?.simulation ?? 0);
  const ps = provider ? describeProviderState(provider.state) : null;

  // ---- map layers ----
  const lines: MapLine[] = [];
  const points: MapPoint[] = [];
  const popupFor = (inc: FeedIncident) => (
    <><strong className="capitalize">{inc.kind.replace('_', ' ')}</strong> · {SOURCE_LABEL[inc.source]}<br />{inc.description}<br />{locationText(inc)}<br />{describeFeedStatus(inc).label}</>
  );
  if (impact?.without_live_incidents.routes[0]?.polyline_latlon?.length)
    lines.push({ positions: impact.without_live_incidents.routes[0].polyline_latlon, color: '#94a3b8', weight: 5, opacity: 0.9, dashArray: '2 10', label: 'Route without live incidents' });
  const altRoute = impact?.with_incidents.routes?.[0];
  if (altRoute?.polyline_latlon?.length)
    lines.push({ positions: altRoute.polyline_latlon, color: '#22d3ee', weight: 5, opacity: 0.9, label: impact?.rerouted ? 'Alternative route (incidents applied)' : 'Route with incidents applied' });
  else if (best?.polyline_latlon?.length) lines.push({ positions: best.polyline_latlon, color: '#22d3ee', weight: 5, opacity: 0.9, label: 'Current best route' });
  feed.forEach((inc) => {
    if (inc.source === 'tomtom') {
      const pos = geometryToLatLon(inc.geometry);
      const color = inc.status === 'applied' ? impactColor(inc.impact_class, '#f43f5e') : inc.status === 'unmatched' ? '#f59e0b' : '#a3a3a3';
      const dashed = inc.status === 'unmatched' ? '6 8' : undefined;
      if (pos.length >= 2) lines.push({ positions: pos, color, weight: 8, opacity: 0.9, dashArray: dashed, label: popupFor(inc) });
      else if (pos.length === 1) points.push({ id: inc.id, lat: pos[0][0], lon: pos[0][1], color, radius: 9, popup: popupFor(inc) });
      return;
    }
    inc.edges.forEach((e) => {
      const a = nodeById.get(e.u); const b = nodeById.get(e.v);
      if (a && b) lines.push({ positions: [[a.latitude, a.longitude], [b.latitude, b.longitude]], color: '#a78bfa', weight: 8, opacity: 0.9, label: popupFor(inc) });
    });
  });

  return (
    <div className="space-y-6">
      <PageHeader title="Incident Management" description="Live TomTom incidents plus manual and simulated test incidents; routes re-plan around confirmed incidents"
        icon={<Siren className="h-6 w-6" />}
        action={<Button variant="secondary" size="sm" icon={<Trash2 className="h-3.5 w-3.5" />} onClick={clearLocal} disabled={localCount === 0}>Clear manual &amp; simulated</Button>} />

      <ProvenanceBanner tone="info">
        <strong>Every incident is labelled by source.</strong> <em>TomTom live</em> = incidents reported by the TomTom Traffic API and fetched by the backend (the API key never reaches this browser).
        {' '}<em>Manual</em> and <em>Simulated</em> = test incidents you create; they are not real reports. Roads are matched on OpenStreetMap geometry.
        Traffic predictions and forecasts elsewhere in the app use a synthetic historical dataset and are not affected by this feed.
      </ProvenanceBanner>

      <Card title="Live TomTom incidents" subtitle="Provider connection status" icon={<Radio className="h-5 w-5" />} action={<SourceTag kind="incident-live" />}>
        {loading && !provider ? <LoadingState label="Contacting backend…" /> : (
          <div className="space-y-4" data-testid="live-status">
            <div className="flex flex-wrap items-center gap-3">
              <span className={`inline-block h-2.5 w-2.5 rounded-full ${ps?.live ? 'animate-pulse bg-teal-400' : ps?.tone === 'danger' ? 'bg-rose-400' : ps?.tone === 'warning' ? 'bg-amber-400' : 'bg-slate-500'}`} aria-hidden />
              <span className="text-sm font-semibold text-slate-100" data-testid="provider-state">{ps ? ps.label : 'Status unknown'}</span>
              {ps && <Badge variant={TONE_VARIANT[ps.tone]}>{provider?.state}</Badge>}
              {provider?.stale && <Badge variant="danger">stale · not applied to routing</Badge>}
            </div>
            {provider && <p className="text-xs text-slate-400" data-testid="provider-message">{provider.message}</p>}
            {provider?.last_error && (
              <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-200" role="alert" data-testid="provider-error">
                <WifiOff className="mr-1 inline h-3.5 w-3.5" />Last attempt failed: {provider.last_error.message}{provider.last_error.http_status ? ` (HTTP ${provider.last_error.http_status})` : ''}
                {provider.consecutive_failures > 1 ? ` · ${provider.consecutive_failures} consecutive failures` : ''}
              </div>
            )}
            {requestError && (
              <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200" role="alert" data-testid="request-error">
                Backend request failed: {requestError} Showing the last known data.
              </div>
            )}
            <div className="grid grid-cols-2 gap-3 text-xs text-slate-300 lg:grid-cols-4">
              <div><p className="text-slate-500">Last successful update</p><p data-testid="last-success">{formatDateTime(provider?.last_success_at)}</p></div>
              <div><p className="text-slate-500">Data age</p><p>{provider?.data_age_seconds != null ? `${Math.round(provider.data_age_seconds)} s` : '—'}</p></div>
              <div><p className="text-slate-500">Active TomTom incidents</p><p data-testid="tomtom-count">{counts?.tomtom ?? 0}</p></div>
              <div><p className="text-slate-500">Search area (bbox)</p><p className="break-all">{provider?.bbox?.map((n) => n.toFixed(3)).join(', ') ?? '—'}</p></div>
            </div>
            <div className="flex flex-wrap items-end gap-4">
              <Button onClick={() => void refreshNow()} disabled={refreshing} icon={<RefreshCw className={`h-4 w-4 ${refreshing ? 'animate-spin' : ''}`} />}>{refreshing ? 'Refreshing…' : 'Refresh now'}</Button>
              <label className="flex items-center gap-2 text-xs text-slate-300">
                <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} /> Auto-refresh
              </label>
              <Select id="poll-interval" label="Every" value={String(intervalSec)} onChange={(v) => setIntervalSec(Number(v))}
                options={POLL_CHOICES_SECONDS.map((s) => ({ value: String(s), label: s >= 60 ? `${s / 60} min` : `${s} s` }))} />
              <p className="text-[11px] text-slate-500">Minimum 30 s. Requests never overlap, pause while this tab is hidden, and are rate-limited again by the backend to respect TomTom quotas.</p>
            </div>
          </div>
        )}
      </Card>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        <StatCard label="TomTom live incidents" value={String(counts?.tomtom ?? 0)} icon={<Radio className="h-5 w-5" />} />
        <StatCard label="Manual incidents" value={String(counts?.manual ?? 0)} icon={<Layers className="h-5 w-5" />} />
        <StatCard label="Simulated incidents" value={String(counts?.simulation ?? 0)} icon={<Layers className="h-5 w-5" />} />
        <StatCard label="TomTom matched to road" value={String(counts?.tomtom_matched ?? 0)} icon={<Navigation className="h-5 w-5" />} />
        <StatCard label="TomTom unmatched" value={String(counts?.tomtom_unmatched ?? 0)} icon={<MapPinOff className="h-5 w-5" />} />
      </div>

      {(notice || formError) && (
        <div className={`rounded-lg border px-4 py-2 text-xs ${formError ? 'border-rose-500/30 bg-rose-500/10 text-rose-200' : 'border-teal-500/30 bg-teal-500/10 text-teal-200'}`} role={formError ? 'alert' : 'status'}>
          {formError ?? notice}
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <Card title="Incident feed" subtitle={`${visible.length} shown · ${feed.length} active`} className="lg:col-span-2"
          action={<Select id="source-filter" label="Show" value={filter} onChange={(v) => setFilter(v as 'all' | IncidentSource)} options={FILTERS} />}>
          {loading && !state ? <LoadingState /> : !state ? <ErrorState message={requestError ?? 'Could not load incidents.'} onRetry={() => void reload()} /> :
            visible.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-12 text-slate-400" data-testid="empty-feed">
                <CheckCircle2 className="h-10 w-10 text-teal-400" />
                <p className="mt-3 text-sm">{filter === 'tomtom' || (filter === 'all' && feed.length === 0 && provider?.state === 'live_empty')
                  ? 'TomTom currently reports no incidents for this area.' : 'No active incidents.'}</p>
                {provider && !provider.is_live && <p className="mt-1 text-xs text-slate-500">Provider status: {describeProviderState(provider.state).label}</p>}
              </div>
            ) : (
              <div className="space-y-2">
                {visible.map((inc) => {
                  const fs = describeFeedStatus(inc);
                  const isTomTom = inc.source === 'tomtom';
                  return (
                    <div key={inc.id} data-testid="incident-item" data-source={inc.source}
                      className={`flex items-start gap-3 rounded-lg border p-3 ${isTomTom ? 'border-cyan-500/30 bg-cyan-500/5' : 'border-navy-700/50 bg-navy-800/40'}`}>
                      <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-navy-700/40 ${isTomTom ? 'text-cyan-300' : 'text-rose-400'}`}>{isTomTom ? <Radio className="h-4 w-4" /> : <AlertTriangle className="h-4 w-4" />}</div>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center justify-between gap-2">
                          <p className="truncate text-sm font-medium capitalize text-slate-200">{inc.provider?.category ?? inc.kind.replace('_', ' ')}{!isTomTom && inc.edges[0] ? ` · ${inc.location_label ?? `${inc.edges[0].u} → ${inc.edges[0].v}`}${inc.bidirectional ? ' (both directions)' : ''}` : ''}</p>
                          <Badge variant={SOURCE_VARIANT[inc.source]}>{SOURCE_LABEL[inc.source]}</Badge>
                        </div>
                        {inc.description && <p className="mt-0.5 text-xs text-slate-300">{inc.description}</p>}
                        <p className="mt-0.5 text-xs text-slate-400">{isTomTom ? locationText(inc) : 'User-created test incident (not a live report)'}</p>
                        <div className="mt-1.5 flex flex-wrap items-center gap-2">
                          {inc.severity != null && <Badge severity={incidentSeverity(inc.severity)}>{severityText(inc)}</Badge>}
                          {isTomTom && <Badge variant={TONE_VARIANT[fs.tone]}>{fs.label}</Badge>}
                          {isTomTom && !inc.confirmed && <Badge variant="warning">{inc.provider?.probability_of_occurrence ?? 'unconfirmed'}</Badge>}
                          {inc.impact_class && <Badge variant={inc.impact_class === 'confirmed_closure' ? 'danger' : 'warning'}>{IMPACT_LABEL[inc.impact_class] ?? inc.impact_class}</Badge>}
                          {isTomTom && <Badge variant={inc.matched ? 'success' : 'neutral'}>{inc.matched ? 'Matched to OSM road' : 'No OSM road match'}</Badge>}
                          {inc.routing_effect && <Badge variant="info">{inc.routing_effect === 'block' ? 'Blocks road in routing' : inc.routing_effect === 'penalty' ? 'Adds delay in routing' : 'Not used in routing'}</Badge>}
                        </div>
                        {inc.explanation && <p className="mt-1 text-[11px] text-slate-400" data-testid="impact-explanation">{inc.explanation}</p>}
                        {inc.unmatched_reason && inc.unmatched_reason !== fs.detail && !inc.matched && <p className="mt-1 text-[11px] text-amber-300/80">{inc.unmatched_reason}</p>}
                        {fs.detail && <p className="mt-1 text-[11px] text-amber-300/80">{fs.detail}</p>}
                        <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] text-slate-500">
                          {isTomTom ? (
                            <>
                              <span className="flex items-center gap-1"><Clock className="h-3 w-3" /> started {formatDateTime(inc.start_time)}</span>
                              <span>{inc.end_time ? `expected end ${formatDateTime(inc.end_time)}` : 'end time not provided'}</span>
                              <span>last seen {formatDateTime(inc.last_seen_at)}</span>
                            </>
                          ) : <span className="flex items-center gap-1"><Clock className="h-3 w-3" /> expires {formatDateTime(inc.expires_at)}</span>}
                        </div>
                      </div>
                      {!isTomTom && <Button size="sm" variant="secondary" aria-label={`Delete incident ${inc.id}`} icon={<Trash2 className="h-3.5 w-3.5" />} onClick={() => remove(inc.id)}>Delete</Button>}
                    </div>
                  );
                })}
              </div>
            )}
        </Card>

        <Card title="Create incident" subtitle="Manual / simulated test incident" icon={<Plus className="h-5 w-5" />} action={<SourceTag kind="incident-sim" />}>
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <Input id="edge-u" label="Edge from (node ID)" value={form.edge_u} onChange={(v) => setForm({ ...form, edge_u: v })} />
              <Input id="edge-v" label="Edge to (node ID)" value={form.edge_v} onChange={(v) => setForm({ ...form, edge_v: v })} />
            </div>
            {(nodeById.get(form.edge_u.trim()) || nodeById.get(form.edge_v.trim())) && (
              <p className="text-[11px] text-slate-500">{nodeById.get(form.edge_u.trim())?.name ?? '—'} → {nodeById.get(form.edge_v.trim())?.name ?? '—'}</p>
            )}
            <Select id="kind" label="Type" value={form.kind} onChange={(v) => setForm({ ...form, kind: v as IncidentKind })} options={KINDS} />
            <div>
              <label htmlFor="sev" className="text-xs font-medium text-slate-400">Severity: {form.severity.toFixed(2)}</label>
              <input id="sev" type="range" min={0} max={1} step={0.05} value={form.severity} onChange={(e) => setForm({ ...form, severity: Number(e.target.value) })} className="w-full" />
            </div>
            <Select id="source" label="Source label" value={form.source} onChange={(v) => setForm({ ...form, source: v as 'manual' | 'simulation' })} options={[{ value: 'manual', label: 'manual' }, { value: 'simulation', label: 'simulation' }]} />
            <Select id="expiry" label="Expires after" value={String(form.expires_in_seconds)} onChange={(v) => setForm({ ...form, expires_in_seconds: Number(v) })} options={EXPIRY} />
            <Input id="desc" label="Description" value={form.description} onChange={(v) => setForm({ ...form, description: v })} />
            <label className="flex items-center gap-2 text-xs text-slate-300"><input type="checkbox" checked={form.bidirectional} onChange={(e) => setForm({ ...form, bidirectional: e.target.checked })} /> Affects both directions</label>
            <Button onClick={create} disabled={creating || !form.edge_u.trim() || !form.edge_v.trim()} icon={<Plus className="h-4 w-4" />} className="w-full">{creating ? 'Creating…' : 'Create incident'}</Button>
          </div>
        </Card>
      </div>

      <Card title="Map, rerouting & incident impact" subtitle="Calculate a route, compare it with and without live incidents, or click a segment to fill the form" icon={<RouteIcon className="h-5 w-5" />}>
        {graph.loading ? <LoadingState label="Loading road graph…" /> : graph.error ? <ErrorState message={graph.error} onRetry={graph.reload} /> : (
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <div className="space-y-3">
              <div className="grid grid-cols-3 gap-3">
                <Select id="inc-origin" label="Origin node" value={origin} onChange={setOrigin} options={nodeOptions} />
                <Select id="inc-dest" label="Destination node" value={dest} onChange={setDest} options={nodeOptions} />
                <Select id="inc-algo" label="Algorithm" value={algorithm} onChange={(v) => setAlgorithm(v as 'dijkstra' | 'astar')} options={ALGORITHMS} />
              </div>
              <div className="flex flex-wrap gap-2">
                <Button variant="secondary" onClick={computeRoute} disabled={routeLoading || !origin || !dest || origin === dest} icon={<Repeat className="h-4 w-4" />}>{routeLoading ? 'Calculating…' : 'Calculate route'}</Button>
                <Button onClick={checkImpact} disabled={impactLoading || !origin || !dest || origin === dest} icon={<Navigation className="h-4 w-4" />}>{impactLoading ? 'Comparing…' : 'Show alternative around incidents'}</Button>
              </div>
              {routeError && <ErrorState message={routeError} />}
              {impactError && <ErrorState message={impactError} />}
              {impact && (
                <div className="space-y-2 rounded-lg border border-cyan-500/20 bg-cyan-500/5 p-3 text-xs" data-testid="impact-panel">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant={impact.rerouted ? 'warning' : 'success'}>{impact.rerouted ? 'Rerouted around incident(s)' : 'No route change'}</Badge>
                    {impact.delta.travel_time_min != null && <Badge variant="info">{impact.delta.travel_time_min >= 0 ? '+' : ''}{impact.delta.travel_time_min.toFixed(1)} min · {(impact.delta.distance_km ?? 0) >= 0 ? '+' : ''}{(impact.delta.distance_km ?? 0).toFixed(2)} km</Badge>}
                    {!impact.provider.is_live && <Badge variant="neutral">TomTom: {describeProviderState(impact.provider.state).label}</Badge>}
                  </div>
                  <p className="text-slate-300">{impact.reason}</p>
                  {impact.with_incidents.error && <p className="text-rose-300">{impact.with_incidents.error}</p>}
                  {impact.incidents_on_original_route.map((i) => <p key={i.id} className="text-slate-400">• {SOURCE_LABEL[i.source]}: {i.description || i.kind.replace('_', ' ')} — {locationText(i)}</p>)}
                  {impact.plan?.decision && (
                    <div className="rounded border border-navy-700/50 bg-navy-900/40 p-2" data-testid="engine-decision">
                      <p className="font-semibold text-slate-200">Shared routing engine: {impact.plan.decision.action === 'reroute' ? 'reroute recommended' : impact.plan.decision.action === 'no_route' ? 'no route available' : 'keep the original route'}</p>
                      <p className="text-slate-400">{impact.plan.decision.reason}</p>
                      {impact.plan.comparison && <p className="text-slate-400">Distance {impact.plan.comparison.distance_delta_km >= 0 ? '+' : ''}{impact.plan.comparison.distance_delta_km.toFixed(2)} km · incidents avoided: {impact.plan.comparison.incidents_avoided.length}</p>}
                    </div>
                  )}
                  <p className="text-[11px] text-slate-500">Grey dotted = fastest route without live TomTom incidents · cyan = route with all active incidents applied.</p>
                </div>
              )}
              {best && (
                <div className="space-y-2 text-xs">
                  <div className="flex flex-wrap items-center gap-2 text-slate-300">
                    <Badge variant="info">{best.distance_km.toFixed(2)} km · {best.estimated_travel_time_min.toFixed(1)} min</Badge>
                    <Badge variant="neutral">{route!.active_incident_count} incident record(s) applied</Badge>
                    {rerouted && <Badge variant="warning">Route changed after last update</Badge>}
                  </div>
                  <p className="break-all text-slate-500">Nodes: {best.node_sequence.join(' → ')}</p>
                  {prev && rerouted && <p className="break-all text-slate-500">Previously: {prev.node_sequence.join(' → ')}</p>}
                  <div className="max-h-44 space-y-1 overflow-y-auto">
                    {best.segments.map((s, i) => (
                      <button key={i} onClick={() => setForm({ ...form, edge_u: s.from_node, edge_v: s.to_node })}
                        className="flex w-full items-center justify-between rounded border border-navy-700/40 px-3 py-1.5 text-left text-slate-300 hover:border-cyan-500/40">
                        <span className="truncate">{s.road_name ?? `Unnamed ${s.highway} road`} <span className="text-slate-500">({s.from_node} → {s.to_node})</span></span>
                        <span className="shrink-0 text-slate-500">{s.length_m.toFixed(0)} m{s.incident_ids.length ? ' · ⚠ incident' : ''}</span>
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
            <div className="space-y-2">
              <TrafficMap lines={lines} points={points} backgroundLines={roads.lines} height="380px" />
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-400" data-testid="map-legend">
                <span><span className="mr-1 inline-block h-2 w-4 rounded bg-rose-500" />TomTom · matched &amp; applied</span>
                <span><span className="mr-1 inline-block h-2 w-4 rounded bg-red-600" />closure</span>
                <span><span className="mr-1 inline-block h-2 w-4 rounded bg-orange-500" />accident</span>
                <span><span className="mr-1 inline-block h-2 w-4 rounded bg-yellow-500" />construction / lane restriction</span>
                <span><span className="mr-1 inline-block h-2 w-4 rounded border border-dashed border-amber-400" />TomTom · unmatched (shown, not applied)</span>
                <span><span className="mr-1 inline-block h-2 w-4 rounded bg-violet-400" />Manual / simulated</span>
                <span><span className="mr-1 inline-block h-0.5 w-4 bg-slate-500" />{roads.status === 'ready' ? `OSM road network (${roads.info?.is_real_road_geometry ? 'real' : 'NOT real OSM'}${roads.info?.truncated ? ', partial' : ''})` : roads.status === 'loading' ? 'road network loading…' : 'road network layer unavailable'}</span>
              </div>
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}
