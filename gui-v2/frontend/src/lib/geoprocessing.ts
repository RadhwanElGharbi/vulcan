/**
 * Geodesic and planar measurement utilities for GIS geoprocessing tools.
 *
 * Implements Haversine distance, spherical-excess area, and planar
 * equivalents projected via equirectangular approximation – matching the
 * behaviour expected from professional GIS measurement tools.
 */

const EARTH_RADIUS = 6371008.8 // metres – WGS-84 mean radius

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function toRad(deg: number): number {
  return (deg * Math.PI) / 180
}

// ---------------------------------------------------------------------------
// Distance
// ---------------------------------------------------------------------------

/**
 * Haversine (great-circle) distance between two WGS-84 points.
 * Returns metres.
 */
export function geodesicDistance(
  lon1: number,
  lat1: number,
  lon2: number,
  lat2: number
): number {
  const dLat = toRad(lat2 - lat1)
  const dLon = toRad(lon2 - lon1)
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2
  return 2 * EARTH_RADIUS * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a))
}

/**
 * Planar distance between two WGS-84 points projected via equirectangular
 * approximation (cosine correction at the midpoint latitude).  Returns metres.
 */
export function planarDistance(
  lon1: number,
  lat1: number,
  lon2: number,
  lat2: number
): number {
  const midLatRad = toRad((lat1 + lat2) / 2)
  const mPerDegLon = (EARTH_RADIUS * Math.PI) / 180 * Math.cos(midLatRad)
  const mPerDegLat = (EARTH_RADIUS * Math.PI) / 180
  const dx = (lon2 - lon1) * mPerDegLon
  const dy = (lat2 - lat1) * mPerDegLat
  return Math.sqrt(dx * dx + dy * dy)
}

// ---------------------------------------------------------------------------
// Polyline length
// ---------------------------------------------------------------------------

export function polylineLength(
  points: [number, number][],
  mode: 'geodesic' | 'planar'
): number {
  let total = 0
  for (let i = 1; i < points.length; i++) {
    const [lon1, lat1] = points[i - 1]
    const [lon2, lat2] = points[i]
    total +=
      mode === 'geodesic'
        ? geodesicDistance(lon1, lat1, lon2, lat2)
        : planarDistance(lon1, lat1, lon2, lat2)
  }
  return total
}

export function segmentDistances(
  points: [number, number][],
  mode: 'geodesic' | 'planar'
): number[] {
  const distances: number[] = []
  for (let i = 1; i < points.length; i++) {
    const [lon1, lat1] = points[i - 1]
    const [lon2, lat2] = points[i]
    distances.push(
      mode === 'geodesic'
        ? geodesicDistance(lon1, lat1, lon2, lat2)
        : planarDistance(lon1, lat1, lon2, lat2)
    )
  }
  return distances
}

// ---------------------------------------------------------------------------
// Area
// ---------------------------------------------------------------------------

/**
 * Geodesic (spherical) area of a closed polygon ring using the trapezoidal
 * spherical-excess formula.  `ring` must be an array of [lng, lat] with the
 * first element duplicated at the end (GeoJSON convention).
 *
 * Returns absolute area in square metres.
 */
export function geodesicArea(ring: [number, number][]): number {
  const n = ring.length
  if (n < 4) return 0 // at least 3 unique vertices + closing vertex

  let total = 0
  for (let i = 0; i < n - 1; i++) {
    const j = (i + 1) % (n - 1)
    total +=
      toRad(ring[j][0] - ring[i][0]) *
      (2 + Math.sin(toRad(ring[i][1])) + Math.sin(toRad(ring[j][1])))
  }

  return Math.abs((total * EARTH_RADIUS * EARTH_RADIUS) / 2)
}

/**
 * Planar area of a polygon ring using the Shoelace formula after projecting
 * via equirectangular approximation.  `ring` must be [lng, lat] pairs with
 * the first duplicated at the end.
 *
 * Returns absolute area in square metres.
 */
export function planarArea(ring: [number, number][]): number {
  if (ring.length < 4) return 0

  const centroidLat = ring.reduce((s, p) => s + p[1], 0) / ring.length
  const cosLat = Math.cos(toRad(centroidLat))
  const mPerDegLon = ((EARTH_RADIUS * Math.PI) / 180) * cosLat
  const mPerDegLat = (EARTH_RADIUS * Math.PI) / 180

  const projected = ring.map(
    ([lon, lat]) => [lon * mPerDegLon, lat * mPerDegLat] as [number, number]
  )

  let area = 0
  for (let i = 0, j = projected.length - 1; i < projected.length; j = i++) {
    area +=
      (projected[j][0] + projected[i][0]) *
      (projected[j][1] - projected[i][1])
  }
  return Math.abs(area / 2)
}

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

export function formatDistance(metres: number): string {
  if (metres >= 1000) return `${(metres / 1000).toFixed(3)} km`
  return `${metres.toFixed(2)} m`
}

export function formatArea(sqMetres: number): string {
  if (sqMetres >= 1_000_000) return `${(sqMetres / 1_000_000).toFixed(4)} km\u00B2`
  if (sqMetres >= 10_000) return `${(sqMetres / 10_000).toFixed(4)} ha`
  return `${sqMetres.toFixed(2)} m\u00B2`
}

// ---------------------------------------------------------------------------
// Elevation profile sampling
// ---------------------------------------------------------------------------

export interface ElevationSample {
  distance: number
  elevation: number | null
  lng: number
  lat: number
}

/**
 * Sample elevation along a polyline at approximately `numSamples` evenly
 * spaced locations using a provided sampler.
 */
export async function sampleElevationProfile(
  points: [number, number][],
  sampler: { sample: (lng: number, lat: number, zoom: number) => Promise<number | null> },
  zoom: number,
  numSamples: number = 100
): Promise<ElevationSample[]> {
  if (points.length < 2) return []

  // Build cumulative geodesic distance array
  const cumDist: number[] = [0]
  for (let i = 1; i < points.length; i++) {
    cumDist.push(
      cumDist[i - 1] +
        geodesicDistance(points[i - 1][0], points[i - 1][1], points[i][0], points[i][1])
    )
  }
  const totalDist = cumDist[cumDist.length - 1]
  if (totalDist === 0) return []

  const results: ElevationSample[] = []

  for (let s = 0; s <= numSamples; s++) {
    const targetDist = (s / numSamples) * totalDist

    // Find the segment that contains this distance
    let segIdx = 0
    for (let i = 1; i < cumDist.length; i++) {
      if (cumDist[i] >= targetDist) {
        segIdx = i - 1
        break
      }
    }

    const segStart = cumDist[segIdx]
    const segEnd = cumDist[segIdx + 1] ?? cumDist[segIdx]
    const segLen = segEnd - segStart
    const t = segLen > 0 ? (targetDist - segStart) / segLen : 0

    const lng = points[segIdx][0] + t * ((points[segIdx + 1]?.[0] ?? points[segIdx][0]) - points[segIdx][0])
    const lat = points[segIdx][1] + t * ((points[segIdx + 1]?.[1] ?? points[segIdx][1]) - points[segIdx][1])

    const elevation = await sampler.sample(lng, lat, zoom)
    results.push({ distance: targetDist, elevation, lng, lat })
  }

  return results
}

