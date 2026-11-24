import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError } from '@/lib/api';
import { clampPollSeconds, DEFAULT_POLL_SECONDS } from '@/lib/incidentStatus';
import type { FeedIncident, IncidentCounts, IncidentProviderStatus, IncidentRefreshResponse, IncidentsResponse } from '@/types';

export interface IncidentFeedState {
  feed: FeedIncident[];
  counts: IncidentCounts;
  provider: IncidentProviderStatus;
}
export interface IncidentFeedClient {
  getIncidents: () => Promise<IncidentsResponse>;
  refreshIncidents: () => Promise<IncidentRefreshResponse>;
}

const errText = (e: unknown) => (e instanceof ApiError ? e.message : 'Could not reach the backend.');

/**
 * Loads the incident feed and (optionally) keeps it fresh.
 *  - Never overlaps requests: one in flight at a time; the next timer is only armed after the previous call finished.
 *  - Skips scheduled refreshes while the tab is hidden.
 *  - Stops every timer on unmount; late responses after unmount are ignored.
 *  - A failed backend call keeps the previous data on screen (and reports the error); it never blanks the feed.
 *  - Real TomTom calls are rate limited again on the server, so extra clicks or tabs cannot burn the quota.
 */
export function useIncidentFeed(opts: { client?: IncidentFeedClient; initialAutoRefresh?: boolean; initialIntervalSec?: number } = {}) {
  const client = opts.client ?? api;
  const [state, setState] = useState<IncidentFeedState | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [requestError, setRequestError] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(opts.initialAutoRefresh ?? true);
  const [intervalSec, setIntervalSecRaw] = useState(clampPollSeconds(opts.initialIntervalSec ?? DEFAULT_POLL_SECONDS));
  const inFlight = useRef(false);
  const queuedReload = useRef(false);
  const mounted = useRef(true);
  const lastAttempt = useRef(0);

  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  const reload = useCallback(async () => {
    if (inFlight.current) { queuedReload.current = true; return; }
    inFlight.current = true;
    try {
      const r = await client.getIncidents();
      if (mounted.current) { setState({ feed: r.feed, counts: r.counts, provider: r.provider }); setRequestError(null); }
    } catch (e) {
      if (mounted.current) setRequestError(errText(e));
    } finally {
      inFlight.current = false;
      if (mounted.current) setLoading(false);
      if (queuedReload.current && mounted.current) { queuedReload.current = false; void reload(); }
    }
  }, [client]);

  /** Asks the backend to fetch TomTom now. Returns false when a request was already running (no overlap). */
  const refreshNow = useCallback(async (): Promise<boolean> => {
    if (inFlight.current) return false;
    inFlight.current = true;
    lastAttempt.current = Date.now();
    if (mounted.current) setRefreshing(true);
    try {
      const r = await client.refreshIncidents();
      if (mounted.current) { setState({ feed: r.feed, counts: r.counts, provider: r.provider }); setRequestError(null); }
    } catch (e) {
      if (mounted.current) setRequestError(errText(e));
    } finally {
      inFlight.current = false;
      if (mounted.current) { setRefreshing(false); setLoading(false); }
      if (queuedReload.current && mounted.current) { queuedReload.current = false; void reload(); }
    }
    return true;
  }, [client, reload]);

  // Initial load: show what the backend already holds, then ask for a fresh TomTom fetch (server throttled).
  useEffect(() => {
    let cancelled = false;
    (async () => { await reload(); if (!cancelled) await refreshNow(); })();
    return () => { cancelled = true; };
  }, [reload, refreshNow]);

  // Auto-refresh: a timeout chain (not setInterval) so a slow request can never overlap the next one.
  useEffect(() => {
    if (!autoRefresh) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let cancelled = false;
    const tick = async () => {
      if (cancelled) return;
      if (typeof document === 'undefined' || !document.hidden) await refreshNow();
      if (!cancelled) timer = setTimeout(tick, intervalSec * 1000);
    };
    timer = setTimeout(tick, intervalSec * 1000);
    const onVisible = () => {
      if (!document.hidden && Date.now() - lastAttempt.current >= intervalSec * 1000) void refreshNow();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => { cancelled = true; if (timer) clearTimeout(timer); document.removeEventListener('visibilitychange', onVisible); };
  }, [autoRefresh, intervalSec, refreshNow]);

  const setIntervalSec = useCallback((v: number) => setIntervalSecRaw(clampPollSeconds(v)), []);

  return { state, loading, refreshing, requestError, autoRefresh, setAutoRefresh, intervalSec, setIntervalSec, refreshNow, reload };
}
