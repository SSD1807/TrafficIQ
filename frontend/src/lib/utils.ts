import type { CongestionLabel, TrafficSeverity } from '@/types';

export function cn(...classes: (string | undefined | false | null)[]): string {
  return classes.filter(Boolean).join(' ');
}

export function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: true });
}

export function formatDateTime(iso?: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' });
}

export function severityColor(sev: string): string {
  switch (sev) {
    case 'low': return '#2dd4bf';
    case 'moderate': return '#fbbf24';
    case 'high': return '#fb923c';
    case 'severe': return '#f43f5e';
    default: return '#64748b';
  }
}

/** Backend congestion classes are Low/Medium/High. Mapped onto the UI palette (no "severe" class exists in the model). */
export function labelToSeverity(label: CongestionLabel | string | null | undefined): TrafficSeverity {
  if (label === 'High') return 'high';
  if (label === 'Medium') return 'moderate';
  return 'low';
}

/** Incident severity is a 0..1 float in the backend. */
export function incidentSeverity(v: number): TrafficSeverity {
  if (v >= 0.8) return 'severe';
  if (v >= 0.55) return 'high';
  if (v >= 0.3) return 'moderate';
  return 'low';
}

export const pct = (v: number | null | undefined, digits = 1) =>
  v === null || v === undefined ? '—' : `${(v * 100).toFixed(digits)}%`;
export const num = (v: number | null | undefined, digits = 1, unit = '') =>
  v === null || v === undefined ? '—' : `${v.toFixed(digits)}${unit}`;
