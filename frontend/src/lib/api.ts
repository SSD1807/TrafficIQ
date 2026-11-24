import type {
  AllPredictionsResponse, CopilotResponse, CurrentTrafficResponse, ForecastResult, HealthResponse,
  IncidentCreate, IncidentImpactResponse, IncidentProviderStatus, IncidentRecord, IncidentRefreshResponse, IncidentsResponse, LocationsResponse, ModelEvaluationResponse,
  NetworkStatus, PlaceMatch, PredictionRequest, PredictionResult, RegressorFamily, RoadsResponse, RouteCalculationRequest, RoutePlanRequest, RoutePlanResponse,
  RouteCalculationResponse, RoutingNodesResponse, TomTomFlowResponse, TrafficFlowResponse, TrafficProviderStatus,
  WeatherResponse,
} from '@/types';

// The ONLY place the backend URL is read. Never put TomTom/OpenAI keys in VITE_* variables (they are public in the bundle).
export const BASE_URL: string = (import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000').replace(/\/$/, '');
/** Traffic tile layer is proxied by the backend, so the provider API key never reaches the browser. */
export const TRAFFIC_TILE_URL = `${BASE_URL}/traffic/tiles/{z}/{x}/{y}.png`;
const DEFAULT_TIMEOUT_MS = 20_000;
// First routing call with no cached OSM graph makes the backend try an OSM download; allow it time.
const ROUTING_TIMEOUT_MS = 240_000;

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

/** FastAPI errors: {detail: string} or {detail: [{msg, loc}]} (422). */
function detailText(body: unknown): string | null {
  if (!body || typeof body !== 'object') return null;
  const d = (body as { detail?: unknown }).detail;
  if (typeof d === 'string') return d;
  // Structured errors from the shared engine: {code, message, reason?, hint?, suggestions?}
  if (d && typeof d === 'object' && !Array.isArray(d) && 'message' in d) {
    const o = d as { message: string; reason?: string; hint?: string; suggestions?: string[] };
    return [o.message, o.reason, o.suggestions?.length ? `Did you mean: ${o.suggestions.join(', ')}?` : undefined, o.hint].filter(Boolean).join(' ');
  }
  if (Array.isArray(d)) {
    return d.map((x) => (x && typeof x === 'object' && 'msg' in x ? String((x as { msg: unknown }).msg) : String(x))).join('; ');
  }
  return null;
}

async function request<T>(path: string, init?: RequestInit, timeoutMs = DEFAULT_TIMEOUT_MS): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${BASE_URL}${path}`, {
      ...init,
      signal: controller.signal,
      headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    });
    if (!res.ok) {
      let detail: string | null = null;
      try { detail = detailText(await res.json()); } catch { /* non-JSON error body */ }
      throw new ApiError(detail ?? `Request to ${path} failed with status ${res.status}.`, res.status);
    }
    return (await res.json()) as T;
  } catch (e) {
    if (e instanceof ApiError) throw e;
    if (e instanceof DOMException && e.name === 'AbortError') {
      throw new ApiError(`Request to ${path} timed out after ${Math.round(timeoutMs / 1000)} s.`, 0);
    }
    throw new ApiError(`Could not reach the backend at ${BASE_URL}. Is FastAPI running?`, 0);
  } finally {
    clearTimeout(timer);
  }
}

const qs = (params: Record<string, string | number | boolean | undefined>) => {
  const p = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => v !== undefined && p.set(k, String(v)));
  const s = p.toString();
  return s ? `?${s}` : '';
};

export type { RoutingAlgorithm, RouteCalculationRequest } from '@/types';

export const api = {
  getHealth: () => request<HealthResponse>('/health'),
  getLocations: () => request<LocationsResponse>('/locations'),
  getCurrentTraffic: () => request<CurrentTrafficResponse>('/traffic/current'),
  getAllPredictions: () => request<AllPredictionsResponse>('/traffic/predictions'),
  predict: (location: string, body: PredictionRequest = {}) =>
    request<PredictionResult>(`/traffic/predict/${encodeURIComponent(location)}`, { method: 'POST', body: JSON.stringify(body) }),
  getForecast: (location: string, horizon: 30 | 60, model: RegressorFamily = 'random_forest') =>
    request<ForecastResult>(`/forecasts/${encodeURIComponent(location)}${qs({ horizon_minutes: horizon, model })}`),

  getRoutingNodes: (limit = 250) =>
    request<RoutingNodesResponse>(`/routing/nodes${qs({ limit })}`, undefined, ROUTING_TIMEOUT_MS),
  calculateRoutes: (body: RouteCalculationRequest) =>
    request<RouteCalculationResponse>('/routes/calculate', { method: 'POST', body: JSON.stringify(body) }, ROUTING_TIMEOUT_MS),

  getNetworkStatus: () => request<NetworkStatus>('/network/status', undefined, 60_000),
  getNetworkRoads: (level: 'major' | 'all' = 'major', limit = 6000) =>
    request<RoadsResponse>(`/network/roads${qs({ level, limit })}`, undefined, 60_000),
  searchLocation: (q: string) => request<PlaceMatch>(`/locations/search${qs({ q })}`, undefined, 60_000),
  /** Incident-aware planning on the shared backend engine (normal route -> live incidents -> impact -> alternatives -> decision). */
  planRoute: (body: RoutePlanRequest) =>
    request<RoutePlanResponse>('/routes/plan', { method: 'POST', body: JSON.stringify(body) }, ROUTING_TIMEOUT_MS),

  getIncidents: () => request<IncidentsResponse>('/incidents'),
  createIncident: (body: IncidentCreate) =>
    request<IncidentRecord>('/incidents', { method: 'POST', body: JSON.stringify(body) }),
  deleteIncident: (id: string) =>
    request<{ removed: boolean; incident_id: string }>(`/incidents/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  /** Clears manual + simulated incidents (optionally one source). TomTom incidents are provider-managed and never cleared. */
  clearIncidents: (source?: 'manual' | 'simulation') =>
    request<{ removed_count: number }>(`/incidents${qs({ source })}`, { method: 'DELETE' }),
  /** Asks the backend to fetch TomTom incidents now (rate limited server-side). The API key never leaves the backend. */
  refreshIncidents: () => request<IncidentRefreshResponse>('/incidents/refresh', { method: 'POST' }, ROUTING_TIMEOUT_MS),
  getIncidentProviderStatus: () => request<IncidentProviderStatus>('/traffic/tomtom/incidents/status'),
  getIncidentImpact: (body: RouteCalculationRequest) =>
    request<IncidentImpactResponse>('/routes/incident-impact', { method: 'POST', body: JSON.stringify(body) }, ROUTING_TIMEOUT_MS),

  getWeather: (opts: { allowSimulatedFallback?: boolean; forceRefresh?: boolean } = {}) =>
    request<WeatherResponse>(
      `/weather/current${qs({ allow_simulated_fallback: opts.allowSimulatedFallback, force_refresh: opts.forceRefresh })}`,
      undefined, 30_000,
    ),
  getTrafficProviderStatus: () => request<TrafficProviderStatus>('/traffic/provider/status'),
  getTrafficFlow: () => request<TrafficFlowResponse>('/traffic/flow', undefined, 30_000),
  getTomTomFlow: () => request<TomTomFlowResponse>('/traffic/tomtom/flow', undefined, 30_000),

  copilotChat: (message: string) =>
    request<CopilotResponse>('/copilot/chat', { method: 'POST', body: JSON.stringify({ message }) }, 90_000),

  getModelEvaluation: () => request<ModelEvaluationResponse>('/models/evaluation'),
};
