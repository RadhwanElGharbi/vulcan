import type { GeoJSON } from '@/lib/api/dataClient'

export type ManagedLayer = {
  id: string
  name: string
  type: 'vector' | 'raster'
  status: 'loading' | 'ready' | 'error'
  message?: string
  sourceId: string
  layerIds: string[]
  visible: boolean
  opacity: number
  order: number
  path?: string
  metadata?: any
  geometryType?: string
  featureCount?: number
  isAoi?: boolean
  bounds?: LngLatBounds
}

export type VectorDetail = {
  properties: string[]
  sample: Record<string, any>[]
  rows: Record<string, any>[]
  features: any[]
  sortedCache?: {
    column: string
    direction: 'asc' | 'desc'
    pairs: { row: Record<string, any>; feature: any }[]
  }
}

export type LineStyle = 'solid' | 'dashed' | 'dotted' | 'dash-dot' | 'long-dash' | 'short-dash'

export type LayerStyleOptions = {
  fillColor?: string
  lineColor?: string
  lineWidth?: number
  lineStyle?: LineStyle
  lineDasharray?: number[]
  pointColor?: string
  pointSize?: number
  opacity?: number
}

// Line dash patterns in PIXELS (fixed screen distance)
// These are the target pixel sizes - will be divided by line width when applied
export const LINE_STYLE_PATTERNS_PX: Record<LineStyle, number[]> = {
  'solid': [],
  'dashed': [12, 8],      // 12px dash, 8px gap
  'dotted': [2, 6],       // 2px dot, 6px gap
  'dash-dot': [12, 6, 2, 6], // 12px dash, 6px gap, 2px dot, 6px gap
  'long-dash': [20, 10],  // 20px dash, 10px gap
  'short-dash': [6, 6],   // 6px dash, 6px gap
}

// Helper to get dasharray values adjusted for line width (for fixed pixel appearance)
export function getDasharrayForWidth(style: LineStyle, lineWidth: number): number[] {
  const pattern = LINE_STYLE_PATTERNS_PX[style]
  if (pattern.length === 0) return []
  // MapLibre dasharray values are multiplied by line width, so divide to get fixed pixels
  return pattern.map(v => v / lineWidth)
}

// Legacy export for backwards compatibility
export const LINE_STYLE_PATTERNS = LINE_STYLE_PATTERNS_PX

export type LngLatBounds = [[number, number], [number, number]]

export const AOI_LAYER_HINTS = ['aoi', 'area_of_interest']
export const SAMPLE_ROWS = 25
export const COLOR_PALETTE = ['#22d3ee', '#fb7185', '#a78bfa', '#f97316', '#22c55e', '#eab308', '#38bdf8']

// Default colors for vector dataset categories
export const CATEGORY_COLORS: Record<string, string> = {
  roads: '#f97316',      // Orange
  railways: '#22c55e',   // Green
  powerlines: '#eab308', // Yellow
  waterways: '#06b6d4',  // Cyan
  pipelines: '#ef4444',  // Red
}

// Keywords to match layer names to categories
// Note: Avoid generic keywords like "street" which match "OpenStreetMap"
const CATEGORY_KEYWORDS: Record<string, string[]> = {
  roads: ['road', 'highway', 'osm_road', 'roads', '_road_'],
  railways: ['rail', 'railway', 'train', 'osm_rail', 'railways'],
  powerlines: ['power', 'powerline', 'transmission', 'electric', 'grid', 'osm_power'],
  waterways: ['water', 'waterway', 'river', 'stream', 'hydro', 'osm_water'],
  pipelines: ['pipeline', 'pipe', 'gas_pipeline', 'oil_pipeline', 'osm_pipeline'],
}

export function colorForLayer(name: string): string {
  const nameLower = name.toLowerCase()

  // Check for category-specific colors based on keywords
  for (const [category, keywords] of Object.entries(CATEGORY_KEYWORDS)) {
    for (const keyword of keywords) {
      if (nameLower.includes(keyword)) {
        return CATEGORY_COLORS[category]
      }
    }
  }

  // Fallback to hash-based color for other layers
  const hash = name.split('').reduce((acc, char) => acc + char.charCodeAt(0), 0)
  return COLOR_PALETTE[hash % COLOR_PALETTE.length]
}

