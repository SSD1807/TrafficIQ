import { useMemo } from 'react';
import { LayoutDashboard, MapPin, Gauge, AlertTriangle, Siren, Car, Database } from 'lucide-react';
import { PageHeader } from '@/components/ui/PageHeader';
import { Card, StatCard } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { LoadingState, ErrorState, EmptyState } from '@/components/ui/States';
import { SourceTag, SyntheticBanner } from '@/components/ui/Provenance';
import { TrafficMap, type MapPoint } from '@/components/map/TrafficMap';
import { HorizontalBars } from '@/components/charts';
import { api } from '@/lib/api';
import { locationText, SOURCE_LABEL } from '@/lib/incidentStatus';
import { useApi } from '@/lib/useApi';
import { getAnchor } from '@/lib/locations';
import { isPrediction } from '@/lib/predictions';
import { incidentSeverity, labelToSeverity, severityColor, formatTime } from '@/lib/utils';

export function ExecutiveOverview() {
  const current = useApi(() => api.getCurrentTraffic(), []);
  const preds = useApi(() => api.getAllPredictions(), []);
  const incidents = useApi(() => api.getIncidents(), []);
  const health = useApi(() => api.getHealth(), []);

  const predictions = useMemo(() => (preds.data?.predictions ?? []).filter(isPrediction), [preds.data]);
  const highCount = predictions.filter((p) => p.predicted_congestion === 'High').length;
  const rows = current.data?.locations ?? [];
  const avgSpeed = rows.length ? rows.reduce((s, r) => s + r.avg_speed, 0) / rows.length : null;

  const points: MapPoint[] = useMemo(
    () => predictions.flatMap((p) => {
      const a = getAnchor(p.location);
      if (!a) return [];
      const sev = labelToSeverity(p.predicted_congestion);
      return [{
        id: p.location, lat: a.lat, lon: a.lon, color: severityColor(sev), radius: 10,
        popup: (<><strong>{p.location}</strong><br />Predicted (next 30 min): {p.predicted_congestion}<br />
          <em>Approximate demo anchor · synthetic data</em></>),
      }];
    }),
    [predictions],
  );

  const riskRanking = [...predictions]
    .sort((a, b) => (b.class_probabilities?.High ?? 0) - (a.class_probabilities?.High ?? 0))
    .slice(0, 5);

  const speedBars = [...rows].sort((a, b) => a.avg_speed - b.avg_speed)
    .map((r) => ({ name: r.location, value: r.avg_speed, label: r.congestion_label }));
  const labelByName = new Map(speedBars.map((s) => [s.name, s.label]));

  return (
    <div className="space-y-6">
      <PageHeader
        title="Executive Overview"
        description="Snapshot of the synthetic-data traffic model, predictions and routing state for Bhubaneswar"
        icon={<LayoutDashboard className="h-6 w-6" />}
      />
      <SyntheticBanner timestamp={current.data?.data_timestamp} />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Locations in dataset" value={current.data ? String(rows.length) : '—'} change="synthetic" icon={<MapPin className="h-5 w-5" />} />
        <StatCard label="Avg speed (latest record)" value={avgSpeed === null ? '—' : `${avgSpeed.toFixed(1)} km/h`} change="not live" icon={<Gauge className="h-5 w-5" />} />
        <StatCard label="Predicted High (next 30 min)" value={preds.data ? String(highCount) : '—'} change="ML output" trend={highCount > 0 ? 'up' : 'neutral'} icon={<AlertTriangle className="h-5 w-5" />} />
        <StatCard label="Active incidents" value={incidents.data ? String(incidents.data.counts.total) : '—'}
          change={incidents.data ? `${incidents.data.counts.tomtom} TomTom live · ${incidents.data.counts.manual + incidents.data.counts.simulation} manual/simulated` : undefined} icon={<Siren className="h-5 w-5" />} />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <Card
          title="Average speed by location" subtitle="Latest record in the synthetic dataset (colour = recorded congestion label)"
          className="lg:col-span-2" icon={<Car className="h-5 w-5" />} action={<SourceTag kind="synthetic" />}
        >
          {current.loading ? <LoadingState /> : current.error ? <ErrorState message={current.error} onRetry={current.reload} /> :
            rows.length === 0 ? <EmptyState message="The backend reported no locations." /> : (
              <HorizontalBars
                data={speedBars.map(({ name, value }) => ({ name, value }))} valueLabel="Avg speed (km/h)" height={300}
                colorFor={(n) => severityColor(labelToSeverity(labelByName.get(n)))}
              />
            )}
        </Card>

        <Card title="Highest predicted risk" subtitle="Probability of High congestion, next 30 min" icon={<AlertTriangle className="h-5 w-5" />} action={<SourceTag kind="ml" />}>
          {preds.loading ? <LoadingState /> : preds.error ? <ErrorState message={preds.error} onRetry={preds.reload} /> :
            riskRanking.length === 0 ? <EmptyState message="No predictions returned." /> : (
              <div className="space-y-2">
                {riskRanking.map((p, i) => (
                  <div key={p.location} className="flex items-center justify-between text-sm">
                    <span className="flex items-center gap-2 text-slate-300">
                      <span className="text-xs font-bold text-slate-500">#{i + 1}</span>{p.location}
                    </span>
                    <Badge severity={labelToSeverity(p.predicted_congestion)}>
                      {p.predicted_congestion}{p.class_probabilities ? ` · P(High) ${(p.class_probabilities.High * 100).toFixed(1)}%` : ''}
                    </Badge>
                  </div>
                ))}
              </div>
            )}
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <Card title="Predicted congestion map" subtitle="Markers use approximate demo anchors for the 10 synthetic locations" className="lg:col-span-2" icon={<MapPin className="h-5 w-5" />} action={<SourceTag kind="ml" />}>
          {preds.loading ? <LoadingState /> : preds.error ? <ErrorState message={preds.error} onRetry={preds.reload} /> :
            <TrafficMap points={points} height="380px" autoFit={false} />}
        </Card>

        <div className="space-y-4">
          <Card title="Incidents" subtitle="Each labelled: TomTom live, manual or simulated (test data)" icon={<Siren className="h-5 w-5" />} action={<SourceTag kind="incident-live" />}>
            {incidents.loading ? <LoadingState /> : incidents.error ? <ErrorState message={incidents.error} onRetry={incidents.reload} /> :
              (incidents.data?.feed.length ?? 0) === 0 ? <EmptyState message={incidents.data?.provider.state === 'live_empty' ? 'TomTom currently reports no incidents. You can create a test incident on the Incident Management page.' : `No active incidents (TomTom: ${incidents.data?.provider.state ?? 'unknown'}). You can create a test incident on the Incident Management page.`} /> : (
                <div className="space-y-3">
                  {incidents.data!.feed.slice(0, 4).map((inc) => (
                    <div key={inc.id} className="rounded-lg border border-navy-700/50 bg-navy-800/40 p-3">
                      <div className="flex items-center justify-between gap-2">
                        <p className="text-sm font-medium capitalize text-slate-200">{inc.kind.replace('_', ' ')}</p>
                        <Badge variant={inc.source === 'tomtom' ? 'info' : 'neutral'}>{SOURCE_LABEL[inc.source]}</Badge>
                      </div>
                      <p className="mt-0.5 text-xs text-slate-400">{inc.source === 'tomtom' ? locationText(inc) : `Edge ${inc.edges[0]?.u} → ${inc.edges[0]?.v} (test incident)`}</p>
                      <div className="mt-1.5 flex items-center gap-2">
                        {inc.severity != null && <Badge severity={incidentSeverity(inc.severity)}>severity {inc.severity.toFixed(2)}</Badge>}
                        <span className="text-[10px] text-slate-500">{formatTime(inc.start_time ?? inc.created_at ?? '')}</span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
          </Card>

          <Card title="Data sources" subtitle="From GET /health (configuration only; no provider call)" icon={<Database className="h-5 w-5" />}>
            {health.loading ? <LoadingState /> : health.error ? <ErrorState message={health.error} onRetry={health.reload} /> : health.data && (
              <div className="space-y-2 text-xs">
                {Object.entries(health.data.data_sources).map(([k, v]) => (
                  <div key={k} className="flex items-center justify-between gap-2">
                    <span className="capitalize text-slate-300">{k.replace('_', ' ')}</span>
                    <Badge variant={v.is_live ? 'success' : 'neutral'}>{v.status}</Badge>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}
