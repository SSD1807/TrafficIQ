import { useEffect, useMemo, useState } from 'react';
import type { MapLine } from '@/components/map/TrafficMap';
import { api } from '@/lib/api';
import type { RoadsResponse } from '@/types';

export interface RoadLayerState {
  lines: MapLine[];
  status: 'loading' | 'ready' | 'unavailable';
  info: Pick<RoadsResponse, 'graph_source' | 'is_real_road_geometry' | 'count' | 'total_matching' | 'truncated'> | null;
}

/**
 * The real OSM road geometry of the loaded network (from GET /network/roads), as a faint context layer for maps.
 * It is optional: if the backend cannot provide it (e.g. no road network), the map simply has no layer and `status` says why.
 */
export function useRoadLayer(level: 'major' | 'all' = 'major'): RoadLayerState {
  const [data, setData] = useState<RoadsResponse | null>(null);
  const [status, setStatus] = useState<RoadLayerState['status']>('loading');

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await api.getNetworkRoads(level);
        if (!cancelled) { setData(r); setStatus('ready'); }
      } catch {
        if (!cancelled) { setData(null); setStatus('unavailable'); }
      }
    })();
    return () => { cancelled = true; };
  }, [level]);

  const lines = useMemo<MapLine[]>(
    () => (data?.segments ?? []).map((s) => ({ positions: s.positions, color: '#64748b', weight: s.highway.startsWith('motorway') || s.highway.startsWith('trunk') || s.highway.startsWith('primary') ? 2.5 : 1.5, opacity: 0.55 })),
    [data],
  );
  const info = data ? { graph_source: data.graph_source, is_real_road_geometry: data.is_real_road_geometry, count: data.count, total_matching: data.total_matching, truncated: data.truncated } : null;
  return { lines, status, info };
}
