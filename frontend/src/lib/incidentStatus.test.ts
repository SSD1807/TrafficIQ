import { describe, expect, it } from 'vitest';
import { clampPollSeconds, describeFeedStatus, describeProviderState, geometryToLatLon, impactColor, locationText, MIN_POLL_SECONDS, severityText } from '@/lib/incidentStatus';
import { localIncident, tomtomIncident } from '@/test/fixtures';
import type { IncidentProviderState } from '@/types';

describe('polling interval', () => {
  it('never goes below the safe minimum and rejects bad values', () => {
    expect(MIN_POLL_SECONDS).toBe(30);
    expect(clampPollSeconds(5)).toBe(30);
    expect(clampPollSeconds(60)).toBe(60);
    expect(clampPollSeconds(119.6)).toBe(120);
    expect(clampPollSeconds('abc')).toBe(60);
    expect(clampPollSeconds(undefined)).toBe(60);
    expect(clampPollSeconds(-10)).toBe(60);
  });
});

describe('provider state wording', () => {
  const cases: [IncidentProviderState, boolean, string][] = [
    ['live', true, 'Live'], ['live_empty', true, 'no incidents'], ['cached', false, 'not live'], ['disabled_no_key', false, 'no TomTom API key'],
    ['disabled', false, 'Disabled'], ['unavailable', false, 'unavailable'], ['idle', false, 'Waiting'],
  ];
  it.each(cases)('%s -> live=%s', (state, live, text) => {
    const d = describeProviderState(state);
    expect(d.live).toBe(live);
    expect(d.label).toContain(text);
  });
  it('only live states are ever presented as live', () => {
    const states: IncidentProviderState[] = ['live', 'live_empty', 'cached', 'disabled_no_key', 'disabled', 'unavailable', 'idle'];
    expect(states.filter((s) => describeProviderState(s).live)).toEqual(['live', 'live_empty']);
  });
});

describe('feed helpers', () => {
  it('converts GeoJSON [lon, lat] to Leaflet [lat, lon]', () => {
    expect(geometryToLatLon({ type: 'LineString', coordinates: [[85.8, 20.2], [85.9, 20.3]] })).toEqual([[20.2, 85.8], [20.3, 85.9]]);
    expect(geometryToLatLon({ type: 'Point', coordinates: [85.8, 20.2] })).toEqual([[20.2, 85.8]]);
    expect(geometryToLatLon(null)).toEqual([]);
    expect(geometryToLatLon({ type: 'Point', coordinates: [NaN, 1] })).toEqual([]);
  });
  it('describes severity honestly', () => {
    expect(severityText(tomtomIncident())).toContain('from provider');
    expect(severityText(tomtomIncident({ severity_source: 'default_for_kind', severity: 0.5 }))).toContain('not provided by TomTom');
    expect(severityText(tomtomIncident({ severity: null }))).toBe('severity not provided');
    expect(severityText(localIncident('manual'))).toBe('severity 0.70');
  });
  it('describes the location from matched roads, from/to and road numbers', () => {
    expect(locationText(tomtomIncident())).toBe('NH16 Service Road between Jaydev Vihar and Nandankanan Road (NH16)');
    expect(locationText(tomtomIncident({ edges: [], provider: null }))).toBe('20.2950, 85.8250');
  });
  it('labels unmatched incidents and explains why', () => {
    const d = describeFeedStatus(tomtomIncident({ status: 'unmatched', match: { status: 'unmatched', confidence: 0, reason: 'no_nearby_road', reason_text: 'No road close enough.', direction_used: false } }));
    expect(d.label).toBe('Not matched to a road');
    expect(d.detail).toBe('No road close enough.');
    expect(describeFeedStatus(tomtomIncident()).label).toBe('Applied to routing');
  });
});

describe('shared impact helpers', () => {
  it('uses the backend location label only when no matched road name exists', () => {
    expect(locationText({ ...tomtomIncident({ edges: [], provider: null, latitude: null, longitude: null }), location_label: 'Near Raj Path' })).toBe('Near Raj Path');
    expect(locationText({ ...tomtomIncident(), location_label: 'ignored' })).toContain('NH16 Service Road');
  });
  it('colours closures red, accidents orange and construction yellow, with a fallback for unknown classes', () => {
    expect(impactColor('confirmed_closure')).toBe('#dc2626');
    expect(impactColor('major_accident')).toBe('#f97316');
    expect(impactColor('construction')).toBe('#eab308');
    expect(impactColor(undefined, '#f43f5e')).toBe('#f43f5e');
  });
});
