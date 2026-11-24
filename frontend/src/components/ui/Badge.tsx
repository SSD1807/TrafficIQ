import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';
import type { TrafficSeverity } from '@/types';

const severityStyles: Record<TrafficSeverity, string> = {
  low: 'bg-teal-500/15 text-teal-300 border-teal-500/30',
  moderate: 'bg-amber-500/15 text-amber-300 border-amber-500/30',
  high: 'bg-orange-500/15 text-orange-300 border-orange-500/30',
  severe: 'bg-rose-500/15 text-rose-300 border-rose-500/30',
};

export function Badge({
  children,
  variant = 'neutral',
  severity,
  className,
}: {
  children: ReactNode;
  variant?: 'neutral' | 'success' | 'warning' | 'danger' | 'info';
  severity?: TrafficSeverity;
  className?: string;
}) {
  if (severity) {
    return (
      <span
        className={cn(
          'inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-medium',
          severityStyles[severity],
          className
        )}
      >
        {children}
      </span>
    );
  }

  const variants: Record<string, string> = {
    neutral: 'bg-navy-700/40 text-slate-300 border-navy-600/50',
    success: 'bg-teal-500/15 text-teal-300 border-teal-500/30',
    warning: 'bg-amber-500/15 text-amber-300 border-amber-500/30',
    danger: 'bg-rose-500/15 text-rose-300 border-rose-500/30',
    info: 'bg-cyan-500/15 text-cyan-300 border-cyan-500/30',
  };

  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-medium',
        variants[variant],
        className
      )}
    >
      {children}
    </span>
  );
}
