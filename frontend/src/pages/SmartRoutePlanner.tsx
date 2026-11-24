import { useState, useEffect, useCallback, useMemo, type ReactNode } from 'react';
import {
  Navigation, Route as RouteIcon, Clock, MapPin, Zap, CheckCircle2, RefreshCw, Info, Wifi, WifiOff, Crosshair, AlertTriangle, Search, Siren,
} from 'lucide-react';
import { PageHeader } from '@/components/ui/PageHeader';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Input, Select } from '@/components/ui/Select';
import { ErrorState, EmptyState, LoadingState } from '@/components/ui/States';
import { ProvenanceBanner, SourceTag, TrafficStateBadge } from '@/components/ui/Provenance';
import { TrafficMap, type MapLine, type MapPoint } from '@/components/map/TrafficMap';
import { api, ApiError } from '@/lib/api';
import { useApi } from '@/lib/useApi';
import { useRoadLayer } from '@/lib/useRoadLayer';
import { geometryToLatLon, IMPACT_LABEL, impactColor } from '@/lib/incidentStatus';
import { cn } from '@/lib/utils';
import type {
  AnalysedIncident, CalculatedRoute, NetworkStatus, PlaceMatch, PlannedRoute, RouteCalculationRequest, RouteCalculationResponse,
  RouteEndpoints, RoutePlanResponse, RoutingAlgorithm, RoutingNode, RoutingNodesResponse,
} from '@/types';

// Legend colours: normal route blue, recommended alternative green, other alternatives grey; incidents use impactColor().
const ORIGINAL_COLOR = '#3b82f6';
const RECOMMENDED_COLOR = '#22c55e';
const ALT_COLOR = '#94a3b8';
const UNMATCHED_COLOR = '#f59e0b';
type Mode = 'names' | 'nodes' | 'map';
type LatLon = { lat: number; lon: number };

const ALGORITHM_OPTIONS: { value: RoutingAlgorithm; label: string }[] = [
  { value: 'dijkstra', label: 'Dijkstra' },
  { value: 'astar', label: 'A*' },
];

const errorMessage = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

/** Prefer the backend's real edge geometry; fall back to the node sequence (latitude/longitude fields). */
function resolveGeometry(route: CalculatedRoute, nodeById: Map<string, RoutingNode>): [number, number][] | null {
  if (route.polyline_latlon && route.polyline_latlon.length > 0) return route.polyline_latlon;
  const positions: [number, number][] = [];
  for (const id of route.node_sequence) {
    const node = nodeById.get(String(id));
    if (!node) return null;
    positions.push([node.latitude, node.longitude]);
  }
  return positions.length > 0 ? positions : null;
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-navy-700/50 bg-navy-800/40 p-3">
      <p className="text-xs text-slate-400">{label}</p>
      <p className="mt-1 text-lg font-bold text-slate-100">{value}</p>
    </div>
  );
}

