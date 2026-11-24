import { useMemo, useState } from 'react';
import { Map as MapIcon, MapPin, Filter, Radio } from 'lucide-react';
import { PageHeader } from '@/components/ui/PageHeader';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Select } from '@/components/ui/Select';
import { LoadingState, ErrorState, EmptyState } from '@/components/ui/States';
import { ProvenanceBanner, SourceTag, SyntheticBanner, TrafficStateBadge } from '@/components/ui/Provenance';
import { TrafficMap, type MapLine, type MapPoint } from '@/components/map/TrafficMap';
import { api, TRAFFIC_TILE_URL } from '@/lib/api';
import { useApi } from '@/lib/useApi';
import { getAnchor } from '@/lib/locations';
import { isPrediction } from '@/lib/predictions';
import { labelToSeverity, severityColor } from '@/lib/utils';
import type { CongestionLabel, TrafficLevel } from '@/types';

type Layer = 'observed' | 'predicted';

// Colours for provider flow segments (levels are app-defined buckets of the provider's 0-10 jam factor).
const FLOW_COLORS: Record<TrafficLevel, string> = { free: '#22c55e', slow: '#eab308', heavy: '#f97316', severe: '#ef4444', closed: '#a855f7' };
const FLOW_RANK: Record<TrafficLevel, number> = { closed: 0, severe: 1, heavy: 2, slow: 3, free: 4 };
const MAX_FLOW_LINES = 1500; // keep the SVG map responsive; worst congestion is drawn first

