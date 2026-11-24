import { useEffect, useState } from 'react';
import { LineChart, BarChart3, TrendingUp, Gauge } from 'lucide-react';
import { PageHeader } from '@/components/ui/PageHeader';
import { Card, StatCard } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Select } from '@/components/ui/Select';
import { LoadingState, ErrorState, EmptyState, UnavailableNote } from '@/components/ui/States';
import { SourceTag, SyntheticBanner } from '@/components/ui/Provenance';
import { GroupedColumns, HorizontalBars, ProbabilityBars } from '@/components/charts';
import { api } from '@/lib/api';
import { useApi } from '@/lib/useApi';
import { formatDateTime, labelToSeverity, num, severityColor } from '@/lib/utils';
import type { ForecastResult, RegressorFamily } from '@/types';

const MODEL_OPTIONS = [{ value: 'random_forest', label: 'Random Forest regressors' }, { value: 'xgboost', label: 'XGBoost regressors' }];

export function ForecastingAnalytics() {
  const locs = useApi(() => api.getLocations(), []);
  const current = useApi(() => api.getCurrentTraffic(), []);
  const [location, setLocation] = useState('');
  const [model, setModel] = useState<RegressorFamily>('random_forest');
  const [cmpHorizon, setCmpHorizon] = useState<'30' | '60'>('30');

  useEffect(() => { if (!location && locs.data?.locations.length) setLocation(locs.data.locations[0]); }, [locs.data, location]);

  const pair = useApi(async () => {
    if (!location) return null;
    const [f30, f60] = await Promise.all([api.getForecast(location, 30, model), api.getForecast(location, 60, model)]);
    return { f30, f60 };
  }, [location, model]);

  const all = useApi(async () => {
    const names = locs.data?.locations ?? [];
    if (!names.length) return [] as ForecastResult[];
    return Promise.all(names.map((n) => api.getForecast(n, Number(cmpHorizon) as 30 | 60, model)));
  }, [locs.data, cmpHorizon, model]);

  const last = current.data?.locations.find((r) => r.location === location);
  const f30 = pair.data?.f30; const f60 = pair.data?.f60;

  const vehicleData = last && f30 && f60 ? [{ x: 'Last record', value: last.vehicle_count }, { x: '+30 min', value: f30.vehicle_count ?? 0 }, { x: '+60 min', value: f60.vehicle_count ?? 0 }] : [];
  const speedData = last && f30 && f60 ? [{ x: 'Last record', value: last.avg_speed }, { x: '+30 min', value: f30.avg_speed ?? 0 }, { x: '+60 min', value: f60.avg_speed ?? 0 }] : [];

  return (
    <div className="space-y-6">
      <PageHeader
        title="Forecasting Analytics" description="Trained 30- and 60-minute forecasts of vehicle count, speed and congestion class"
        icon={<LineChart className="h-6 w-6" />}
        action={
          <div className="flex flex-wrap items-end gap-3">
            <Select label="Location" id="fc-location" value={location} onChange={setLocation} options={(locs.data?.locations ?? []).map((l) => ({ value: l, label: l }))} />
            <Select label="Regressor family" id="fc-model" value={model} onChange={(v) => setModel(v as RegressorFamily)} options={MODEL_OPTIONS} />
          </div>
        }
      />
      <SyntheticBanner timestamp={current.data?.data_timestamp} />
      {locs.error && <ErrorState message={locs.error} onRetry={locs.reload} />}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="+30 min congestion" value={f30?.predicted_congestion ?? '—'} change="forecast" icon={<TrendingUp className="h-5 w-5" />} />
        <StatCard label="+60 min congestion" value={f60?.predicted_congestion ?? '—'} change="forecast" icon={<TrendingUp className="h-5 w-5" />} />
        <StatCard label="+30 min speed" value={num(f30?.avg_speed, 1, ' km/h')} change={last ? `now ${last.avg_speed}` : undefined} icon={<Gauge className="h-5 w-5" />} />
        <StatCard label="+60 min speed" value={num(f60?.avg_speed, 1, ' km/h')} change={last ? `now ${last.avg_speed}` : undefined} icon={<Gauge className="h-5 w-5" />} />
      </div>

      {pair.loading ? <Card><LoadingState label="Running forecast models…" /></Card> : pair.error ? <Card><ErrorState message={pair.error} onRetry={pair.reload} /></Card> : f30 && f60 && (
        <>
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <Card title="Vehicle count" subtitle={`${location}: last synthetic record → forecasts`} icon={<BarChart3 className="h-5 w-5" />} action={<SourceTag kind="forecast" />}>
              <GroupedColumns data={vehicleData} series={[{ key: 'value', name: 'Vehicles / 30-min interval' }]} />
            </Card>
            <Card title="Average speed" subtitle={`${location}: last synthetic record → forecasts`} icon={<BarChart3 className="h-5 w-5" />} action={<SourceTag kind="forecast" />}>
              <GroupedColumns data={speedData} series={[{ key: 'value', name: 'Avg speed (km/h)', color: '#2dd4bf' }]} />
            </Card>
          </div>

          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            {[f30, f60].map((f) => (
              <Card key={f.horizon_minutes} title={`+${f.horizon_minutes} min congestion class`} subtitle={`Target ${formatDateTime(f.target_timestamp)} · ${f.forecast_model}`}
                action={f.predicted_congestion ? <Badge severity={labelToSeverity(f.predicted_congestion)}>{f.predicted_congestion}</Badge> : undefined}>
                {f.congestion_probabilities ? <ProbabilityBars probabilities={f.congestion_probabilities} height={170} /> : <EmptyState message="No class probabilities returned." />}
                <p className="mt-2 text-xs text-slate-500">{f.notice}</p>
              </Card>
            ))}
          </div>
        </>
      )}

      <Card title="All locations compared" subtitle="Forecast speed per location at the selected horizon" icon={<BarChart3 className="h-5 w-5" />}
        action={<Select value={cmpHorizon} onChange={(v) => setCmpHorizon(v as '30' | '60')} options={[{ value: '30', label: '+30 min' }, { value: '60', label: '+60 min' }]} />}>
        {all.loading ? <LoadingState label="Forecasting all locations…" /> : all.error ? <ErrorState message={all.error} onRetry={all.reload} /> : (all.data?.length ?? 0) === 0 ? <EmptyState /> : (
          <HorizontalBars
            data={[...all.data!].sort((a, b) => (a.avg_speed ?? 0) - (b.avg_speed ?? 0)).map((f) => ({ name: f.location, value: +(f.avg_speed ?? 0).toFixed(1) }))}
            valueLabel="Forecast avg speed (km/h)" height={320}
            colorFor={(n) => severityColor(labelToSeverity(all.data!.find((f) => f.location === n)?.predicted_congestion))}
          />
        )}
      </Card>

      <UnavailableNote>
        The backend forecasts a single point 30 and 60 minutes ahead. It has no weekly/daily series or confidence-interval endpoint, so those
        views from the original design are not shown. Forecast accuracy figures are on the Model Evaluation page.
      </UnavailableNote>
    </div>
  );
}
