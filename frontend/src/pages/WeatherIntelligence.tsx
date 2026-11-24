import { useState } from 'react';
import { CloudRain, Droplets, Wind, Thermometer, Cloud, RefreshCw, FlaskConical, Compass } from 'lucide-react';
import { PageHeader } from '@/components/ui/PageHeader';
import { Card, StatCard } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { LoadingState, ErrorState, UnavailableNote } from '@/components/ui/States';
import { ProvenanceBanner, SourceTag } from '@/components/ui/Provenance';
import { api } from '@/lib/api';
import { useApi } from '@/lib/useApi';
import { formatDateTime, num } from '@/lib/utils';
import type { WeatherResponse } from '@/types';

export function WeatherIntelligence() {
  const [simulate, setSimulate] = useState(false);
  const [tick, setTick] = useState(0);
  const [force, setForce] = useState(false);
  const w = useApi<WeatherResponse>(() => api.getWeather({ allowSimulatedFallback: simulate, forceRefresh: force }), [simulate, tick]);
  const d = w.data;

  const kind = d?.status === 'simulated_fallback' ? 'weather-sim' as const : d && d.status !== 'unavailable' ? 'weather-live' as const : null;
  const statusBadge = d?.status === 'ok' ? { v: 'success' as const, t: 'Live (Open-Meteo)' }
    : d?.status === 'stale_cache' ? { v: 'warning' as const, t: `Stale cache (${d.cache_age_seconds ?? '?'} s old)` }
    : d?.status === 'simulated_fallback' ? { v: 'danger' as const, t: 'SIMULATED' }
    : { v: 'danger' as const, t: 'Unavailable' };

  return (
    <div className="space-y-6">
      <PageHeader title="Weather Intelligence" description="Current conditions for Bhubaneswar from Open-Meteo, with explicit simulated fallback"
        icon={<CloudRain className="h-6 w-6" />}
        action={<div className="flex gap-2">
          <Button variant="secondary" size="sm" icon={<RefreshCw className="h-3.5 w-3.5" />} onClick={() => { setForce(true); setTick((t) => t + 1); }}>Refresh</Button>
          <Button variant={simulate ? 'primary' : 'secondary'} size="sm" icon={<FlaskConical className="h-3.5 w-3.5" />} onClick={() => { setForce(false); setSimulate((s) => !s); }}>
            {simulate ? 'Simulated fallback ON' : 'Allow simulated fallback'}
          </Button>
        </div>} />

      {d?.status === 'simulated_fallback' ? (
        <ProvenanceBanner tone="warn"><strong>SIMULATED WEATHER.</strong> Open-Meteo could not be reached; these numbers are generated demo values, not observations.</ProvenanceBanner>
      ) : (
        <ProvenanceBanner>Live values are Open-Meteo <em>model-derived</em> current conditions, not roadside sensor readings. Without internet the backend reports “unavailable” unless you enable the labelled simulated fallback.</ProvenanceBanner>
      )}

      {w.loading ? <Card><LoadingState label="Fetching weather…" /></Card> : w.error ? <Card><ErrorState message={w.error} onRetry={w.reload} /></Card> : d && (
        d.status === 'unavailable' ? (
          <Card title="Weather unavailable" icon={<CloudRain className="h-5 w-5" />}>
            <div className="space-y-3 text-sm text-slate-300">
              <p>{d.message ?? 'The weather service returned no data.'}</p>
              {d.error && <p className="text-xs text-rose-300">Error: {d.error}</p>}
              <p className="text-xs text-slate-400">No weather values are shown because none are available. Check your internet connection, or use “Allow simulated fallback” for a clearly labelled demo.</p>
            </div>
          </Card>
        ) : (
          <>
            <Card title="Current weather" subtitle={`Bhubaneswar · observed ${d.observation_time ?? 'n/a'} (${d.timezone ?? 'Asia/Kolkata'}) · fetched ${formatDateTime(d.fetched_at)}`}
              action={<div className="flex gap-2">{kind && <SourceTag kind={kind} />}<Badge variant={statusBadge.v}>{statusBadge.t}</Badge></div>}>
              <div className="flex flex-col gap-6 md:flex-row md:items-center md:justify-between">
                <div className="flex items-center gap-5">
                  <div className="flex h-20 w-20 items-center justify-center rounded-2xl bg-gradient-to-br from-cyan-500/20 to-teal-500/20 text-cyan-300 ring-1 ring-cyan-500/20"><Thermometer className="h-10 w-10" /></div>
                  <div>
                    <p className="text-4xl font-bold text-slate-50">{num(d.temperature_c, 1, '°C')}</p>
                    <p className="mt-1 text-sm text-slate-400">WMO weather code: {d.weather_code ?? 'n/a'}</p>
                    <p className="text-xs text-slate-500">cache: {d.cache_status ?? 'n/a'}</p>
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                  {([[Droplets, 'Humidity', num(d.relative_humidity_pct, 0, '%')], [Wind, 'Wind', num(d.wind_speed_kmh, 1, ' km/h')], [Cloud, 'Cloud cover', num(d.cloud_cover_pct, 0, '%')], [CloudRain, 'Rain', num(d.rain_mm, 1, ' mm')]] as const).map(([Icon, l, v]) => (
                    <div key={l} className="rounded-lg border border-navy-700/50 bg-navy-800/40 p-3 text-center"><Icon className="mx-auto h-5 w-5 text-cyan-400" /><p className="mt-1.5 text-xs text-slate-400">{l}</p><p className="text-sm font-bold text-slate-100">{v}</p></div>
                  ))}
                </div>
              </div>
              {d.last_error && <p className="mt-3 text-xs text-amber-300">Last refresh error: {d.last_error}</p>}
              <p className="mt-3 text-xs text-slate-500">{d.attribution ?? d.notice}</p>
            </Card>

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <StatCard label="Precipitation" value={num(d.precipitation_mm, 1, ' mm')} icon={<Droplets className="h-5 w-5" />} />
              <StatCard label="Showers" value={num(d.showers_mm, 1, ' mm')} icon={<CloudRain className="h-5 w-5" />} />
              <StatCard label="Wind gusts" value={num(d.wind_gusts_kmh, 1, ' km/h')} icon={<Wind className="h-5 w-5" />} />
              <StatCard label="Wind direction" value={num(d.wind_direction_deg, 0, '°')} icon={<Compass className="h-5 w-5" />} />
            </div>
          </>
        )
      )}

      <UnavailableNote>
        The backend exposes current conditions only. There is no hourly weather forecast, visibility, “traffic impact score” or advisory endpoint, so the
        12-hour impact chart and advisory text from the original design were removed rather than invented. Weather can be fed into the congestion model on the Traffic Prediction page.
      </UnavailableNote>
    </div>
  );
}