export function getGeoJSONBounds(geojson: GeoJSON): [[number, number], [number, number]] | null {
  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity

  const updateBounds = (coords: number[]) => {
    if (!Array.isArray(coords) || coords.length < 2) return
    const [x, y] = coords
    if (typeof x !== 'number' || typeof y !== 'number') return
    minX = Math.min(minX, x)
    minY = Math.min(minY, y)
    maxX = Math.max(maxX, x)
    maxY = Math.max(maxY, y)
  }

  const processCoordinates = (coords: any) => {
    if (!coords) return
    if (typeof coords[0] === 'number') {
      updateBounds(coords)
    } else {
      coords.forEach(processCoordinates)
    }
  }

  const processGeometry = (geometry: any) => {
    if (!geometry) return
    switch (geometry.type) {
      case 'Point':
      case 'LineString':
      case 'MultiPoint':
      case 'MultiLineString':
      case 'Polygon':
      case 'MultiPolygon':
        processCoordinates(geometry.coordinates)
        break
      case 'GeometryCollection':
        geometry.geometries?.forEach(processGeometry)
        break
      default:
        break
    }
  }

  if ((geojson as any).type === 'FeatureCollection') {
    (geojson as any).features?.forEach((feature: any) => processGeometry(feature.geometry))
  } else if ((geojson as any).type === 'Feature') {
    processGeometry((geojson as any).geometry)
  } else {
    processGeometry(geojson)
  }

  if (minX === Infinity) {
    return null
  }

  return [
    [minX, minY],
    [maxX, maxY]
  ]
}

export function inferGeometryType(geojson: GeoJSON): 'polygon' | 'line' | 'point' | 'mixed' {
  const features = (geojson as any).features || []
  let hasPolygon = false
  let hasLine = false
  let hasPoint = false

  for (const feature of features) {
    const geomType = feature?.geometry?.type
    if (!geomType) continue
    if (geomType.includes('Polygon')) hasPolygon = true
    if (geomType.includes('Line')) hasLine = true
    if (geomType.includes('Point')) hasPoint = true
  }

  const flags = [hasPolygon, hasLine, hasPoint].filter(Boolean).length
  if (flags > 1) return 'mixed'
  if (hasPolygon) return 'polygon'
  if (hasLine) return 'line'
  if (hasPoint) return 'point'
  return 'mixed'
}

export function buildPropertySummary(geojson: GeoJSON): VectorDetail {
  const features = (geojson as any).features || []
  const propertiesSet = new Set<string>()
  const sample: Record<string, any>[] = []

  for (const feature of features.slice(0, SAMPLE_ROWS)) {
    const props = feature?.properties || {}
    Object.keys(props || {}).forEach(key => propertiesSet.add(key))
    sample.push(props)
  }

  return {
    properties: Array.from(propertiesSet),
    sample,
    rows: [], // Will be filled later if needed
    features: [] // Will be filled later if needed
  }
}

export function getRasterBounds(metadata: any | undefined): LngLatBounds | null {
  if (!metadata) return null
  const value = metadata.bbox_wgs84
  const bbox = Array.isArray(value) && value.length === 4
    ? { west: value[0], south: value[1], east: value[2], north: value[3] } : value
  if (
    bbox &&
    [bbox.west, bbox.south, bbox.east, bbox.north].every((v: any) => typeof v === 'number' && Number.isFinite(v))
  ) {
    return [
      [bbox.west, bbox.south],
      [bbox.east, bbox.north]
    ]
  }
  return null
}

