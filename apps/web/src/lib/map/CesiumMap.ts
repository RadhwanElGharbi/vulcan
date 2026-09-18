import type * as Cesium from 'cesium'
import { TerrainTiles, TERRAIN_GRID, type TerrainOverlay } from './terrain'

type Engine = typeof Cesium
type Bounds = [[number, number], [number, number]]
type CameraOptions = { center?: [number, number]; zoom?: number; bearing?: number; pitch?: number; duration?: number; padding?: unknown }
type Layer = { id: string; type: string; source?: string; paint?: Record<string, unknown>; layout?: Record<string, unknown>; filter?: unknown }
type SourceOptions = { type: string; data?: GeoJSON.FeatureCollection; tiles?: string[]; tileSize?: number; bounds?: number[]; encoding?: string }
export type GlobeMouseEvent = { lngLat: { lng: number; lat: number }; preventDefault: () => void }
type Handler = (event: GlobeMouseEvent) => void
type Source = SourceOptions & { setData: (data: GeoJSON.FeatureCollection) => void }
type RenderedLayer = { spec: Layer; imagery?: Cesium.ImageryLayer; data?: Cesium.CustomDataSource }

/** Keeps the dataset UI's small map contract while rendering entirely in CesiumJS.
 * Viewer options originate in colony-tech/defense-globe.js; dataset rendering and
 * terrain bridge are specific to ZEUS. No MapLibre renderer is created here.
 */
export class CesiumMap {
  readonly viewer: Cesium.Viewer
  private canvas: HTMLCanvasElement
  private sources = new Map<string, Source>()
  private layers = new Map<string, RenderedLayer>()
  private listeners = new Map<string, Set<Handler>>()
  private entityLayers = new WeakMap<Cesium.Entity, string>()
  private handler: Cesium.ScreenSpaceEventHandler
  private terrainTiles?: TerrainTiles
  private terrainOverlays: TerrainOverlay[] = []
  private terrainEnabled = true
  private destroyed = false
  private cameraMoving = false
  private imageryErrors = new Map<Cesium.ImageryProvider, string>()
  private doubleClickEnabled = true
  readonly doubleClickZoom = {
    disable: () => { this.doubleClickEnabled = false },
    enable: () => { this.doubleClickEnabled = true },
  }

