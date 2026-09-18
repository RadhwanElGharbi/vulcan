type TileKey = string

const TILE_SIZE = 256
const MAX_LATITUDE = 85.05112878

/**
 * Lightweight Terrain-RGB sampler that fetches MapLibre DEM tiles
 * and decodes elevation values for arbitrary lat/lon points.
 */
export class TerrainSampler {
  private template: string
  private cache = new Map<TileKey, ImageData | null>()
  private pending = new Map<TileKey, Promise<ImageData | null>>()
  private canvas: HTMLCanvasElement | null = null
  private cacheLimit = 96

  constructor(template: string) {
    this.template = template
  }

  updateTemplate(nextTemplate: string) {
    if (this.template === nextTemplate) return
    this.template = nextTemplate
    this.cache.clear()
    this.pending.clear()
  }

  dispose() {
    this.cache.clear()
    this.pending.clear()
    this.canvas = null
  }

  async sample(lng: number, lat: number, zoom: number): Promise<number | null> {
    if (!Number.isFinite(lng) || !Number.isFinite(lat) || !this.template) {
      return null
    }

    const cappedLat = Math.max(Math.min(lat, MAX_LATITUDE), -MAX_LATITUDE)
    const z = Math.max(2, Math.min(14, Math.round(zoom)))
    const n = 2 ** z
    const xFloat = ((lng + 180) / 360) * n
    const latRad = (cappedLat * Math.PI) / 180
    const yFloat = (1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2 * n
    const xTile = Math.floor(xFloat)
    const yTile = Math.floor(yFloat)
    const pixelX = Math.min(TILE_SIZE - 1, Math.max(0, Math.floor((xFloat - xTile) * TILE_SIZE)))
    const pixelY = Math.min(TILE_SIZE - 1, Math.max(0, Math.floor((yFloat - yTile) * TILE_SIZE)))

    const tile = await this.getTile(z, xTile, yTile)
    if (!tile) return null

    const idx = (pixelY * tile.width + pixelX) * 4
    const data = tile.data
    const r = data[idx]
    const g = data[idx + 1]
    const b = data[idx + 2]

    if (!data[idx + 3] || r === undefined || g === undefined || b === undefined) {
      return null
    }

    const elevation = -10000 + ((r * 256 * 256 + g * 256 + b) * 0.1)
    return Number.isFinite(elevation) ? elevation : null
  }

  private async getTile(z: number, x: number, y: number): Promise<ImageData | null> {
    const key = this.toKey(z, x, y)
    if (this.cache.has(key)) {
      return this.cache.get(key) ?? null
    }
    if (this.pending.has(key)) {
      return this.pending.get(key)!
    }

    const promise = this.fetchTile(z, x, y)
      .then((image) => {
        if (image) {
          this.cache.set(key, image)
          this.evictIfNeeded()
        } else {
          this.cache.set(key, null)
        }
        this.pending.delete(key)
        return image
      })
      .catch((error) => {
        console.warn('[TerrainSampler] tile fetch failed', error)
        this.pending.delete(key)
        this.cache.set(key, null)
        return null
      })

    this.pending.set(key, promise)
    return promise
  }

  private async fetchTile(z: number, x: number, y: number): Promise<ImageData | null> {
    if (!this.template || typeof window === 'undefined') {
      return null
    }
    const url = this.template
      .replace('{z}', z.toString())
      .replace('{x}', x.toString())
      .replace('{y}', y.toString())

    try {
      const response = await fetch(url, { cache: 'force-cache' })
      if (!response.ok) {
        return null
      }
      const blob = await response.blob()
      return await this.blobToImageData(blob)
    } catch (error) {
      console.warn('[TerrainSampler] request failed', error)
      return null
    }
  }

  private blobToImageData(blob: Blob): Promise<ImageData | null> {
    if (typeof window === 'undefined') {
      return Promise.resolve(null)
    }

    if (typeof createImageBitmap === 'function') {
      return createImageBitmap(blob)
        .then((bitmap) => {
          const ctx = this.ensureContext(bitmap.width, bitmap.height)
          if (!ctx) {
            bitmap.close()
            return null
          }
          ctx.clearRect(0, 0, bitmap.width, bitmap.height)
          ctx.drawImage(bitmap, 0, 0)
          const data = ctx.getImageData(0, 0, bitmap.width, bitmap.height)
          bitmap.close()
          return data
        })
        .catch((error) => {
          console.warn('[TerrainSampler] bitmap decode failed', error)
          return null
        })
    }

    return new Promise((resolve, reject) => {
      if (typeof document === 'undefined') {
        resolve(null)
        return
      }
      const img = new Image()
      const objectUrl = URL.createObjectURL(blob)
      img.crossOrigin = 'anonymous'
      img.onload = () => {
        try {
          const ctx = this.ensureContext(img.width, img.height)
          if (!ctx) {
            resolve(null)
            return
          }
          ctx.clearRect(0, 0, img.width, img.height)
          ctx.drawImage(img, 0, 0)
          const data = ctx.getImageData(0, 0, img.width, img.height)
          resolve(data)
        } catch (error) {
          reject(error)
        } finally {
          URL.revokeObjectURL(objectUrl)
        }
      }
      img.onerror = (event) => {
        console.warn('[TerrainSampler] image decode error', event)
        URL.revokeObjectURL(objectUrl)
        resolve(null)
      }
      img.src = objectUrl
    })
  }

  private ensureContext(width: number, height: number): CanvasRenderingContext2D | null {
    if (typeof document === 'undefined') {
      return null
    }
    if (!this.canvas) {
      this.canvas = document.createElement('canvas')
    }
    this.canvas.width = width
    this.canvas.height = height
    return this.canvas.getContext('2d', { willReadFrequently: true })
  }

  private evictIfNeeded() {
    if (this.cache.size <= this.cacheLimit) {
      return
    }
    const firstKey = this.cache.keys().next().value
    if (firstKey) {
      this.cache.delete(firstKey)
    }
  }

  private toKey(z: number, x: number, y: number): TileKey {
    return `${z}/${x}/${y}`
  }
}


