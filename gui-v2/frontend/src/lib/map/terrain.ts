/** Display terrain only. Never enters the scientific inventory or measurement sampler. */
export type TerrainOverlay = { id: string; template: string; bounds?: [number, number, number, number] }
type Tile = { data: Uint8ClampedArray; width: number; height: number }
type Source = TerrainOverlay & { encoding: 'terrarium' | 'mapbox'; maxZoom: number }
export const TERRAIN_GRID = 65
export const BACKGROUND_TERRAIN = 'https://elevation-tiles-prod.s3.amazonaws.com/terrarium/{z}/{x}/{y}.png'
const wrap = (x: number, n: number) => ((x % n) + n) % n

export function containsTerrainPoint(bounds: TerrainOverlay['bounds'], lng: number, lat: number) {
  if (!bounds) return true // Legacy tiles still carry their own alpha coverage.
  const [w, s, e, n] = bounds
  return lat >= s && lat <= n && (w <= e ? lng >= w && lng <= e : lng >= w || lng <= e)
}

export function decodeTerrainPixel(tile: Tile | null, x: number, y: number, encoding: Source['encoding']): number | null {
  if (!tile) return null
  const i = (y * tile.width + x) * 4, d = tile.data
  if (!d[i + 3]) return null
  return encoding === 'terrarium' ? d[i] * 256 + d[i + 1] + d[i + 2] / 256 - 32768
    : -10000 + (d[i] * 65536 + d[i + 1] * 256 + d[i + 2]) * 0.1
}

/** One bounded cache per globe; cancel all work when a project/provider is replaced. */
export class TerrainTiles {
  private controller = new AbortController()
  private cache = new Map<string, Tile | null>()
  private pending = new Map<string, Promise<Tile | null>>()
  private active = 0
  private queue: Array<() => void> = []
  private warned = false
  constructor(private onUnavailable: () => void) {}
  dispose() { this.controller.abort(); this.queue.splice(0).forEach(resolve => resolve()); this.cache.clear(); this.pending.clear() }

  private get(source: Source, z: number, x: number, y: number): Promise<Tile | null> {
    const n = 2 ** z
    const url = source.template.replace('{z}', String(z)).replace('{x}', String(wrap(x, n))).replace('{y}', String(Math.max(0, Math.min(n - 1, y))))
    if (this.cache.has(url)) { const tile = this.cache.get(url)!; this.cache.delete(url); this.cache.set(url, tile); return Promise.resolve(tile) }
    const existing = this.pending.get(url)
    if (existing) return existing
    const promise = this.download(url).then(tile => {
      if (!this.controller.signal.aborted) {
        this.cache.set(url, tile)
        while (this.cache.size > 96) this.cache.delete(this.cache.keys().next().value!)
      }
      return tile
    }).finally(() => this.pending.delete(url))
    this.pending.set(url, promise)
    return promise
  }

  private async download(url: string): Promise<Tile | null> {
    if (this.active >= 8) await new Promise<void>(resolve => this.queue.push(resolve))
    if (this.controller.signal.aborted) return null
    this.active++
    const request = new AbortController(), abort = () => request.abort()
    this.controller.signal.addEventListener('abort', abort, { once: true })
    const timer = setTimeout(abort, 10000)
    try {
      const response = await fetch(url, { signal: request.signal })
      if (!response.ok) throw new Error(`Terrain HTTP ${response.status}`)
      const bitmap = await createImageBitmap(await response.blob(), { colorSpaceConversion: 'none', premultiplyAlpha: 'none' })
      try {
        if (bitmap.width !== 256 || bitmap.height !== 256) throw new Error('Invalid terrain tile size')
        const canvas = document.createElement('canvas'); canvas.width = canvas.height = 256
        const context = canvas.getContext('2d', { willReadFrequently: true })!
        context.drawImage(bitmap, 0, 0)
        return context.getImageData(0, 0, 256, 256)
      } finally { bitmap.close() }
    } catch {
      if (!this.controller.signal.aborted && !this.warned) { this.warned = true; this.onUnavailable() }
      return null
    } finally {
      clearTimeout(timer); this.controller.signal.removeEventListener('abort', abort)
      this.active--; this.queue.shift()?.()
    }
  }

  /** Shared world coordinates and bilinear sampling make adjacent tile edges identical. */
  async heightmap(x: number, y: number, level: number, overlays: TerrainOverlay[]) {
    const count = TERRAIN_GRID ** 2, heights = new Float32Array(count), n = 2 ** level
    const points = Array.from({ length: count }, (_, i) => {
      const u = (x + (i % TERRAIN_GRID) / (TERRAIN_GRID - 1)) / n
      const v = (y + Math.floor(i / TERRAIN_GRID) / (TERRAIN_GRID - 1)) / n
      return { u, v, lng: u * 360 - 180, lat: Math.atan(Math.sinh(Math.PI * (1 - 2 * v))) * 180 / Math.PI }
    })
    // Stable list order: later fetched DEMs take precedence; alpha gaps retain the layer underneath.
    const sources: Source[] = [{ id: 'background', template: BACKGROUND_TERRAIN, encoding: 'terrarium', maxZoom: 12 },
      ...overlays.map(overlay => ({ ...overlay, encoding: 'mapbox' as const, maxZoom: 18 }))]
    for (const source of sources) {
      if (this.controller.signal.aborted) break
      const z = Math.min(level, source.maxZoom), pixels = 256 * 2 ** z
      const needed = new Map<string, Promise<Tile | null>>()
      const samples = points.map(point => {
        if (!containsTerrainPoint(source.bounds, point.lng, point.lat)) return null
        const px = point.u * pixels - 0.5, py = Math.max(0, Math.min(pixels - 1, point.v * pixels - 0.5))
        const ix = Math.floor(px), iy = Math.floor(py)
        const corners = [[ix, iy], [ix + 1, iy], [ix, Math.min(pixels - 1, iy + 1)], [ix + 1, Math.min(pixels - 1, iy + 1)]].map(([a, b]) => {
          const tx = Math.floor(a / 256), ty = Math.floor(b / 256), key = `${tx}/${ty}`
          if (!needed.has(key)) needed.set(key, this.get(source, z, tx, ty))
          return { key, x: wrap(a, 256), y: wrap(b, 256) }
        })
        return { corners, dx: px - ix, dy: py - iy }
      })
      const tiles = new Map(await Promise.all([...needed].map(async ([key, promise]) => [key, await promise] as const)))
      samples.forEach((sample, i) => {
        if (!sample) return
        const values = sample.corners.map(c => decodeTerrainPixel(tiles.get(c.key) ?? null, c.x, c.y, source.encoding))
        const { dx, dy } = sample
        // Never interpolate through a gap. Use the nearest valid pixel only when its own alpha is valid.
        const nearest = values[(dy >= 0.5 ? 2 : 0) + (dx >= 0.5 ? 1 : 0)]
        if (nearest === null) return
        heights[i] = values.every(v => v !== null)
          ? values[0]! * (1 - dx) * (1 - dy) + values[1]! * dx * (1 - dy) + values[2]! * (1 - dx) * dy + values[3]! * dx * dy
          : nearest
      })
    }
    return heights
  }
}
