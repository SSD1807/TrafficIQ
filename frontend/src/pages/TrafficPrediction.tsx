import { useEffect, useState } from 'react';
import { TrendingUp, Clock, Zap, Activity, RefreshCw } from 'lucide-react';
import { PageHeader } from '@/components/ui/PageHeader';
import { Card, StatCard } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Input, Select } from '@/components/ui/Select';
import { LoadingState, ErrorState } from '@/components/ui/States';
import { ProvenanceBanner, SourceTag, SyntheticBanner } from '@/components/ui/Provenance';
import { ProbabilityBars } from '@/components/charts';
import { api, ApiError } from '@/lib/api';
import { useApi } from '@/lib/useApi';
import { formatDateTime, labelToSeverity, num } from '@/lib/utils';
import type { PredictionRequest, PredictionResult } from '@/types';

const numOrNull = (s: string) => (s.trim() === '' || Number.isNaN(Number(s)) ? null : Number(s));

export function TrafficPrediction() {
  const locs = useApi(() => api.getLocations(), []);
  const current = useApi(() => api.getCurrentTraffic(), []);
  const [location, setLocation] = useState('');
  const [useWeather, setUseWeather] = useState(false);
  const [allowSim, setAllowSim] = useState(false);
  const [ov, setOv] = useState({ vehicle_count: '', avg_speed: '', rainfall: '', clouds: '', temperature: '' });

  const [result, setResult] = useState<PredictionResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { if (!location && locs.data?.locations.length) setLocation(locs.data.locations[0]); }, [locs.data, location]);

  const run = async () => {
    if (!location) return;
    const body: PredictionRequest = {
      use_open_meteo_weather: useWeather,
      allow_simulated_weather_fallback: useWeather && allowSim,
      vehicle_count: numOrNull(ov.vehicle_count), avg_speed: numOrNull(ov.avg_speed),
      rainfall: numOrNull(ov.rainfall), clouds: numOrNull(ov.clouds), temperature: numOrNull(ov.temperature),
    };
    setLoading(true); setError(null);
    try { setResult(await api.predict(location, body)); }
    catch (e) { setResult(null); setError(e instanceof ApiError ? e.message : 'Prediction failed.'); }
    finally { setLoading(false); }
  };

  // Re-run automatically when the location changes (with the current options).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { if (location) void run(); }, [location]);

  const row = current.data?.locations.find((r) => r.location === location);
  const topProb = result?.class_probabilities ? Math.max(...Object.values(result.class_probabilities)) : null;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Traffic Prediction" description="Next-30-minute congestion class from the trained classifier"
        icon={<TrendingUp className="h-6 w-6" />}
        action={<Select label="Location" id="pred-location" value={location} onChange={setLocation}
          options={(locs.data?.locations ?? []).map((l) => ({ value: l, label: l }))} />}
      />
      <SyntheticBanner timestamp={current.data?.data_timestamp} />

      {locs.error && <ErrorState message={locs.error} onRetry={locs.reload} />}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Last recorded label" value={row?.congestion_label ?? '—'} change={row ? `${row.avg_speed} km/h` : undefined} icon={<Activity className="h-5 w-5" />} />
        <StatCard label="Predicted (next 30 min)" value={result?.predicted_congestion ?? '—'} change="ML output" trend={result?.predicted_congestion === 'High' ? 'up' : 'neutral'} icon={<Zap className="h-5 w-5" />} />
        <StatCard label="Top class probability" value={topProb === null ? '—' : `${(topProb * 100).toFixed(1)}%`} change="model probability" icon={<TrendingUp className="h-5 w-5" />} />
        <StatCard label="Prediction time" value={result ? new Date(result.prediction_timestamp).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' }) : '—'} change="+30 min" icon={<Clock className="h-5 w-5" />} />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <Card title={`Prediction: ${location || '—'}`} subtitle={result ? `${result.model_name} · origin ${formatDateTime(result.origin_timestamp)} → ${formatDateTime(result.prediction_timestamp)}` : 'Run a prediction'}
          className="lg:col-span-2" icon={<TrendingUp className="h-5 w-5" />} action={<SourceTag kind="ml" />}>
          {loading ? <LoadingState label="Running classifier…" /> : error ? <ErrorState message={error} onRetry={run} /> : result ? (
            <div className="space-y-4">
              <div className="flex items-center gap-3">
                <Badge severity={labelToSeverity(result.predicted_congestion)} className="px-4 py-1.5 text-base">{result.predicted_congestion}</Badge>
                <span className="text-xs text-slate-400">weather input: {result.weather_input_source}</span>
              </div>
              {result.class_probabilities ? <ProbabilityBars probabilities={result.class_probabilities} /> : <p className="text-xs text-slate-400">The model did not return class probabilities.</p>}
              <div className="grid grid-cols-2 gap-3 text-xs sm:grid-cols-5">
                {([['Vehicles (lag)', num(result.input_measurements.lag_vehicle_count, 0)], ['Speed (lag)', num(result.input_measurements.lag_avg_speed, 1, ' km/h')],
                  ['Rain (lag)', num(result.input_measurements.lag_rainfall, 1, ' mm')], ['Clouds (lag)', num(result.input_measurements.lag_clouds, 0, '%')],
                  ['Temp (lag)', num(result.input_measurements.lag_temperature, 1, '°C')]] as const).map(([k, v]) => (
                  <div key={k} className="rounded-lg border border-navy-700/50 bg-navy-800/40 p-3"><p className="text-slate-400">{k}</p><p className="mt-1 font-semibold text-slate-100">{v}</p></div>
                ))}
              </div>
              <p className="text-xs text-slate-500">{result.notice}</p>
            </div>
          ) : null}
        </Card>

        <Card title="What-if inputs" subtitle="Optional overrides of the model's input measurements" icon={<Zap className="h-5 w-5" />}>
          <div className="space-y-3">
            {([['vehicle_count', 'Vehicle count'], ['avg_speed', 'Avg speed (km/h)'], ['rainfall', 'Rainfall (mm)'], ['clouds', 'Clouds (%)'], ['temperature', 'Temperature (°C)']] as const).map(([k, l]) => (
              <Input key={k} id={`ov-${k}`} label={l} type="number" value={ov[k]} placeholder="use dataset value" onChange={(v) => setOv((o) => ({ ...o, [k]: v }))} />
            ))}
            <label className="flex items-center gap-2 text-xs text-slate-300">
              <input type="checkbox" checked={useWeather} onChange={(e) => setUseWeather(e.target.checked)} /> Use Open-Meteo weather (needs internet)
            </label>
            <label className={`flex items-center gap-2 text-xs ${useWeather ? 'text-slate-300' : 'text-slate-600'}`}>
              <input type="checkbox" disabled={!useWeather} checked={allowSim} onChange={(e) => setAllowSim(e.target.checked)} /> Allow SIMULATED weather fallback
            </label>
            <Button onClick={run} disabled={loading || !location} icon={<RefreshCw className="h-4 w-4" />} className="w-full">Run prediction</Button>
          </div>
        </Card>
      </div>

      <ProvenanceBanner>
        The classifier predicts the congestion class for the <strong>next consecutive 30-minute interval</strong> only; it cannot predict
        other horizons. For 30/60-minute numeric forecasts see Forecasting Analytics.
      </ProvenanceBanner>
    </div>
  );
}
