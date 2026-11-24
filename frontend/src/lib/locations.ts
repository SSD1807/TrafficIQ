import anchors from '@backend-config/location_coordinates.json';

/**
 * The 10 traffic locations in the dataset are SYNTHETIC archetypes. The backend ships approximate
 * map anchors for them in backend/config/location_coordinates.json (imported here, not copied).
 * They are demo positions, NOT surveyed sensor locations.
 */
export interface LocationAnchor { lat: number; lon: number; area: string }

const table = anchors as unknown as Record<string, LocationAnchor | string>;

export function getAnchor(location: string): LocationAnchor | null {
  const a = table[location];
  return a && typeof a === 'object' ? a : null;
}