  constructor(private C: Engine, container: HTMLElement, view: CameraOptions, onError: (message: string) => void, private onTerrainUnavailable: () => void = () => {}) {
    this.viewer = new C.Viewer(container, {
      animation: false, baseLayer: false, baseLayerPicker: false,
      fullscreenButton: false, geocoder: false, homeButton: false,
      infoBox: false, navigationHelpButton: false, sceneModePicker: false,
      selectionIndicator: false, timeline: false, scene3DOnly: true,
      shouldAnimate: false, requestRenderMode: true, maximumRenderTimeChange: Infinity,
      contextOptions: { webgl: { antialias: true } },
    })
    this.canvas = this.viewer.canvas
    // Cesium otherwise renders at CSS-pixel resolution even on high-DPI displays.
    // A small supersample also helps ordinary displays; cap cost on Retina/4K screens.
    this.viewer.resolutionScale = Math.min(2, Math.max(1.25, window.devicePixelRatio || 1))
    this.setTerrainEnabled(true)
    const scene = this.viewer.scene
    if (scene.skyBox) scene.skyBox.show = true
    if (scene.skyAtmosphere) scene.skyAtmosphere.show = true
    scene.globe.showGroundAtmosphere = true
    scene.globe.enableLighting = false
    scene.globe.maximumScreenSpaceError = 1.25
    scene.backgroundColor = C.Color.BLACK
    const controls = scene.screenSpaceCameraController
    controls.rotateEventTypes = C.CameraEventType.LEFT_DRAG
    controls.tiltEventTypes = [C.CameraEventType.MIDDLE_DRAG, C.CameraEventType.PINCH]
    controls.zoomEventTypes = [C.CameraEventType.RIGHT_DRAG, C.CameraEventType.WHEEL, C.CameraEventType.PINCH]
    controls.minimumZoomDistance = 20
    controls.maximumZoomDistance = 60000000
    scene.renderError.addEventListener((_scene: Cesium.Scene, error: Error) => onError(error.message))
    this.viewer.imageryLayers.layerAdded.addEventListener((layer: Cesium.ImageryLayer) => {
      layer.imageryProvider.errorEvent.addEventListener((error: Cesium.TileProviderError) => {
        this.imageryErrors.set(layer.imageryProvider, error.message)
      })
    })
    this.viewer.imageryLayers.layerRemoved.addEventListener((layer: Cesium.ImageryLayer) => this.imageryErrors.delete(layer.imageryProvider))
    const satellite = new C.UrlTemplateImageryProvider({
      url: 'https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
      maximumLevel: 19, credit: new C.Credit('<a href="https://www.arcgis.com/home/item.html?id=10df2279f9684e4a9f6a7f08febac2a9" target="_blank" rel="noopener noreferrer">World Imagery: Esri and imagery contributors</a>'),
    })
    // Tone adjustments belong only to the visual basemap, never dataset imagery.
    this.viewer.imageryLayers.add(new C.ImageryLayer(satellite, {
      maximumAnisotropy: 16, brightness: 1.02, contrast: 1.08, saturation: 1.05, gamma: 1.03,
    }))
    const labels = this.viewer.imageryLayers.addImageryProvider(new C.UrlTemplateImageryProvider({
      url: 'https://services.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
      maximumLevel: 19,
    }))
    labels.alpha = 0.72
    this.viewer.camera.setView({
      destination: C.Cartesian3.fromDegrees(...(view.center || [-80.5449, 43.4723]), this.heightForZoom(view.zoom ?? 2)),
      orientation: { heading: C.Math.toRadians(view.bearing || 0), pitch: C.Math.toRadians((view.pitch || 0) - 90), roll: 0 },
    })
    this.viewer.camera.changed.addEventListener(() => { this.emit('rotate'); this.emit('pitch') })
    this.viewer.camera.moveStart.addEventListener(() => { this.cameraMoving = true })
    this.viewer.camera.moveEnd.addEventListener(() => { this.cameraMoving = false; this.emit('moveend') })
    this.handler = new C.ScreenSpaceEventHandler(this.viewer.canvas)
    this.viewer.screenSpaceEventHandler.removeInputAction(C.ScreenSpaceEventType.LEFT_DOUBLE_CLICK)
    const mouse = (kind: string, point: Cesium.Cartesian2) => {
      const ray = this.viewer.camera.getPickRay(point)
      const position = ray && (scene.globe.pick(ray, scene) || this.viewer.camera.pickEllipsoid(point))
      if (!position) return
      const coord = C.Cartographic.fromCartesian(position)
      const event: GlobeMouseEvent = { lngLat: { lng: C.Math.toDegrees(coord.longitude), lat: C.Math.toDegrees(coord.latitude) }, preventDefault() {} }
      this.emit(kind, event)
      if (kind === 'click') {
        const entity = scene.pick(point)?.id
        const layer = entity && this.entityLayers.get(entity)
        if (layer) this.emit(`click:${layer}`, event)
      }
      if (kind === 'dblclick' && this.doubleClickEnabled) this.flyTo({ center: [event.lngLat.lng, event.lngLat.lat], zoom: this.getZoom() + 1 })
    }
    this.handler.setInputAction((e: { position: Cesium.Cartesian2 }) => mouse('click', e.position), C.ScreenSpaceEventType.LEFT_CLICK)
    this.handler.setInputAction((e: { position: Cesium.Cartesian2 }) => mouse('dblclick', e.position), C.ScreenSpaceEventType.LEFT_DOUBLE_CLICK)
    this.handler.setInputAction((e: { endPosition: Cesium.Cartesian2 }) => mouse('mousemove', e.endPosition), C.ScreenSpaceEventType.MOUSE_MOVE)
  }

