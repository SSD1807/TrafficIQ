import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

interface CardProps {
  children: ReactNode;
  className?: string;
  title?: string;
  subtitle?: string;
  action?: ReactNode;
  icon?: ReactNode;
}

export function Card({ children, className, title, subtitle, action, icon }: CardProps) {
  return (
    <div
      className={cn(
        'rounded-xl border border-navy-700/60 bg-navy-900/80 backdrop-blur-sm shadow-card',
        'transition-colors hover:border-navy-600/60',
        className
      )}
    >
      {(title || action) && (
        <div className="flex items-start justify-between gap-4 px-5 pt-5">
          <div className="flex items-start gap-3">
            {icon && <div className="text-cyan-400 mt-0.5">{icon}</div>}
            <div>
              {title && <h3 className="text-sm font-semibold text-slate-100">{title}</h3>}
              {subtitle && <p className="text-xs text-slate-400 mt-0.5">{subtitle}</p>}
            </div>
          </div>
          {action}
        </div>
      )}
      <div className="p-5">{children}</div>
    </div>
  );
}

export function StatCard({
  label,
  value,
  change,
  trend,
  icon,
}: {
  label: string;
  value: string;
  change?: string;
  trend?: 'up' | 'down' | 'neutral';
  icon?: ReactNode;
}) {
  const trendColor =
    trend === 'up' ? 'text-teal-400' : trend === 'down' ? 'text-rose-400' : 'text-slate-400';
  return (
    <div className="rounded-xl border border-navy-700/60 bg-navy-900/80 p-5 shadow-card transition-all hover:border-cyan-500/40 hover:shadow-glow">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium uppercase tracking-wide text-slate-400">{label}</span>
        {icon && <div className="text-cyan-400">{icon}</div>}
      </div>
      <div className="mt-3 flex items-baseline gap-2">
        <span className="text-2xl font-bold text-slate-50">{value}</span>
        {change && <span className={cn('text-xs font-semibold', trendColor)}>{change}</span>}
      </div>
    </div>
  );
}