export function formatMetadata(metadata: any | undefined): { label: string, value: string }[] {
  if (!metadata || typeof metadata !== 'object') return []
  const rows: { label: string, value: string }[] = []

  const num = (val: any, digits = 2) => (typeof val === 'number' ? Number(val).toFixed(digits) : val)
  const asMB = (bytes: any) => (typeof bytes === 'number' ? `${(bytes / (1024 * 1024)).toFixed(2)} MB` : bytes)

  const name = metadata.dataset_name || metadata.name
  if (name) rows.push({ label: 'Dataset', value: name })

  if (metadata.format) rows.push({ label: 'Format', value: metadata.format })

  const type = metadata.data_type || metadata.type
  if (type) rows.push({ label: 'Type', value: type })

  const crsName = metadata.target_crs_name || metadata.crs_name
  const crsCode = metadata.target_crs || metadata.crs
  if (crsName || crsCode) {
    rows.push({
      label: 'CRS',
      value: `${crsName ?? ''}${crsCode ? ` (${crsCode})` : ''}`.trim()
    })
  }

  if (metadata.resolution_m) rows.push({ label: 'Resolution', value: `${metadata.resolution_m} m` })
  if (metadata.file_size_bytes) rows.push({ label: 'Size', value: asMB(metadata.file_size_bytes) })
  if (metadata.nodata_value !== undefined) rows.push({ label: 'NoData', value: String(metadata.nodata_value) })

  if (metadata.extent && metadata.extent.minx !== undefined) {
    rows.push({
      label: 'Extent (proj)',
      value: `min(${num(metadata.extent.minx)}, ${num(metadata.extent.miny)}) · max(${num(metadata.extent.maxx)}, ${num(metadata.extent.maxy)})`
    })
  }
  if (metadata.bbox_wgs84) {
    const bounds = Array.isArray(metadata.bbox_wgs84) ? {west:metadata.bbox_wgs84[0],south:metadata.bbox_wgs84[1],east:metadata.bbox_wgs84[2],north:metadata.bbox_wgs84[3]} : metadata.bbox_wgs84
    rows.push({
      label: 'Extent (WGS84)',
      value: `W:${num(bounds.west)} E:${num(bounds.east)} S:${num(bounds.south)} N:${num(bounds.north)}`
    })
  }

  if (metadata.statistics) {
    const stats = metadata.statistics
    rows.push({
      label: 'Statistics',
      value: `min ${stats.min}, max ${stats.max}, mean ${num(stats.mean)}, stddev ${num(stats.stddev)}`
    })
  }

  const operations = metadata.operations_applied || metadata.processing_steps
  if (Array.isArray(operations) && operations.length > 0) {
    const values = operations.map((op: any) => {
      if (typeof op === 'string') return op
      return op.operation || ''
    }).filter(Boolean)
    rows.push({
      label: 'Operations',
      value: values.join(', ')
    })
  }

  if (metadata.source) rows.push({ label: 'Source', value: metadata.source })
  if (metadata.provider) rows.push({ label: 'Provider', value: metadata.provider })

  if (Array.isArray(metadata.source_files) && metadata.source_files.length > 0) {
    rows.push({
      label: 'Source files',
      value: metadata.source_files.map((f: any) => f.filename || '').filter(Boolean).join(', ')
    })
  }

  const dateAcquired = metadata.date_acquired || metadata.capture_date
  if (dateAcquired) {
    rows.push({ label: 'Date acquired', value: dateAcquired })
  } else if (metadata.processing_date) {
    rows.push({ label: 'Processing Date', value: metadata.processing_date })
  } else if (metadata.date) {
    rows.push({ label: 'Date', value: metadata.date })
  }

  if (metadata.validation_status) {
    rows.push({
      label: 'Validation',
      value: `${metadata.validation_status}${metadata.validation_date ? ` (${metadata.validation_date})` : ''}`
    })
  }
  if (metadata.notes) rows.push({ label: 'Notes', value: String(metadata.notes) })
  if (metadata.schema_version === 'zeus.acquisition/2.0') {
    for (const [label,key] of [['Source version','source_version'],['Units','units'],['Native spacing','native_spacing'],['Observation period','observation_period'],['Release date','release_date'],['Documented accuracy','accuracy'],['Licence','license'],['Attribution','attribution'],['Artifact role','role'],['Parameters','parameters'],['SHA-256','sha256'],['Scientific hash','scientific_hash']]) {
      const value = metadata[key]
      rows.push({label, value:value == null ? 'Unknown — not supplied by provider' : typeof value === 'object' ? JSON.stringify(value) : String(value)})
    }
  }
  
  return rows
}

export function featureBounds(feature: any): LngLatBounds | null {
  if (!feature) return null
  return getGeoJSONBounds({
    type: 'FeatureCollection',
    features: [feature]
  } as any)
}

