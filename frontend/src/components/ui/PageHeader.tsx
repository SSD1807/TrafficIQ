import type { ReactNode } from 'react';

interface PageHeaderProps {
  title: string;
  description: string;
  icon?: ReactNode;
  action?: ReactNode;
}

export function PageHeader({ title, description, icon, action }: PageHeaderProps) {
  return (
    <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex items-start gap-3">
        {icon && (
          <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-cyan-500/10 text-cyan-400 ring-1 ring-cyan-500/20">
            {icon}
          </div>
        )}
        <div>
          <h1 className="text-xl font-bold text-slate-50 md:text-2xl">{title}</h1>
          <p className="mt-1 text-sm text-slate-400">{description}</p>
        </div>
      </div>
      {action}
    </div>
  );
}
