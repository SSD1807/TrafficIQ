import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useIncidentFeed, type IncidentFeedClient } from '@/lib/useIncidentFeed';
import { ApiError } from '@/lib/api';
import { refreshed, snapshot, tomtomIncident } from '@/test/fixtures';

const live = snapshot('live', [tomtomIncident()]);

function makeClient(over: Partial<IncidentFeedClient> = {}) {
  const client = {
    getIncidents: vi.fn(async () => live),
    refreshIncidents: vi.fn(async () => refreshed(live)),
    ...over,
  };
  return client as typeof client & IncidentFeedClient;
}
const flush = async () => { await act(async () => { await Promise.resolve(); await Promise.resolve(); }); };

describe('useIncidentFeed', () => {
  beforeEach(() => { vi.useFakeTimers(); Object.defineProperty(document, 'hidden', { value: false, configurable: true }); });
  afterEach(() => { vi.useRealTimers(); });

  it('loads the registry then asks the backend for one fresh refresh', async () => {
    const client = makeClient();
    const { result } = renderHook(() => useIncidentFeed({ client }));
    await flush();
    expect(client.getIncidents).toHaveBeenCalledTimes(1);
    expect(client.refreshIncidents).toHaveBeenCalledTimes(1);
    expect(result.current.state?.provider.state).toBe('live');
    expect(result.current.state?.counts.tomtom).toBe(1);
    expect(result.current.loading).toBe(false);
  });

  it('never overlaps requests: a second Refresh now while one is running is ignored', async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => { release = r; });
    const client = makeClient({ refreshIncidents: vi.fn(async () => { await gate; return refreshed(live); }) });
    const { result } = renderHook(() => useIncidentFeed({ client, initialAutoRefresh: false }));
    await flush();
    expect(result.current.refreshing).toBe(true);
    let accepted = true;
    await act(async () => { accepted = await result.current.refreshNow(); });
    expect(accepted).toBe(false);
    expect(client.refreshIncidents).toHaveBeenCalledTimes(1);
    await act(async () => { release(); await gate; });
    await flush();
    expect(result.current.refreshing).toBe(false);
    await act(async () => { await result.current.refreshNow(); });
    expect(client.refreshIncidents).toHaveBeenCalledTimes(2);
  });

  it('auto-refreshes on the interval and does not stack timers while a request is slow', async () => {
    const client = makeClient();
    renderHook(() => useIncidentFeed({ client, initialIntervalSec: 60 }));
    await flush();
    expect(client.refreshIncidents).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(59_000); });
    expect(client.refreshIncidents).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    expect(client.refreshIncidents).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(client.refreshIncidents).toHaveBeenCalledTimes(3);
  });

  it('clamps the interval to the 30 s minimum', async () => {
    const client = makeClient();
    const { result } = renderHook(() => useIncidentFeed({ client, initialIntervalSec: 5 }));
    await flush();
    expect(result.current.intervalSec).toBe(30);
    act(() => result.current.setIntervalSec(1));
    expect(result.current.intervalSec).toBe(30);
  });

  it('turning auto-refresh off stops further requests', async () => {
    const client = makeClient();
    const { result } = renderHook(() => useIncidentFeed({ client, initialIntervalSec: 30 }));
    await flush();
    act(() => result.current.setAutoRefresh(false));
    await act(async () => { await vi.advanceTimersByTimeAsync(300_000); });
    expect(client.refreshIncidents).toHaveBeenCalledTimes(1);
  });

  it('stops all timers when the page unmounts', async () => {
    const client = makeClient();
    const { unmount } = renderHook(() => useIncidentFeed({ client, initialIntervalSec: 30 }));
    await flush();
    unmount();
    await act(async () => { await vi.advanceTimersByTimeAsync(600_000); });
    expect(client.refreshIncidents).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('skips scheduled refreshes while the tab is hidden', async () => {
    const client = makeClient();
    renderHook(() => useIncidentFeed({ client, initialIntervalSec: 30 }));
    await flush();
    Object.defineProperty(document, 'hidden', { value: true, configurable: true });
    await act(async () => { await vi.advanceTimersByTimeAsync(31_000); });
    expect(client.refreshIncidents).toHaveBeenCalledTimes(1);
    Object.defineProperty(document, 'hidden', { value: false, configurable: true });
    await act(async () => { document.dispatchEvent(new Event('visibilitychange')); await Promise.resolve(); });
    expect(client.refreshIncidents).toHaveBeenCalledTimes(2);
  });

  it('keeps the previous data when a later request fails and reports the error', async () => {
    const refresh = vi.fn().mockResolvedValueOnce(refreshed(live)).mockRejectedValueOnce(new ApiError('Backend is down', 503));
    const client = makeClient({ refreshIncidents: refresh });
    const { result } = renderHook(() => useIncidentFeed({ client, initialAutoRefresh: false }));
    await flush();
    await act(async () => { await result.current.refreshNow(); });
    expect(result.current.requestError).toBe('Backend is down');
    expect(result.current.state?.counts.tomtom).toBe(1);
    expect(result.current.refreshing).toBe(false);
  });

  it('ignores a response that arrives after unmount', async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => { release = r; });
    const client = makeClient({ refreshIncidents: vi.fn(async () => { await gate; return refreshed(live); }) });
    const { unmount } = renderHook(() => useIncidentFeed({ client, initialAutoRefresh: false }));
    await flush();
    unmount();
    await act(async () => { release(); await gate; });
    expect(client.refreshIncidents).toHaveBeenCalledTimes(1);
  });
});