  private heightForZoom(zoom: number) { return 48000000 / 2 ** zoom }
  getZoom() { return Math.log2(48000000 / Math.max(1, this.viewer.camera.positionCartographic.height)) }
  getBearing() { return this.C.Math.toDegrees(this.viewer.camera.heading) }
  getPitch() { return 90 + this.C.Math.toDegrees(this.viewer.camera.pitch) }
  getCenter() {
    const C = this.C, camera = this.viewer.camera
    const point = new C.Cartesian2(this.viewer.canvas.clientWidth / 2, this.viewer.canvas.clientHeight / 2)
    const ray = camera.getPickRay(point)
    const position = ray && this.viewer.scene.globe.pick(ray, this.viewer.scene)
    const cartographic = position ? C.Cartographic.fromCartesian(position) : camera.positionCartographic
    const lng = C.Math.toDegrees(cartographic.longitude), lat = C.Math.toDegrees(cartographic.latitude)
    return { lng, lat, toArray: (): [number, number] => [lng, lat] }
  }
  getCanvas() { return this.canvas }
  /** Wait for the current view's tiles and vector primitives to actually render. */
  waitForReady(signal: AbortSignal): Promise<void> {
    return new Promise((resolve, reject) => {
      if (signal.aborted || this.destroyed) { reject(new DOMException('Load cancelled', 'AbortError')); return }
      const scene = this.viewer.scene
      let settledFrames = 0
      const cleanup: Array<() => void> = []
      const finish = (error?: Error) => {
        cleanup.forEach(remove => remove())
        signal.removeEventListener('abort', abort)
        if (error) reject(error); else resolve()
      }
      const abort = () => finish(new DOMException('Load cancelled', 'AbortError'))
      signal.addEventListener('abort', abort, { once: true })
      cleanup.push(scene.renderError.addEventListener((_scene: Cesium.Scene, error: Error) => finish(error)))
      for (let i = 0; i < this.viewer.imageryLayers.length; i++) {
        const layer = this.viewer.imageryLayers.get(i)
        if (layer.show && this.imageryErrors.has(layer.imageryProvider)) {
          finish(new Error(`Could not load map imagery: ${this.imageryErrors.get(layer.imageryProvider)}`))
          return
        }
        if (layer.show) cleanup.push(layer.imageryProvider.errorEvent.addEventListener((error: Cesium.TileProviderError) => {
          finish(new Error(`Could not load map imagery: ${error.message}`))
        }))
      }
      cleanup.push(scene.postRender.addEventListener(() => {
        const complete = !this.cameraMoving && scene.globe.tilesLoaded && this.viewer.dataSourceDisplay.ready
        settledFrames = complete ? settledFrames + 1 : 0
        if (settledFrames >= 2) finish()
        else scene.requestRender()
      }))
      scene.requestRender()
    })
  }
  resize() {
    if (!this.destroyed) {
      this.viewer.resolutionScale = Math.min(2, Math.max(1.25, window.devicePixelRatio || 1))
      this.viewer.resize(); this.viewer.scene.requestRender()
    }
  }
  private emit(kind: string, event?: GlobeMouseEvent) {
    for (const handler of this.listeners.get(kind) || []) handler(event || { lngLat: this.getCenter(), preventDefault() {} })
  }
  on(kind: string, layerOrHandler: string | Handler, handler?: Handler) {
    const key = typeof layerOrHandler === 'string' ? `${kind}:${layerOrHandler}` : kind
    const callback = typeof layerOrHandler === 'string' ? handler! : layerOrHandler
    if (!this.listeners.has(key)) this.listeners.set(key, new Set())
    this.listeners.get(key)!.add(callback)
  }
  off(kind: string, layerOrHandler: string | Handler, handler?: Handler) {
    const key = typeof layerOrHandler === 'string' ? `${kind}:${layerOrHandler}` : kind
    this.listeners.get(key)?.delete(typeof layerOrHandler === 'string' ? handler! : layerOrHandler)
  }
  easeTo(options: CameraOptions) {
    const C = this.C, camera = this.viewer.camera
    const center = options.center || this.getCenter().toArray()
    const tilt = C.Math.toRadians(Math.min(85, Math.max(0, options.pitch ?? this.getPitch())))
    camera.flyToBoundingSphere(new C.BoundingSphere(C.Cartesian3.fromDegrees(...center), 0), {
      offset: new C.HeadingPitchRange(C.Math.toRadians(options.bearing ?? this.getBearing()), tilt - Math.PI / 2, this.heightForZoom(options.zoom ?? this.getZoom()) / Math.max(0.1, Math.cos(tilt))),
      duration: (options.duration ?? 700) / 1000,
    })
  }
  flyTo(options: CameraOptions) { this.easeTo(options) }
  fitBounds(bounds: Bounds, options?: { padding?: number; maxZoom?: number }) {
    const C = this.C, camera = this.viewer.camera
    const rectangle = C.Rectangle.fromDegrees(...bounds[0], ...bounds[1])
    const position = C.Cartographic.fromCartesian(camera.getRectangleCameraCoordinates(rectangle))
    const dimension = Math.min(this.viewer.canvas.clientWidth, this.viewer.canvas.clientHeight)
    const paddingScale = dimension / Math.max(100, dimension - (options?.padding ?? 0) * 2)
    const height = Math.max(position.height * paddingScale, this.heightForZoom(options?.maxZoom ?? 18))
    camera.flyTo({ destination: C.Cartesian3.fromRadians(position.longitude, position.latitude, height), orientation: { heading: 0, pitch: -Math.PI / 2, roll: 0 }, duration: 0.8 })
  }
  getStyle() { return { layers: [...this.layers.values()].map(l => l.spec), sources: Object.fromEntries(this.sources) } }
  getLayer(id: string) { return this.layers.get(id)?.spec }
  getSource(id: string) { return this.sources.get(id) }
  addSource(id: string, options: SourceOptions) {
    this.sources.set(id, { ...options, setData: data => {
      const source = this.sources.get(id)
      if (!source || this.destroyed) return
      source.data = data
      for (const layer of this.layers.values()) if (layer.spec.source === id) this.renderEntities(layer)
      this.viewer.scene.requestRender()
    } })
  }
  removeSource(id: string) { this.sources.delete(id) }
  addLayer(spec: Layer) {
    const C = this.C, source = this.sources.get(spec.source || '')
    if (!source) throw new Error(`Missing source: ${spec.source}`)
    const layer: RenderedLayer = { spec: { ...spec, paint: { ...spec.paint }, layout: { ...spec.layout } } }
    if (spec.type === 'raster') {
      layer.imagery = this.viewer.imageryLayers.addImageryProvider(new C.UrlTemplateImageryProvider({
        url: source.tiles![0], tileWidth: source.tileSize || 256, tileHeight: source.tileSize || 256,
        tilingScheme: new C.WebMercatorTilingScheme(), maximumLevel: 18,
        ...(source.bounds ? { rectangle: C.Rectangle.fromDegrees(source.bounds[0], source.bounds[1], source.bounds[2], source.bounds[3]) } : {}),
      }))
    } else {
      layer.data = new C.CustomDataSource(spec.id)
      void this.viewer.dataSources.add(layer.data).then(data => {
        if (!this.destroyed && this.layers.get(spec.id) !== layer) this.viewer.dataSources.remove(data, true)
      })
    }
    this.layers.set(spec.id, layer)
    this.renderEntities(layer)
    this.applyStyle(layer)
  }
  removeLayer(id: string) {
    const layer = this.layers.get(id)
    if (!layer) return
    if (!this.destroyed) {
      if (layer.imagery) this.viewer.imageryLayers.remove(layer.imagery, true)
      if (layer.data) this.viewer.dataSources.remove(layer.data, true)
      this.viewer.scene.requestRender()
    }
    this.layers.delete(id)
  }
  private renderEntities(layer: RenderedLayer) {
    if (!layer.data || this.destroyed) return
    const C = this.C, entities = layer.data.entities, type = layer.spec.type
    entities.removeAll()
    entities.suspendEvents()
    const positions = (coords: GeoJSON.Position[]) => coords.map(p => C.Cartesian3.fromDegrees(p[0], p[1], p[2] || 0))
    const add = (options: Cesium.Entity.ConstructorOptions) => { const entity = entities.add(options); this.entityLayers.set(entity, layer.spec.id) }
    const geometry = (shape: GeoJSON.Geometry, properties: GeoJSON.GeoJsonProperties) => {
      switch (shape.type) {
        case 'GeometryCollection': shape.geometries.forEach(g => geometry(g, properties)); break
        case 'MultiPolygon': shape.coordinates.forEach(coordinates => geometry({ type: 'Polygon', coordinates }, properties)); break
        case 'MultiLineString': shape.coordinates.forEach(coordinates => geometry({ type: 'LineString', coordinates }, properties)); break
        case 'MultiPoint': shape.coordinates.forEach(coordinates => geometry({ type: 'Point', coordinates }, properties)); break
        case 'Polygon':
          if (type === 'fill' && shape.coordinates[0]?.length >= 3) add({ polygon: {
            hierarchy: new C.PolygonHierarchy(positions(shape.coordinates[0]), shape.coordinates.slice(1).map(r => new C.PolygonHierarchy(positions(r)))),
            height: 0, heightReference: C.HeightReference.CLAMP_TO_GROUND,
          } })
          if (type === 'line') shape.coordinates.forEach(r => { if (r.length >= 2) add({ polyline: { positions: positions(r), clampToGround: true } }) })
          break
        case 'LineString':
          if (type === 'line' && shape.coordinates.length >= 2) add({ polyline: { positions: positions(shape.coordinates), clampToGround: true } })
          break
        case 'Point':
          if (type === 'circle') add({ position: C.Cartesian3.fromDegrees(shape.coordinates[0], shape.coordinates[1]), point: { heightReference: C.HeightReference.CLAMP_TO_GROUND } })
          if (type === 'symbol') add({ position: C.Cartesian3.fromDegrees(shape.coordinates[0], shape.coordinates[1]), label: {
            text: String(properties?.idx ?? ''), font: '10px sans-serif', pixelOffset: new C.Cartesian2(0, -14),
            heightReference: C.HeightReference.CLAMP_TO_GROUND, disableDepthTestDistance: Infinity,
          } })
          break
      }
    }
    try { for (const feature of this.sources.get(layer.spec.source || '')?.data?.features || []) if (feature.geometry) geometry(feature.geometry, feature.properties) }
    finally { entities.resumeEvents() }
    this.applyStyle(layer)
  }
  private applyStyle(layer: RenderedLayer) {
    const C = this.C, paint = layer.spec.paint || {}, visible = layer.spec.layout?.visibility !== 'none'
    const number = (name: string, fallback: number) => typeof paint[name] === 'number' ? paint[name] as number : fallback
    const color = (name: string, opacity: string, fallback = '#ffffff') => (C.Color.fromCssColorString(String(paint[name] || fallback)) || C.Color.WHITE).withAlpha(number(opacity, 1))
    if (layer.imagery) { layer.imagery.show = visible; layer.imagery.alpha = number('raster-opacity', 1) }
    if (layer.data) {
      layer.data.show = visible
      for (const entity of layer.data.entities.values) {
        if (entity.polygon) entity.polygon.material = new C.ColorMaterialProperty(color('fill-color', 'fill-opacity'))
        if (entity.polyline) {
          const lineColor = color('line-color', 'line-opacity')
          const dash = paint['line-dasharray'] as number[] | undefined
          entity.polyline.material = dash ? new C.PolylineDashMaterialProperty({ color: lineColor, dashLength: Math.max(4, dash.reduce((a,b) => a+b, 0) * number('line-width', 2)) }) : new C.ColorMaterialProperty(lineColor)
          entity.polyline.width = new C.ConstantProperty(number('line-width', 2))
        }
        if (entity.point) {
          entity.point.color = new C.ConstantProperty(color('circle-color', 'circle-opacity'))
          entity.point.pixelSize = new C.ConstantProperty(number('circle-radius', 4) * 2)
          entity.point.outlineColor = new C.ConstantProperty(color('circle-stroke-color', 'circle-opacity'))
          entity.point.outlineWidth = new C.ConstantProperty(number('circle-stroke-width', 0))
        }
      }
    }
    if (!this.destroyed) this.viewer.scene.requestRender()
  }
  setLayoutProperty(id: string, key: string, value: unknown) { const l = this.layers.get(id); if (l && !Object.is(l.spec.layout![key], value)) { l.spec.layout![key] = value; this.applyStyle(l) } }
  setPaintProperty(id: string, key: string, value: unknown) { const l = this.layers.get(id); if (l && !Object.is(l.spec.paint![key], value)) { l.spec.paint![key] = value; this.applyStyle(l) } }
  moveLayer(id: string) {
    const layer = this.layers.get(id)
    if (!layer || this.destroyed) return
    if (layer.imagery) this.viewer.imageryLayers.raiseToTop(layer.imagery)
    if (layer.data && this.viewer.dataSources.contains(layer.data)) this.viewer.dataSources.raiseToTop(layer.data)
    this.layers.delete(id); this.layers.set(id, layer)
    this.viewer.scene.requestRender()
  }
  setTerrainOverlays(overlays: TerrainOverlay[]) {
    this.terrainOverlays = overlays
    this.setTerrainEnabled(this.terrainEnabled)
  }
  setTerrainEnabled(enabled: boolean) {
    if (this.destroyed) return
    this.terrainEnabled = enabled
    this.terrainTiles?.dispose()
    const C = this.C
    if (!enabled) {
      this.viewer.terrainProvider = new C.EllipsoidTerrainProvider()
    } else {
      const tiles = new TerrainTiles(this.onTerrainUnavailable), overlays = this.terrainOverlays
      this.terrainTiles = tiles
      let active = 0
      this.viewer.terrainProvider = new C.CustomHeightmapTerrainProvider({
        tilingScheme: new C.WebMercatorTilingScheme(), width: TERRAIN_GRID, height: TERRAIN_GRID,
        credit: new C.Credit('<a href="https://github.com/tilezen/joerd/blob/master/docs/attribution.md" target="_blank" rel="noopener noreferrer">Background terrain: Mapzen / source attribution</a>'),
        callback: (x, y, level) => {
          if (active >= 4) return undefined // Let Cesium defer tiles instead of creating an unbounded queue.
          active++
          return tiles.heightmap(x, y, level, overlays).finally(() => { active-- })
        },
      })
    }
    this.viewer.scene.verticalExaggeration = 1
    this.viewer.scene.requestRender()
  }
  remove() {
    if (this.destroyed) return
    this.destroyed = true
    this.terrainTiles?.dispose()
    this.listeners.clear()
    this.handler.destroy()
    this.viewer.destroy()
    this.layers.clear(); this.sources.clear(); this.imageryErrors.clear()
  }
}
