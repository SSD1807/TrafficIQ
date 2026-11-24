
import { Info } from 'lucide-react';
import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';
import type { TrafficDataState } from '@/types';

/**
 * Every data kind shown in the UI must be labelled with one of these.
 */
export type SourceKind =
  | 'synthetic'
  | 'ml'
  | 'forecast'
  | 'osm'
  | 'osm-fallback'
  | 'weather-live'
  | 'weather-sim'
  | 'tomtom'
  | 'incident-sim'
  | 'incident-live'
  | 'rule-based'
  | 'llm'
  | 'ollama'
  | 'artifact';

const META: Record<SourceKind, { label: string; cls: string }> = {
  synthetic: {
    label: 'Historical synthetic · not live',
    cls: 'border-amber-500/30 bg-amber-500/10 text-amber-300',
  },

  ml: {
    label: 'ML prediction · synthetic input',
    cls: 'border-cyan-500/30 bg-cyan-500/10 text-cyan-300',
  },

  forecast: {
    label: 'Forecast model output',
    cls: 'border-cyan-500/30 bg-cyan-500/10 text-cyan-300',
  },

  osm: {
    label: 'OpenStreetMap geometry',
    cls: 'border-teal-500/30 bg-teal-500/10 text-teal-300',
  },

  'osm-fallback': {
    label: 'Synthetic fallback graph · not OSM',
    cls: 'border-amber-500/30 bg-amber-500/10 text-amber-300',
  },

  'weather-live': {
    label: 'Open-Meteo · live model weather',
    cls: 'border-teal-500/30 bg-teal-500/10 text-teal-300',
  },

  'weather-sim': {
    label: 'SIMULATED weather',
    cls: 'border-rose-500/30 bg-rose-500/10 text-rose-300',
  },

  tomtom: {
    label: 'TomTom · live measurement',
    cls: 'border-teal-500/30 bg-teal-500/10 text-teal-300',
  },

  'incident-live': {
    label: 'TomTom · live reported incidents',
    cls: 'border-teal-500/30 bg-teal-500/10 text-teal-300',
  },

  'incident-sim': {
    label: 'Simulated / manual incident',
    cls: 'border-rose-500/30 bg-rose-500/10 text-rose-300',
  },

  'rule-based': {
    label: 'Rule-based response',
    cls: 'border-slate-500/30 bg-slate-500/10 text-slate-300',
  },

  llm: {
    label: 'LLM · tool-grounded',
    cls: 'border-teal-500/30 bg-teal-500/10 text-teal-300',
  },

  ollama: {
    label: 'Ollama · local tool-grounded',
    cls: 'border-violet-500/30 bg-violet-500/10 text-violet-300',
  },

  artifact: {
    label: 'Saved training artifacts',
    cls: 'border-cyan-500/30 bg-cyan-500/10 text-cyan-300',
  },
};

/**
 * Small provenance label used throughout the dashboard.
 */
export function SourceTag({
  kind,
  className,
  label,
}: {
  kind: SourceKind;
  className?: string;
  label?: string;
}) {
  const meta = META[kind];

  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full border px-2.5 py-0.5 text-[11px] font-medium',
        meta.cls,
        className
      )}
    >
      {label ?? meta.label}
    </span>
  );
}

/**
 * Informational provenance banner.
 */
export function ProvenanceBanner({
  children,
  tone = 'info',
}: {
  children: ReactNode;
  tone?: 'info' | 'warn';
}) {
  const cls =
    tone === 'warn'
      ? 'border-amber-500/20 bg-amber-500/5 text-amber-200'
      : 'border-cyan-500/20 bg-cyan-500/5 text-cyan-200';

  return (
    <div
      className={cn(
        'flex items-start gap-2 rounded-lg border px-4 py-2.5 text-xs',
        cls
      )}
    >
      <Info
        className={cn(
          'mt-0.5 h-4 w-4 shrink-0',
          tone === 'warn'
            ? 'text-amber-400'
            : 'text-cyan-400'
        )}
      />

      <span>{children}</span>
    </div>
  );
}

/**
 * Standard banner for pages driven by the bundled synthetic dataset.
 */
export function SyntheticBanner({
  timestamp,
}: {
  timestamp?: string;
}) {
  return (
    <ProvenanceBanner tone="warn">
      <strong className="font-semibold">
        Historical synthetic data — not live traffic.
      </strong>{' '}

      Values come from the bundled synthetic Bhubaneswar dataset.

      {timestamp ? (
        <>
          {' '}
          (Latest record:{' '}
          {new Date(timestamp).toLocaleString('en-IN', {
            timeZone: 'Asia/Kolkata',
          })}
          )
        </>
      ) : null}

      {' '}Predictions and forecasts are model outputs computed from that
      history; the models have not been validated on real road traffic.
    </ProvenanceBanner>
  );
}

/**
 * Standard traffic-provider state labels.
 */
const STATE_META: Record<
  TrafficDataState,
  { label: string; cls: string }
> = {
  live: {
    label: 'LIVE',
    cls: 'border-teal-500/30 bg-teal-500/10 text-teal-300',
  },

  cached: {
    label: 'CACHED',
    cls: 'border-cyan-500/30 bg-cyan-500/10 text-cyan-300',
  },

  stale_cache: {
    label: 'STALE CACHE · not live',
    cls: 'border-amber-500/30 bg-amber-500/10 text-amber-300',
  },

  unavailable: {
    label: 'UNAVAILABLE',
    cls: 'border-rose-500/30 bg-rose-500/10 text-rose-300',
  },

  disabled: {
    label: 'NOT CONFIGURED',
    cls: 'border-slate-500/30 bg-slate-500/10 text-slate-300',
  },
};

/**
 * Consistent provider status badge:
 * live / cached / stale cache / unavailable / not configured.
 */
export function TrafficStateBadge({
  state,
  provider,
  ageSeconds,
  className,
}: {
  state: TrafficDataState;
  provider?: string;
  ageSeconds?: number | null;
  className?: string;
}) {
  const meta = STATE_META[state] ?? STATE_META.unavailable;

  const age =
    (state === 'cached' || state === 'stale_cache') &&
    ageSeconds != null
      ? ` · ${Math.round(ageSeconds)}s old`
      : '';

  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full border px-2.5 py-0.5 text-[11px] font-semibold',
        meta.cls,
        className
      )}
    >
      {provider ? `${provider.toUpperCase()} · ` : ''}
      {meta.label}
      {age}
    </span>
  );
}