export function TrafficMapPage() {
  const current = useApi(() => api.getCurrentTraffic(), []);
  const preds = useApi(() => api.getAllPredictions(), []);
  const incidents = useApi(() => api.getIncidents(), []);
  const nodes = useApi(() => api.getRoutingNodes(2000), []);
  const flow = useApi(() => api.getTrafficFlow(), []);
  const provider = useApi(() => api.getTrafficProviderStatus(), []);

  const [layer, setLayer] = useState<Layer>('predicted');
  const [filter, setFilter] = useState('all');
  const [showIncidents, setShowIncidents] = useState(true);
  const [showFlow, setShowFlow] = useState(true);
  const [showTiles, setShowTiles] = useState(false);

  const predByLoc = useMemo(
    () => new Map((preds.data?.predictions ?? []).filter(isPrediction).map((p) => [p.location, p])),
    [preds.data],
  );

  const items = useMemo(() => (current.data?.locations ?? []).map((r) => {
    const predicted: CongestionLabel | undefined = predByLoc.get(r.location)?.predicted_congestion;
    const label: CongestionLabel = layer === 'predicted' ? (predicted ?? r.congestion_label) : r.congestion_label;
    return { row: r, label, predicted, anchor: getAnchor(r.location) };
  }), [current.data, predByLoc, layer]);

  const shown = items.filter((i) => filter === 'all' || i.label === filter);
  const counts = { Low: 0, Medium: 0, High: 0 } as Record<CongestionLabel, number>;
  items.forEach((i) => { counts[i.label]++; });

  const points: MapPoint[] = shown.flatMap(({ row, label, predicted, anchor }) => anchor ? [{
    id: row.location, lat: anchor.lat, lon: anchor.lon,
    color: severityColor(labelToSeverity(label)), radius: 8 + Math.min(10, row.vehicle_count / 100),
    popup: (<><strong>{row.location}</strong><br />Recorded label: {row.congestion_label}<br />
      Predicted next 30 min: {predicted ?? 'n/a'}<br />Speed: {row.avg_speed} km/h · {row.vehicle_count} vehicles<br />
      <em>{anchor.area}</em><br /><em>Synthetic data, approximate anchor</em></>),
  }] : []);

  // Incidents are graph-edge based; draw them only when both endpoint nodes are known to /routing/nodes.
  const nodeById = useMemo(() => new Map((nodes.data?.nodes ?? []).map((n) => [n.id, n])), [nodes.data]);
  const incidentLines: MapLine[] = [];
  let unplaced = 0;
  if (showIncidents) {
    (incidents.data?.incidents ?? []).forEach((inc) => {
      const a = nodeById.get(inc.edge_u); const b = nodeById.get(inc.edge_v);
      if (!a || !b) { unplaced++; return; }
      incidentLines.push({
        positions: [[a.latitude, a.longitude], [b.latitude, b.longitude]], color: inc.source === 'tomtom' ? '#f43f5e' : '#a78bfa', weight: 7, opacity: 0.9,
        label: <><strong className="capitalize">{inc.kind.replace('_', ' ')}</strong><br />{inc.source === 'tomtom' ? 'TomTom live report (matched road edge)' : 'Simulated/manual test incident'} · severity {inc.severity}<br />{inc.description}</>,
      });
    });
  }

  // Provider flow segments: drawn only when the backend returned real provider data (live / cached / stale cache).
  const flowData = flow.data;
  const flowHasData = !!flowData && flowData.segments !== undefined && flowData.segments.length > 0;
  const flowLines: MapLine[] = [];
  let flowHidden = 0;
  if (showFlow && flowHasData) {
    const sorted = [...(flowData!.segments ?? [])].sort((a, b) => FLOW_RANK[a.level] - FLOW_RANK[b.level]);
    flowHidden = Math.max(0, sorted.length - MAX_FLOW_LINES);
    sorted.slice(0, MAX_FLOW_LINES).forEach((s) => flowLines.push({
      positions: s.path, color: FLOW_COLORS[s.level], weight: 4, opacity: 0.85,
      label: <><strong>{s.description ?? 'Road segment'}</strong><br />Level: {s.level}{s.jam_factor != null ? ` (jam factor ${s.jam_factor.toFixed(1)}/10)` : ''}<br />
        {s.current_kph != null ? `${s.current_kph.toFixed(0)} km/h` : 'speed n/a'} vs free-flow {s.free_flow_kph != null ? `${s.free_flow_kph.toFixed(0)} km/h` : 'n/a'}<br />
        <em>{flowData!.provider.toUpperCase()} · {flowData!.data_state}{flowData!.data_state === 'stale_cache' ? ' (not live)' : ''}</em></>,
    }));
  }
  const tilesAvailable = !!provider.data?.configured && !!provider.data?.supports.tiles;

  const loading = current.loading || preds.loading;
  const error = current.error ?? preds.error;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Traffic Map"
        description="Synthetic-location congestion, ML predictions and routing-graph incidents"
        icon={<MapIcon className="h-6 w-6" />}
        action={
          <div className="flex flex-wrap items-end gap-3">
            <Select label="Layer" value={layer} onChange={(v) => setLayer(v as Layer)}
              options={[{ value: 'predicted', label: 'ML prediction (next 30 min)' }, { value: 'observed', label: 'Latest synthetic record' }]} />
            <Select label="Congestion" value={filter} onChange={setFilter}
              options={[{ value: 'all', label: 'All classes' }, { value: 'Low', label: 'Low' }, { value: 'Medium', label: 'Medium' }, { value: 'High', label: 'High' }]} />
            <button onClick={() => setShowIncidents((v) => !v)}
              className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-xs font-medium transition-colors ${showIncidents ? 'border-rose-500/30 bg-rose-500/10 text-rose-300' : 'border-navy-600/50 bg-navy-800/60 text-slate-400'}`}>
              Incident edges
            </button>
            <button onClick={() => setShowFlow((v) => !v)} disabled={!flowHasData}
              className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-xs font-medium transition-colors disabled:opacity-40 ${showFlow && flowHasData ? 'border-teal-500/30 bg-teal-500/10 text-teal-300' : 'border-navy-600/50 bg-navy-800/60 text-slate-400'}`}>
              Provider traffic flow
            </button>
            <button onClick={() => setShowTiles((v) => !v)} disabled={!tilesAvailable}
              className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-xs font-medium transition-colors disabled:opacity-40 ${showTiles && tilesAvailable ? 'border-teal-500/30 bg-teal-500/10 text-teal-300' : 'border-navy-600/50 bg-navy-800/60 text-slate-400'}`}>
              Tile layer
            </button>
          </div>
        }
      />
      <SyntheticBanner timestamp={current.data?.data_timestamp} />
      {flow.loading ? null : flow.error ? (
        <ProvenanceBanner tone="warn"><strong className="font-semibold">Live traffic: unavailable.</strong> The backend traffic endpoint could not be reached ({flow.error}). No traffic overlay is drawn.</ProvenanceBanner>
      ) : flowData && (flowData.data_state === 'live' || flowData.data_state === 'cached') ? (
        <ProvenanceBanner>
          <strong className="font-semibold">Provider traffic overlay: {flowData.data_state === 'live' ? 'LIVE' : 'CACHED'} ({flowData.provider.toUpperCase()}).</strong>{' '}
          Coloured road segments come from the {flowData.provider.toUpperCase()} Traffic API{flowData.age_seconds != null && flowData.data_state === 'cached' ? ` (fetched ${Math.round(flowData.age_seconds)}s ago)` : ''}
          {flowData.source_updated ? `, provider timestamp ${new Date(flowData.source_updated).toLocaleString('en-IN')}` : ''}. The location markers below remain synthetic.
        </ProvenanceBanner>
      ) : (
        <ProvenanceBanner tone="warn">
          <strong className="font-semibold">
            {flowData?.data_state === 'stale_cache' ? 'Provider traffic overlay: STALE CACHE — not live.' : flowData?.data_state === 'disabled' ? 'Live traffic: provider not configured.' : 'Live traffic: unavailable.'}
          </strong>{' '}
          {flowData?.message ?? 'No provider traffic is shown.'} Nothing on this map is simulated as live traffic; markers are synthetic/ML only.
        </ProvenanceBanner>
      )}

      <div className="flex flex-wrap items-center gap-4 rounded-lg border border-navy-700/60 bg-navy-900/60 px-4 py-3">
        <span className="flex items-center gap-1.5 text-xs text-slate-400"><Filter className="h-3.5 w-3.5" /> Legend:</span>
        {(['Low', 'Medium', 'High'] as CongestionLabel[]).map((l) => (
          <span key={l} className="flex items-center gap-1.5 text-xs text-slate-300">
            <span className="h-3 w-3 rounded-full" style={{ backgroundColor: severityColor(labelToSeverity(l)) }} />{l} ({counts[l]})
          </span>
        ))}
        <span className="flex items-center gap-1.5 text-xs text-slate-300"><span className="h-1 w-5 rounded bg-rose-500" /> TomTom incident edge</span>
        <span className="flex items-center gap-1.5 text-xs text-slate-300"><span className="h-1 w-5 rounded bg-violet-400" /> Manual / simulated edge</span>
        {(flowHasData || showTiles) && (['free', 'slow', 'heavy', 'severe', 'closed'] as TrafficLevel[]).map((l) => (
          <span key={l} className="flex items-center gap-1.5 text-xs capitalize text-slate-300"><span className="h-1 w-5 rounded" style={{ backgroundColor: FLOW_COLORS[l] }} />Traffic: {l}</span>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-4">
        <Card title="Bhubaneswar map" subtitle="Click markers for details · marker positions are approximate demo anchors"
          className="lg:col-span-3" icon={<MapPin className="h-5 w-5" />}
          action={<SourceTag kind={layer === 'predicted' ? 'ml' : 'synthetic'} />}>
          {loading ? <LoadingState /> : error ? <ErrorState message={error} onRetry={() => { current.reload(); preds.reload(); }} /> :
            <TrafficMap points={points} lines={[...flowLines, ...incidentLines]} height="480px" autoFit={false}
              trafficTileUrl={showTiles && tilesAvailable ? TRAFFIC_TILE_URL : undefined} />}
          {flowHidden > 0 && <p className="mt-2 text-xs text-slate-400">Showing the {MAX_FLOW_LINES} most congested of {flowData?.segments?.length} flow segments; enable the tile layer to see the full network.</p>}
          {unplaced > 0 && <p className="mt-2 text-xs text-amber-300">{unplaced} incident(s) not drawn: their road-graph nodes are outside the node list returned by the backend.</p>}
        </Card>

        <div className="space-y-4">
          <Card title="Locations" subtitle={`${shown.length} shown`}>
            <div className="max-h-[300px] space-y-2 overflow-y-auto pr-1">
              {shown.length === 0 ? <EmptyState message="No locations match this filter." /> : shown.map(({ row, label, predicted }) => (
                <div key={row.location} className="rounded-lg border border-navy-700/50 bg-navy-800/40 p-3">
                  <div className="flex items-center justify-between">
                    <p className="text-sm font-medium text-slate-200">{row.location}</p>
                    <Badge severity={labelToSeverity(label)}>{label}</Badge>
                  </div>
                  <div className="mt-1.5 flex justify-between text-xs text-slate-400">
                    <span>{row.avg_speed} km/h</span><span>{row.vehicle_count} veh</span>
                    <span>pred: {predicted ?? 'n/a'}</span>
                  </div>
                </div>
              ))}
            </div>
          </Card>

          <Card title="Live traffic provider" subtitle="Measured road traffic (needs an API key in backend/.env)" icon={<Radio className="h-5 w-5" />}>
            {flow.loading || provider.loading ? <LoadingState label="Checking traffic provider…" /> : flow.error ? <ErrorState message={flow.error} onRetry={() => { flow.reload(); provider.reload(); }} /> : (
              <div className="space-y-2 text-xs text-slate-400">
                <div className="flex items-center justify-between">
                  <span>Provider</span><span className="font-semibold uppercase text-slate-200">{flowData?.provider ?? provider.data?.provider}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span>Data state</span>
                  {flowData && <TrafficStateBadge state={flowData.data_state} ageSeconds={flowData.age_seconds} />}
                </div>
                <div className="flex items-center justify-between"><span>Segments received</span><span className="text-slate-200">{flowData?.segment_count ?? 0}</span></div>
                {flowData?.message && <p>{flowData.message}</p>}
                {provider.data && <p>{provider.data.coverage_note}</p>}
                {provider.data?.last_error && <p className="text-amber-300">Last error: {provider.data.last_error}</p>}
                {!provider.data?.configured && <p>Set TRAFFIC_PROVIDER and HERE_API_KEY in backend/.env (see docs/HERE_SETUP.md).</p>}
                <button onClick={() => { flow.reload(); provider.reload(); }} className="mt-1 rounded border border-navy-600/50 px-2 py-1 text-slate-300 hover:bg-navy-800">Refresh traffic</button>
              </div>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}
