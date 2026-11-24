import { NavLink } from 'react-router-dom';
import {
  LayoutDashboard,
  Map as MapIcon,
  TrendingUp,
  LineChart,
  Navigation,
  Siren,
  CloudRain,
  Bot,
  Gauge,
  ChevronLeft,
  TrafficCone,
} from 'lucide-react';
import { cn } from '@/lib/utils';

const navItems = [
  { to: '/', label: 'Executive Overview', icon: LayoutDashboard, end: true },
  { to: '/traffic-map', label: 'Traffic Map', icon: MapIcon },
  { to: '/prediction', label: 'Traffic Prediction', icon: TrendingUp },
  { to: '/forecasting', label: 'Forecasting Analytics', icon: LineChart },
  { to: '/route-planner', label: 'Smart Route Planner', icon: Navigation },
  { to: '/incidents', label: 'Incident Management', icon: Siren },
  { to: '/weather', label: 'Weather Intelligence', icon: CloudRain },
  { to: '/copilot', label: 'AI Traffic Copilot', icon: Bot },
  { to: '/model-evaluation', label: 'Model Evaluation', icon: Gauge },
];

interface SidebarProps {
  collapsed: boolean;
  onToggle: () => void;
}

export function Sidebar({ collapsed, onToggle }: SidebarProps) {
  return (
    <aside
      className={cn(
        'flex flex-col border-r border-navy-700/60 bg-navy-900/90 backdrop-blur-sm transition-all duration-300',
        collapsed ? 'w-[68px]' : 'w-64'
      )}
    >
      <div className="flex h-16 items-center gap-3 border-b border-navy-700/60 px-4">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-cyan-400 to-teal-500 text-navy-950 shadow-glow">
          <TrafficCone className="h-5 w-5" />
        </div>
        {!collapsed && (
          <div className="overflow-hidden">
            <p className="truncate text-sm font-bold text-slate-100">TrafficIQ</p>
            <p className="truncate text-[10px] text-slate-400">Bhubaneswar Smart Routing</p>
          </div>
        )}
        <button
          onClick={onToggle}
          className={cn(
            'ml-auto rounded-md p-1.5 text-slate-400 transition-colors hover:bg-navy-700/40 hover:text-slate-200',
            collapsed && 'absolute top-4 right-2'
          )}
          aria-label="Toggle sidebar"
        >
          <ChevronLeft className={cn('h-4 w-4 transition-transform', collapsed && 'rotate-180')} />
        </button>
      </div>

      <nav className="flex-1 space-y-1 overflow-y-auto p-2">
        {navItems.map((item) => {
          const Icon = item.icon;
          return (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                cn(
                  'group flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-all',
                  isActive
                    ? 'bg-cyan-500/10 text-cyan-300 shadow-glow'
                    : 'text-slate-400 hover:bg-navy-700/30 hover:text-slate-200',
                  collapsed && 'justify-center px-2'
                )
              }
            >
              <Icon className="h-5 w-5 shrink-0" />
              {!collapsed && <span className="truncate">{item.label}</span>}
              {collapsed && (
                <span className="pointer-events-none absolute left-16 z-50 whitespace-nowrap rounded-md bg-navy-800 px-2.5 py-1.5 text-xs text-slate-200 opacity-0 shadow-lg transition-opacity group-hover:opacity-100">
                  {item.label}
                </span>
              )}
            </NavLink>
          );
        })}
      </nav>

      {!collapsed && (
        <div className="border-t border-navy-700/60 p-3">
          <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 px-3 py-2 text-[10px] text-amber-200/80">
            Synthetic-data demonstrator · FastAPI backend
          </div>
        </div>
      )}
    </aside>
  );
}
