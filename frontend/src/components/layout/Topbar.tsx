import { Activity, Menu } from 'lucide-react';
import { Badge } from '@/components/ui/Badge';
import { api } from '@/lib/api';
import { useApi } from '@/lib/useApi';

interface TopbarProps {
  onMenuToggle?: () => void;
}

/** System status is derived from GET /health — never hard-coded. */
export function Topbar({ onMenuToggle }: TopbarProps) {
  const { data, loading, error, reload } = useApi(() => api.getHealth(), []);
  const status = loading ? { v: 'neutral' as const, t: 'Checking…' }
    : error ? { v: 'danger' as const, t: 'Backend offline' }
    : data?.status === 'ok' ? { v: 'success' as const, t: 'Backend online' }
    : { v: 'warning' as const, t: 'Backend degraded' };

  return (
    <header className="flex h-16 items-center gap-4 border-b border-navy-700/60 bg-navy-900/80 px-4 backdrop-blur-sm md:px-6">
      <button
        onClick={onMenuToggle}
        className="rounded-md p-2 text-slate-400 hover:bg-navy-700/40 hover:text-slate-200 lg:hidden"
        aria-label="Toggle menu"
      >
        <Menu className="h-5 w-5" />
      </button>

      <div className="hidden text-xs text-slate-500 md:block">
        Synthetic-data demonstrator · traffic values are not live
      </div>

      <div className="ml-auto flex items-center gap-3">
        <button onClick={reload} className="flex items-center gap-2" title="Re-check backend health" aria-label="Backend status">
          <Activity className={`h-4 w-4 ${error ? 'text-rose-400' : 'animate-pulse text-teal-400'}`} />
          <span className="hidden text-xs text-slate-400 md:inline">System status:</span>
          <Badge variant={status.v}>{status.t}</Badge>
        </button>
        <div className="flex items-center gap-2 rounded-lg border border-navy-600/50 bg-navy-800/60 px-3 py-1.5">
          <div className="flex h-7 w-7 items-center justify-center rounded-full bg-gradient-to-br from-cyan-400 to-teal-500 text-xs font-bold text-navy-950">
            BT
          </div>
          <div className="hidden text-left sm:block">
            <p className="text-xs font-semibold text-slate-200">Traffic Admin</p>
            <p className="text-[10px] text-slate-400">Bhubaneswar</p>
          </div>
        </div>
      </div>
    </header>
  );
}