export function SmartRoutePlanner() {
  const health = useApi(() => api.getHealth(), []);
  const [graph, setGraph] = useState<RoutingNodesResponse | null>(null);
  const [nodesLoading, setNodesLoading] = useState(true);
  const [nodesError, setNodesError] = useState<string | null>(null);

  const [mode, setMode] = useState<Mode>('names');
  const [origin, setOrigin] = useState('');
  const [destination, setDestination] = useState('');
  const [clickOrigin, setClickOrigin] = useState<LatLon | null>(null);
  const [clickDest, setClickDest] = useState<LatLon | null>(null);
  const [originName, setOriginName] = useState('');
  const [destName, setDestName] = useState('');
  const [algorithm, setAlgorithm] = useState<RoutingAlgorithm>('dijkstra');
  const [alternatives, setAlternatives] = useState('3');
  const [simCongestion, setSimCongestion] = useState(false);
  const [departure, setDeparture] = useState('');
  const [rainfall, setRainfall] = useState('0');
  const [useTomTom, setUseTomTom] = useState(false);
  const [useLiveTraffic, setUseLiveTraffic] = useState(false);

  // Legacy /routes/calculate result: only requested when TomTom/provider *flow* options are ticked (kept for the flow-comparison cards).
  const [result, setResult] = useState<RouteCalculationResponse | null>(null);
  const [plan, setPlan] = useState<RoutePlanResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string>('recommended');
  const [places, setPlaces] = useState<{ origin?: PlaceMatch; destination?: PlaceMatch; error?: string } | null>(null);
  const [checking, setChecking] = useState(false);
  const [network, setNetwork] = useState<NetworkStatus | null>(null);
  const roads = useRoadLayer('major');

  const nodeById = useMemo(() => new Map((graph?.nodes ?? []).map((n) => [n.id, n])), [graph]);

  const loadNodes = useCallback(async () => {
    setNodesLoading(true);
    setNodesError(null);
    try {
      const data = await api.getRoutingNodes(2000);
      setGraph(data);
      if (data.nodes.length >= 2) {
        setOrigin((o) => o || data.nodes[0].id);
        setDestination((d) => d || data.nodes[1].id);
      }
    } catch (e) {
      setNodesError(errorMessage(e, 'Could not load routing nodes.'));
    } finally {
      setNodesLoading(false);
    }
  }, []);

  useEffect(() => { void loadNodes(); }, [loadNodes]);
  useEffect(() => { api.getNetworkStatus().then(setNetwork).catch(() => setNetwork(null)); }, []);

  const onMapClick = useCallback((lat: number, lon: number) => {
    if (mode !== 'map') return;
    if (!clickOrigin || (clickOrigin && clickDest)) { setClickOrigin({ lat, lon }); setClickDest(null); }
    else setClickDest({ lat, lon });
  }, [mode, clickOrigin, clickDest]);

  // Exactly ONE endpoint pair is built (backend validator rejects zero or several).
  const endpoints: RouteEndpoints | null =
    mode === 'nodes' ? (origin && destination && origin !== destination ? { origin_node: origin, destination_node: destination } : null)
    : mode === 'map' ? (clickOrigin && clickDest ? { origin_lat: clickOrigin.lat, origin_lon: clickOrigin.lon, destination_lat: clickDest.lat, destination_lon: clickDest.lon } : null)
    : (originName.trim() && destName.trim() ? { origin_name: originName.trim(), destination_name: destName.trim() } : null);

  const checkPlaces = async () => {
    if (mode !== 'names') return;
    setChecking(true); setPlaces(null);
    try {
      const [o, d] = await Promise.all([api.searchLocation(originName.trim()), api.searchLocation(destName.trim())]);
      setPlaces({ origin: o, destination: d });
    } catch (e) { setPlaces({ error: errorMessage(e, 'Location search failed.') }); }
    finally { setChecking(false); }
  };

  const handlePlan = async () => {
    if (!endpoints) return;
    const base = {
      ...endpoints, algorithm, alternatives: Number(alternatives),
      rainfall_mm: Math.max(0, Number(rainfall) || 0), use_simulated_congestion: simCongestion, departure_time: simCongestion && departure ? departure : null,
    };
    const flowRequested = useTomTom || useLiveTraffic;
    setLoading(true); setError(null); setResult(null); setPlan(null); setSelectedId('recommended');
    try {
      // Incident-aware planning always runs on the shared backend engine; the legacy call only adds the flow comparison cards.
      const [planned, legacy] = await Promise.all([
        api.planRoute({ ...base, refresh_incidents: true }),
        flowRequested ? api.calculateRoutes({ ...base, use_tomtom_flow: useTomTom && !useLiveTraffic, use_live_traffic: useLiveTraffic } as RouteCalculationRequest).catch(() => null) : Promise.resolve(null),
      ]);
      setPlan(planned);
      setResult(legacy);
      setSelectedId(planned.decision.action === 'reroute' ? 'recommended' : 'original');
    } catch (e) {
      setError(errorMessage(e, 'Route calculation failed.'));
    } finally {
      setLoading(false);
    }
  };

  const nodeOptions = (graph?.nodes ?? []).map((n) => ({ value: n.id, label: n.label_source && n.label_source !== 'coordinates' ? n.name : n.name && n.name !== n.id ? `${n.name} (${n.id})` : n.id }));

  const rec = plan?.recommended_route ?? null;
  const rerouted = plan?.decision.action === 'reroute';
  const routesById = new Map<string, PlannedRoute>();
  if (plan) {
    routesById.set('original', plan.original_route);
    if (rec) routesById.set('recommended', rec);
    plan.alternative_routes.forEach((r) => routesById.set(r.route_id, r));
  }
  const selectedRoute = routesById.get(selectedId) ?? rec ?? plan?.original_route ?? null;
  const incidentLabel = (id: string) => plan?.incident_analysis.find((i) => i.id === id)?.location_label ?? id;

  const lines: MapLine[] = [];
  const points: MapPoint[] = [];
  const addRoute = (id: string, route: PlannedRoute | undefined, color: string, title: string, dashed = false) => {
    if (!route) return;
    const positions = resolveGeometry(route, nodeById);
    if (!positions) return;
    const on = id === selectedId;
    lines.push({
      positions, color, weight: on ? 7 : 4, opacity: on ? 0.95 : 0.55, dashArray: dashed ? '8 8' : undefined,
      label: <><strong>{title}</strong><br />{route.distance_km} km · {route.estimated_travel_time_min} min{route.incident_count ? ` · ${route.incident_count} incident(s)` : ''}</>,
    });
  };
  if (plan) {
    plan.alternative_routes.forEach((r, i) => addRoute(r.route_id, r, ALT_COLOR, `Alternative ${i + 1}`, true));
    if (rerouted) addRoute('original', plan.original_route, ORIGINAL_COLOR, 'Original route (normal)', true);
    addRoute(rerouted ? 'recommended' : 'original', rerouted ? rec ?? undefined : plan.original_route, rerouted ? RECOMMENDED_COLOR : ORIGINAL_COLOR, rerouted ? 'Recommended alternative' : 'Route (original kept)');
    plan.incident_analysis.forEach((inc: AnalysedIncident) => {
      const pos = geometryToLatLon(inc.geometry);
      const color = inc.matched ? impactColor(inc.impact_class) : UNMATCHED_COLOR;
      const popup = (
        <>
          <strong>{IMPACT_LABEL[inc.impact_class] ?? inc.kind}</strong> · {inc.source === 'tomtom' ? 'TomTom live' : inc.source === 'manual' ? 'Manual (test)' : 'Simulated (test)'}<br />
          {inc.description || inc.category}<br />{inc.location_label}<br />
          {inc.matched ? (inc.routing_applied ? 'Matched to an OSM road · applied to routing' : 'Matched · not applied to routing') : `Not matched to a road${inc.unmatched_reason ? `: ${inc.unmatched_reason}` : ''}`}<br />
          {inc.affects_original_route ? 'On the original route' : 'Not on the original route'}
        </>
      );
      if (pos.length >= 2) lines.push({ positions: pos, color, weight: 9, opacity: 0.9, dashArray: inc.matched ? undefined : '6 8', label: popup });
      if (pos.length >= 1) {
        const mid = pos[Math.floor(pos.length / 2)];
        points.push({ id: `inc-${inc.id}`, lat: mid[0], lon: mid[1], color, radius: inc.affects_original_route ? 10 : 7, popup });
      } else if (inc.latitude != null && inc.longitude != null) {
        points.push({ id: `inc-${inc.id}`, lat: inc.latitude, lon: inc.longitude, color, radius: 7, popup });
      }
    });
    points.push({ id: 'o', lat: plan.origin.latitude, lon: plan.origin.longitude, color: '#2dd4bf', radius: 9, popup: `Origin: ${plan.origin.road_label.label}` });
    points.push({ id: 'd', lat: plan.destination.latitude, lon: plan.destination.longitude, color: '#f43f5e', radius: 9, popup: `Destination: ${plan.destination.road_label.label}` });
  } else if (mode === 'map') {
    if (clickOrigin) points.push({ id: 'o', lat: clickOrigin.lat, lon: clickOrigin.lon, color: '#2dd4bf', radius: 9, popup: 'Origin (clicked)' });
    if (clickDest) points.push({ id: 'd', lat: clickDest.lat, lon: clickDest.lon, color: '#f43f5e', radius: 9, popup: 'Destination (clicked)' });
  } else if (mode === 'nodes') {
    const o = nodeById.get(origin); const d = nodeById.get(destination);
    if (o) points.push({ id: 'o', lat: o.latitude, lon: o.longitude, color: '#2dd4bf', radius: 9, popup: `Origin ${o.name}` });
    if (d) points.push({ id: 'd', lat: d.latitude, lon: d.longitude, color: '#f43f5e', radius: 9, popup: `Destination ${d.name}` });
  } else if (mode === 'names' && places?.origin && places.destination) {
    points.push({ id: 'o', lat: places.origin.latitude, lon: places.origin.longitude, color: '#2dd4bf', radius: 9, popup: `Origin: ${places.origin.road_label.label}` });
    points.push({ id: 'd', lat: places.destination.latitude, lon: places.destination.longitude, color: '#f43f5e', radius: 9, popup: `Destination: ${places.destination.road_label.label}` });
  }

  const isOsm = network ? network.is_real_osm : graph?.is_real_road_geometry ?? false;
  const healthState = health.loading ? 'checking' : health.error ? 'offline' : 'online';
  const formatKm = (v?: number | null) => (v == null ? 'Not reported' : `${v.toFixed(2)} km`);
  const formatMin = (v?: number | null) => (v == null ? 'Not reported' : `${v.toFixed(1)} min`);
  const signed = (v: number, unit: string, digits = 1) => `${v >= 0 ? '+' : ''}${v.toFixed(digits)} ${unit}`;

  const unavailable = network?.state === 'unavailable';
  const pr = plan?.incident_data.provider;
  const rows = plan?.incident_analysis ?? [];
  const routeCard = (id: string, route: PlannedRoute | undefined | null, title: string, color: string, extra?: ReactNode) => route && (
    <button key={id} onClick={() => setSelectedId(id)} data-testid={`route-card-${id}`}
      className={cn('w-full rounded-xl border p-4 text-left transition-all', selectedId === id ? 'border-cyan-500/50 bg-cyan-500/5 shadow-glow' : 'border-navy-700/60 bg-navy-900/60 hover:border-navy-600/60')}>
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="flex items-center gap-2 text-sm font-semibold text-slate-100"><span className="inline-block h-2 w-5 rounded" style={{ background: color }} />{title}</p>
          <div className="mt-1 flex flex-wrap items-center gap-3 text-xs text-slate-400">
            <span className="flex items-center gap-1"><MapPin className="h-3 w-3" /> {formatKm(route.distance_km)}</span>
            <span className="flex items-center gap-1"><Clock className="h-3 w-3" /> {formatMin(route.estimated_travel_time_min)}</span>
            <span>{route.incident_count} incident(s) on route</span>
            {route.blocked_by_closure && <span className="text-rose-300">blocked by a confirmed closure</span>}
          </div>
          {route.via_roads.length > 0 && <p className="mt-1 text-xs text-slate-500">via {route.via_roads.join(', ')}</p>}
          {route.incident_ids.length > 0 && <p className="mt-1 text-xs text-amber-300">⚠ {route.incident_ids.map(incidentLabel).join('; ')}</p>}
          {extra}
        </div>
      </div>
    </button>
  );

  return (
    <div className="space-y-6">
      <PageHeader
        title="Smart Route Planner" description="Dijkstra / A* routes on the real OpenStreetMap network, re-planned around live TomTom incidents"
        icon={<Navigation className="h-6 w-6" />}
        action={
          <div className={cn('flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-medium',
            healthState === 'online' && 'border-teal-500/30 bg-teal-500/10 text-teal-300',
            healthState === 'offline' && 'border-rose-500/30 bg-rose-500/10 text-rose-300',
            healthState === 'checking' && 'border-navy-600/50 bg-navy-800/60 text-slate-400')}>
            {healthState === 'offline' ? <WifiOff className="h-3.5 w-3.5" /> : <Wifi className="h-3.5 w-3.5" />}
            {healthState === 'online' ? 'Backend connected' : healthState === 'offline' ? 'Backend unreachable' : 'Checking backend'}
          </div>
        }
      />

      {unavailable && network?.error ? (
        <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-xs text-rose-200" role="alert" data-testid="network-unavailable">
          <p className="font-semibold">The OpenStreetMap road network is not available — routing is disabled (no synthetic network is substituted).</p>
          <p className="mt-1">{network.error.reason}</p><p className="mt-1 text-rose-300/80">{network.error.hint}</p>
        </div>
      ) : (
        <ProvenanceBanner tone={isOsm ? 'info' : 'warn'}>
          {network && !isOsm ? (
            <><strong>This graph is not OpenStreetMap data ({network.source}).</strong> Do not treat routes as real roads. </>
          ) : network ? (<>Road network: real <strong>OpenStreetMap</strong> data ({network.source}; {network.nodes.toLocaleString()} nodes, {network.edges.toLocaleString()} road edges{network.named_edge_share != null ? `; ${(network.named_edge_share * 100).toFixed(0)}% of edges carry an OSM name` : ''}). </>) : null}
          Travel times are model estimates from road attributes{simCongestion ? ' plus SIMULATED time-of-day congestion' : ''} and provider-reported incident delays — <strong>not live per-road speeds</strong> unless TomTom flow is applied.
        </ProvenanceBanner>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <Card title="Plan a Route" subtitle="One endpoint pair per request" icon={<RouteIcon className="h-5 w-5" />}>
          <div className="space-y-4">
            {nodesLoading && <LoadingState label="Loading the OpenStreetMap road graph…" />}
            {nodesError && !nodesLoading && (
              <div className="space-y-3"><ErrorState message={nodesError} />
                <Button variant="secondary" onClick={loadNodes} icon={<RefreshCw className="h-4 w-4" />} className="w-full">Retry</Button></div>
            )}
            {!nodesLoading && !nodesError && (graph?.nodes.length ?? 0) < 2 && <EmptyState message="The backend returned fewer than two routable nodes." />}

            {!nodesLoading && !nodesError && graph && graph.nodes.length >= 2 && (
              <>
                <Select label="Endpoints by" id="mode-select" value={mode} onChange={(v) => setMode(v as Mode)}
                  options={[{ value: 'names', label: 'Place / road names (searched on OpenStreetMap)' }, { value: 'nodes', label: 'Road junctions (named from OSM)' }, { value: 'map', label: 'Map clicks (coordinates)' }]} />

                {mode === 'nodes' && (<>
                  <Select label="Origin node" value={origin} onChange={setOrigin} options={nodeOptions} id="origin-select" />
                  <Select label="Destination node" value={destination} onChange={setDestination} options={nodeOptions} id="dest-select" />
                  {origin !== '' && origin === destination && <p className="text-xs text-amber-300">Origin and destination must differ.</p>}
                  {graph.nodes.length >= 2000 && <p className="text-[11px] text-slate-500">Showing the first {graph.nodes.length} named junctions. Use place names or map clicks to route anywhere in the city.</p>}
                </>)}
                {mode === 'map' && (
                  <div className="space-y-2 text-xs text-slate-300">
                    <p className="flex items-center gap-2 text-slate-400"><Crosshair className="h-4 w-4" /> Click the map: 1st click = origin, 2nd = destination, 3rd restarts.</p>
                    <p>Origin: {clickOrigin ? `${clickOrigin.lat.toFixed(5)}, ${clickOrigin.lon.toFixed(5)}` : '—'}</p>
                    <p>Destination: {clickDest ? `${clickDest.lat.toFixed(5)}, ${clickDest.lon.toFixed(5)}` : '—'}</p>
                  </div>
                )}
                {mode === 'names' && (<>
                  <Input id="origin-name" label="Origin place" value={originName} onChange={setOriginName} placeholder="e.g. Jharapada, Bhubaneswar" />
                  <Input id="dest-name" label="Destination place" value={destName} onChange={setDestName} placeholder="e.g. Baragada, Bhubaneswar" />
                  <Button variant="secondary" size="sm" onClick={checkPlaces} disabled={checking || !endpoints} icon={<Search className="h-3.5 w-3.5" />} className="w-full">{checking ? 'Searching…' : 'Check places'}</Button>
                  {places?.error && <p className="text-xs text-rose-300" role="alert">{places.error}</p>}
                  {[places?.origin, places?.destination].map((p, i) => p && (
                    <div key={i} className="rounded border border-navy-700/50 bg-navy-800/40 p-2 text-[11px] text-slate-300">
                      <p className="font-semibold text-slate-200">{i === 0 ? 'Origin' : 'Destination'}: {p.geocode?.display_name ?? p.input}</p>
                      <p>Nearest road node: {p.road_label.label} · {Math.round(p.snap_distance_m)} m away</p>
                      <p className="text-slate-500">Found via {p.geocode?.source === 'road_graph_name' ? 'the loaded OSM road names' : 'OpenStreetMap Nominatim'}{p.geocode && p.geocode.query_used !== p.geocode.query.toLowerCase() ? ` (spelling tried: ${p.geocode.query_used})` : ''}</p>
                    </div>
                  ))}
                </>)}

                <div className="grid grid-cols-2 gap-3">
                  <Select label="Algorithm" value={algorithm} onChange={(v) => setAlgorithm(v as RoutingAlgorithm)} options={ALGORITHM_OPTIONS} id="algorithm-select" />
                  <Select label="Alternatives" value={alternatives} onChange={setAlternatives} id="alt-select"
                    options={[1, 2, 3, 4, 5].map((n) => ({ value: String(n), label: String(n) }))} />
                </div>
                <Input id="rain" label="Rainfall (mm) for edge speeds" type="number" value={rainfall} onChange={setRainfall} />
                <label className="flex items-center gap-2 text-xs text-slate-300">
                  <input type="checkbox" checked={simCongestion} onChange={(e) => setSimCongestion(e.target.checked)} /> Apply SIMULATED time-of-day congestion
                </label>
                {simCongestion && <Input id="dep" label="Departure (local IST, optional)" type="datetime-local" value={departure} onChange={setDeparture} />}
                <label className={cn('flex items-center gap-2 text-xs', health.data?.tomtom_enabled ? 'text-slate-300' : 'text-slate-600')}>
                  <input type="checkbox" disabled={!health.data?.tomtom_enabled} checked={useTomTom} onChange={(e) => setUseTomTom(e.target.checked)} />
                  Apply TomTom measured flow {health.data?.tomtom_enabled ? '' : '(no API key configured)'}
                </label>

                {(() => {
                  const tp = health.data?.data_sources.traffic_provider;
                  const ok = !!tp?.configured && tp.supports.flow;
                  return (
                    <div>
                      <label className={cn('flex items-center gap-2 text-xs', ok ? 'text-slate-300' : 'text-slate-600')}>
                        <input type="checkbox" disabled={!ok} checked={useLiveTraffic} onChange={(e) => setUseLiveTraffic(e.target.checked)} />
                        Traffic-aware routing with {tp ? tp.provider.toUpperCase() : 'provider'} measured flow {ok ? '' : '(no API key configured)'}
                      </label>
                      {ok && <p className="mt-1 text-[11px] text-slate-500">Uses live/cached provider speeds on matched road edges only; the OSM Dijkstra/A* result is the fallback if traffic is unavailable.</p>}
                    </div>
                  );
                })()}

                <Button onClick={handlePlan} disabled={loading || !endpoints} icon={<Navigation className="h-4 w-4" />} className="w-full">
                  {loading ? 'Checking incidents & calculating…' : 'Find incident-aware route'}
                </Button>
              </>
            )}
          </div>
        </Card>

        <div className="space-y-4 lg:col-span-2">
          <Card title="Map" subtitle={mode === 'map' ? 'Click to set origin and destination' : 'OSM road network, routes and live incidents'} icon={<MapPin className="h-5 w-5" />}
            action={network ? <SourceTag kind={isOsm ? 'osm' : 'osm-fallback'} /> : undefined}>
            <TrafficMap lines={lines} points={points} backgroundLines={roads.lines} height="400px" onMapClick={mode === 'map' ? onMapClick : undefined} autoFit={!!plan || mode === 'nodes' || (mode === 'names' && !!places?.origin)} />
            <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-400" data-testid="map-legend">
              <span><span className="mr-1 inline-block h-1.5 w-5 rounded" style={{ background: ORIGINAL_COLOR }} />Normal route</span>
              <span><span className="mr-1 inline-block h-1.5 w-5 rounded" style={{ background: RECOMMENDED_COLOR }} />Recommended alternative</span>
              <span><span className="mr-1 inline-block h-1.5 w-5 rounded border border-dashed border-slate-400" />Other alternatives</span>
              <span><span className="mr-1 inline-block h-1.5 w-5 rounded" style={{ background: impactColor('confirmed_closure') }} />Road closure</span>
              <span><span className="mr-1 inline-block h-1.5 w-5 rounded" style={{ background: impactColor('major_accident') }} />Accident</span>
              <span><span className="mr-1 inline-block h-1.5 w-5 rounded" style={{ background: impactColor('construction') }} />Construction / lane restriction</span>
              <span><span className="mr-1 inline-block h-1.5 w-5 rounded border border-dashed" style={{ borderColor: UNMATCHED_COLOR }} />Unmatched incident (shown, not applied)</span>
              <span><span className="mr-1 inline-block h-0.5 w-5 bg-slate-500" />{roads.status === 'ready' ? `OSM road network${roads.info?.truncated ? ' (partial)' : ''}` : roads.status === 'loading' ? 'road network loading…' : 'road network layer unavailable'}</span>
            </div>
          </Card>

          {!plan && !loading && !error && <Card title="Route Results"><EmptyState message={'Enter an origin and destination, then click "Find incident-aware route".'} /></Card>}
          {loading && <Card title="Route Results"><LoadingState label="Fetching incidents and calculating routes…" /></Card>}
          {error && !loading && (
            <Card title="Route Results"><div className="space-y-3"><ErrorState message={error} />
              <Button variant="secondary" onClick={handlePlan} icon={<RefreshCw className="h-4 w-4" />} className="w-full">Retry</Button></div></Card>
          )}

          {plan && (<>
            <div className={cn('rounded-xl border p-4 text-sm', plan.decision.action === 'reroute' ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-100' : plan.decision.action === 'no_route' ? 'border-rose-500/40 bg-rose-500/10 text-rose-100' : 'border-cyan-500/30 bg-cyan-500/5 text-slate-100')} data-testid="decision-banner">
              <p className="flex items-center gap-2 font-semibold">
                {plan.decision.action === 'reroute' ? <><Siren className="h-4 w-4" /> Alternative route recommended</> : plan.decision.action === 'no_route' ? <><AlertTriangle className="h-4 w-4" /> No route available</> : <><CheckCircle2 className="h-4 w-4" /> Original route kept</>}
              </p>
              <p className="mt-1 text-xs opacity-90">{plan.decision.reason}</p>
              <p className="mt-2 text-[11px] opacity-75" data-testid="incident-data-note">{plan.incident_data.note}</p>
              {plan.decision.action === 'keep_original' && !plan.incident_data.incident_free_claim_supported && rows.every((r) => !r.affects_original_route) && (
                <p className="mt-1 text-[11px] text-amber-200">Not shown as “incident-free”: the available incident data does not support that conclusion.</p>
              )}
            </div>

            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              {routeCard('original', plan.original_route, rerouted ? 'Original route (normal)' : 'Route (original kept)', ORIGINAL_COLOR,
                <p className="mt-1 text-xs text-slate-500">Free-flow {formatMin(plan.original_route.free_flow_time_min)} · with incident delays {plan.original_route.time_with_incidents_min == null ? 'route is closed' : formatMin(plan.original_route.time_with_incidents_min)}</p>)}
              {rerouted && routeCard('recommended', rec, 'Recommended alternative', RECOMMENDED_COLOR,
                plan.comparison && <p className="mt-1 text-xs text-emerald-300">
                  {signed(plan.comparison.distance_delta_km, 'km', 2)} distance
                  {plan.comparison.time_delta_vs_original_with_incidents_min != null ? ` · ${signed(plan.comparison.time_delta_vs_original_with_incidents_min, 'min')} vs original with incidents` : ' · original is blocked'}
                  {` · ${signed(plan.comparison.time_delta_vs_original_free_flow_min, 'min')} vs original free-flow`}
                </p>)}
            </div>

            {plan.alternative_routes.length > 0 && (
              <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                {plan.alternative_routes.map((r, i) => routeCard(r.route_id, r, `Alternative ${i + 1}`, ALT_COLOR))}
              </div>
            )}

            {plan.comparison && rerouted && (
              <Card title="Why this alternative" subtitle="Comparison with the original route" icon={<Zap className="h-5 w-5" />}>
                <div className="grid grid-cols-2 gap-3 text-center sm:grid-cols-4">
                  <Stat label="Additional distance" value={signed(plan.comparison.distance_delta_km, 'km', 2)} />
                  <Stat label="Time vs original (with incidents)" value={plan.comparison.time_delta_vs_original_with_incidents_min == null ? 'original blocked' : signed(plan.comparison.time_delta_vs_original_with_incidents_min, 'min')} />
                  <Stat label="Time vs original (free-flow)" value={signed(plan.comparison.time_delta_vs_original_free_flow_min, 'min')} />
                  <Stat label="Incidents avoided" value={String(plan.comparison.incidents_avoided.length)} />
                </div>
                {plan.comparison.incidents_avoided.length > 0 && <p className="mt-3 text-xs text-slate-300">Avoided: {plan.comparison.incidents_avoided.map(incidentLabel).join('; ')}</p>}
                {plan.comparison.incidents_still_on_recommended.length > 0 && <p className="mt-1 text-xs text-amber-300">Still on the alternative: {plan.comparison.incidents_still_on_recommended.map(incidentLabel).join('; ')}</p>}
                <p className="mt-2 text-[11px] text-slate-500">Reroute rule: closed route, or a saving of at least {plan.decision.thresholds.min_saving_s} s and {plan.decision.thresholds.min_saving_pct}% on the incident-priced route.</p>
              </Card>
            )}

            <Card title="Incident analysis" subtitle={`${rows.length} active incident(s) considered · ${pr ? `TomTom: ${pr.state}` : ''}`} icon={<Siren className="h-5 w-5" />}>
              {rows.length === 0 ? (
                <p className="text-sm text-slate-400" data-testid="no-incidents">{plan.incident_data.live_data_supported ? 'TomTom reports no active incidents for this area.' : 'No incidents are available (see the data status above).'}</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[720px] text-left text-xs text-slate-300" data-testid="incident-table">
                    <thead className="text-slate-500"><tr>
                      <th className="py-1 pr-3">Type</th><th className="pr-3">Severity</th><th className="pr-3">Road / location</th><th className="pr-3">Length · delay</th>
                      <th className="pr-3">Expected end</th><th className="pr-3">Road match</th><th className="pr-3">On original</th><th>On recommended</th></tr></thead>
                    <tbody>
                      {rows.map((i) => (
                        <tr key={i.id} className="border-t border-navy-700/40 align-top" data-testid="incident-row">
                          <td className="py-2 pr-3"><span className="mr-1 inline-block h-2 w-2 rounded-full" style={{ background: i.matched ? impactColor(i.impact_class) : UNMATCHED_COLOR }} />{IMPACT_LABEL[i.impact_class] ?? i.kind}<br /><span className="text-slate-500">{i.source === 'tomtom' ? 'TomTom live' : i.source === 'manual' ? 'Manual (test)' : 'Simulated (test)'}</span></td>
                          <td className="pr-3">{i.magnitude_label && i.magnitude_label !== 'undefined' ? i.magnitude_label : i.severity_source === 'default_for_kind' ? 'not provided' : '—'} ({i.severity.toFixed(2)})</td>
                          <td className="pr-3">{i.location_label}{i.road_name_source === 'nearest_named_road' && <span className="text-slate-500"> (nearest named road)</span>}</td>
                          <td className="pr-3">{i.provider_length_m != null ? `${(i.provider_length_m / 1000).toFixed(2)} km` : i.matched_length_m ? `${(i.matched_length_m / 1000).toFixed(2)} km matched` : 'length n/a'}<br />{i.provider_delay_s != null && i.provider_delay_s > 0 ? `${Math.round(i.provider_delay_s / 60)} min delay` : 'delay not reported'}</td>
                          <td className="pr-3">{i.end_time ? new Date(i.end_time).toLocaleString() : 'not provided'}</td>
                          <td className="pr-3">{i.matched ? (i.routing_applied ? 'Matched · applied' : 'Matched · not applied') : <span className="text-amber-300">Unmatched{i.unmatched_reason ? `: ${i.unmatched_reason}` : ''}</span>}</td>
                          <td className="pr-3">{i.affects_original_route ? `Yes (${((i.affected_length_on_original_m ?? 0) / 1000).toFixed(2)} km)` : 'No'}</td>
                          <td>{i.affects_recommended_route ? 'Yes' : 'No'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <p className="mt-2 text-[11px] text-slate-500">Blocked lanes are not reported by the TomTom incident feed and are never assumed. Closures are only applied when TomTom marks the road closed and the incident is matched to an OSM road.</p>
                </div>
              )}
            </Card>

            {selectedRoute && (
              <Card title="Selected route details" subtitle={`${selectedRoute.algorithm} · ${selectedRoute.steps.length} road steps`} icon={<MapPin className="h-5 w-5" />}>
                {!resolveGeometry(selectedRoute, nodeById) && <ErrorState message="This route's geometry could not be drawn." />}
                <div className="grid grid-cols-2 gap-3 text-center sm:grid-cols-4">
                  <Stat label="Distance" value={formatKm(selectedRoute.distance_km)} />
                  <Stat label="Est. time (incl. incident delay)" value={formatMin(selectedRoute.estimated_travel_time_min)} />
                  <Stat label="Free-flow" value={formatMin(selectedRoute.free_flow_estimate_min)} />
                  <Stat label="Nodes" value={String(selectedRoute.node_sequence.length)} />
                </div>
                <div className="mt-4 max-h-48 space-y-1 overflow-y-auto text-xs">
                  {selectedRoute.steps.map((st, i) => (
                    <div key={i} className="flex items-center justify-between rounded border border-navy-700/40 px-3 py-1.5 text-slate-300">
                      <span className="truncate">{st.road}</span>
                      <span className="shrink-0 text-slate-400">{(st.distance_m / 1000).toFixed(2)} km · {st.time_min.toFixed(1)} min</span>
                    </div>
                  ))}
                </div>
              </Card>
            )}

            <Card title="Locations & data sources" icon={<Info className="h-5 w-5" />}>
              <div className="grid grid-cols-1 gap-3 text-xs sm:grid-cols-2">
                {([['Origin', plan.origin], ['Destination', plan.destination]] as const).map(([k, p]) => (
                  <div key={k} className="rounded-lg border border-navy-700/50 bg-navy-800/40 p-3">
                    <p className="text-slate-400">{k}: “{p.input}”</p>
                    <p className="mt-1 font-semibold text-slate-100">{p.road_label.label}</p>
                    <p className="text-slate-500">{Math.round(p.snap_distance_m)} m to the nearest road node · {p.geocode ? `found via ${p.geocode.source === 'road_graph_name' ? 'OSM road names' : 'Nominatim'}` : 'chosen directly'} · label from {p.road_label.label_source.replace(/_/g, ' ')}</p>
                  </div>
                ))}
              </div>
              <div className="mt-3 grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
                {Object.entries(plan.data_sources).map(([k, v]) => (
                  <div key={k} className="rounded-lg border border-navy-700/50 bg-navy-800/40 p-3"><p className="text-slate-400">{k.replace(/_/g, ' ')}</p><p className="mt-1 break-words font-semibold text-slate-100">{v}</p></div>
                ))}
              </div>
              <p className="mt-3 text-xs text-slate-500">{plan.notice}</p>
            </Card>
            {result?.traffic_flow?.provider && (() => {
              const f = result.traffic_flow!;
              const applied = f.status === 'applied';
              const best = result.routes[0];
              const baseTime = applied ? f.baseline_best_time_min : best?.estimated_travel_time_min;
              const changed = applied && f.baseline_best_nodes && best ? JSON.stringify(f.baseline_best_nodes) !== JSON.stringify(best.node_sequence) : false;
              const eta = result.provider_eta;
              const P = String(f.provider).toUpperCase();
              const baseBasis = result.traffic_cost_mode.startsWith('simulated') ? 'OSM route with SIMULATED congestion' : 'OSM route, free-flow road attributes';
              return (
                <Card title="Baseline vs traffic-aware" subtitle={`${P} measured flow on the OSM graph`} icon={<Zap className="h-5 w-5" />}
                  action={f.traffic_data_state ? <TrafficStateBadge state={f.traffic_data_state} provider={String(f.provider)} ageSeconds={f.age_seconds as number | null | undefined} /> : undefined}>
                  {applied ? (
                    <div className="grid grid-cols-2 gap-3 text-center sm:grid-cols-4">
                      <Stat label={`Baseline (${baseBasis})`} value={baseTime != null ? formatMin(baseTime) : 'n/a'} />
                      <Stat label={`Traffic-aware (${f.is_live ? 'live' : String(f.traffic_data_state)})`} value={formatMin(best.estimated_travel_time_min)} />
                      <Stat label="Difference" value={baseTime != null ? `${(best.estimated_travel_time_min - baseTime >= 0 ? '+' : '')}${(best.estimated_travel_time_min - baseTime).toFixed(1)} min` : 'n/a'} />
                      <Stat label="Best route changed" value={changed ? 'Yes' : 'No'} />
                    </div>
                  ) : (
                    <ErrorState message={`Not traffic-aware: provider flow status is "${f.status}". ${f.note ?? ''} The routes shown are the unchanged OSM Dijkstra/A* result.`} />
                  )}
                  {applied && (
                    <p className="mt-3 text-xs text-slate-400">
                      {f.edges_matched} directed road edges priced from {P} speeds · {((f.coverage_best_route ?? 0) * 100).toFixed(0)}% of the selected route's length is measured; all other edges use modelled costs.
                      {!f.is_live && ' This data is not live (cached or stale), so the result is labelled accordingly.'}
                    </p>
                  )}
                  {eta && (
                    <div className="mt-3 rounded-lg border border-navy-700/50 bg-navy-800/40 p-3 text-xs text-slate-300">
                      <div className="flex items-center justify-between">
                        <span className="font-semibold text-slate-200">{P} reference ETA (provider road network)</span>
                        <TrafficStateBadge state={eta.data_state} provider={eta.provider} ageSeconds={eta.age_seconds} />
                      </div>
                      {eta.duration_min != null && eta.free_flow_duration_min != null
                        ? <p className="mt-1">With traffic {formatMin(eta.duration_min)} vs free-flow {formatMin(eta.free_flow_duration_min)} (delay {eta.traffic_delay_min?.toFixed(1)} min){eta.length_km != null ? ` · ${eta.length_km.toFixed(1)} km` : ''}. Shown for comparison only; it is not the OSM route time.</p>
                        : <p className="mt-1 text-slate-400">{eta.message ?? 'Provider ETA unavailable.'}</p>}
                    </div>
                  )}
                </Card>
              );
            })()}

            {result && <Card title="Routing details (flow comparison request)" icon={<Info className="h-5 w-5" />}>
              <div className="grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
                {([['Algorithm', ALGORITHM_OPTIONS.find((o) => o.value === result.algorithm)?.label ?? result.algorithm], ['Graph source', result.graph_source],
                  ['Routes returned', String(result.route_count)], ['Active incidents applied', String(result.active_incident_count)],
                  ['Traffic cost mode', result.traffic_cost_mode], ['Departure', `${result.departure_time_source}`],
                  [`Traffic flow (${String(result.traffic_flow?.provider ?? 'tomtom').toUpperCase()})`, result.traffic_flow?.status ?? 'not requested'],
                  ['Live traffic', result.traffic_flow?.status === 'applied' ? (result.traffic_flow.is_live ? `Partly (${String(result.traffic_flow.provider ?? 'tomtom').toUpperCase()} live, matched edges only)` : `No - ${String(result.traffic_flow.traffic_data_state ?? 'non-live')} provider data applied`) : 'No']] as const).map(([k, v]) => (
                  <div key={k} className="rounded-lg border border-navy-700/50 bg-navy-800/40 p-3"><p className="text-slate-400">{k}</p><p className="mt-1 break-words font-semibold text-slate-100">{v}</p></div>
                ))}
              </div>
              <p className="mt-3 text-xs text-slate-500">{result.notice}</p>
            </Card>}
          </>)}
        </div>
      </div>
    </div>
  );
}
