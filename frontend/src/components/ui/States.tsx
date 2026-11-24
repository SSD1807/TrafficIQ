import { Loader2, AlertCircle, Inbox, RefreshCw } from 'lucide-react';

export function LoadingState({ label = 'Loading from backend…' }: { label?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-12 text-slate-400" role="status">
      <Loader2 className="h-8 w-8 animate-spin text-cyan-400" />
      <p className="text-sm">{label}</p>
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message?: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-10 text-rose-300" role="alert">
      <AlertCircle className="h-8 w-8" />
      <p className="max-w-md text-center text-sm">{message ?? 'Something went wrong. Please try again.'}</p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="inline-flex items-center gap-2 rounded-lg border border-navy-600/50 bg-navy-700/50 px-3 py-1.5 text-xs text-slate-200 hover:bg-navy-600/50"
        >
          <RefreshCw className="h-3.5 w-3.5" /> Retry
        </button>
      )}
    </div>
  );
}

export function EmptyState({ message }: { message?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-12 text-slate-400">
      <Inbox className="h-8 w-8" />
      <p className="text-sm">{message ?? 'No data available.'}</p>
    </div>
  );
}

/** Shown where the backend exposes no endpoint for something the original design displayed. */
export function UnavailableNote({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-navy-600/60 bg-navy-800/30 px-4 py-3 text-xs text-slate-400">
      <span className="font-semibold text-slate-300">Not available from the backend. </span>
      {children}
    </div>
  );
}
