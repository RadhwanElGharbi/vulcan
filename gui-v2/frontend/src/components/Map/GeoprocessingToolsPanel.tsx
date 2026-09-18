'use client'

import {
formatArea,
formatDistance,
geodesicArea,
planarArea,
polylineLength,
sampleElevationProfile,
segmentDistances,
type ElevationSample
} from '@/lib/geoprocessing'
import type { TerrainSampler } from '@/lib/terrainSampler'
import { cn } from '@/lib/utils'
import { CheckCircle2,MousePointerClick,Play,RotateCcw,Trash2,X } from 'lucide-react'
import type { CesiumMap, GlobeMouseEvent } from '@/lib/map/CesiumMap'
import { useCallback,useEffect,useMemo,useRef,useState } from 'react'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type MeasureMode = 'geodesic' | 'planar'

export interface MeasureToolPanelProps {
  tool: 'distance' | 'area' | 'elevation'
  map: CesiumMap | null
  terrainSampler: TerrainSampler | null
  demAvailable: boolean
  active: boolean
  onActivate: () => void
  onClose: () => void
  initialPosition: { x: number; y: number }
}

// ---------------------------------------------------------------------------
// Per-tool visual config
// ---------------------------------------------------------------------------

const TOOL_CFG = {
  distance: {
    title: 'Measure Distance',
    lineColor: '#ff9800',
    pointColor: '#ff9800',
    accent: 'text-orange-400',
    border: 'border-orange-500/40',
    activeDot: 'bg-orange-500',
  },
  area: {
    title: 'Measure Area',
    lineColor: '#00bcd4',
    pointColor: '#00bcd4',
    accent: 'text-cyan-400',
    border: 'border-cyan-500/40',
    activeDot: 'bg-cyan-500',
  },
  elevation: {
    title: 'Elevation Profile',
    lineColor: '#4caf50',
    pointColor: '#4caf50',
    accent: 'text-green-400',
    border: 'border-green-500/40',
    activeDot: 'bg-green-500',
  },
} as const

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function emptyFC(): GeoJSON.FeatureCollection {
  return { type: 'FeatureCollection', features: [] }
}

function ptFeature(coord: [number, number], idx: number): GeoJSON.Feature {
  return {
    type: 'Feature',
    geometry: { type: 'Point', coordinates: coord },
    properties: { idx: idx + 1 }
  }
}

// ---------------------------------------------------------------------------
// MeasureToolPanel
// ---------------------------------------------------------------------------

export function MeasureToolPanel({
  tool,
  map,
  terrainSampler,
  demAvailable,
  active,
  onActivate,
  onClose,
  initialPosition
}: MeasureToolPanelProps) {
  const cfg = TOOL_CFG[tool]

  // Unique source / layer IDs for this tool instance
  const SRC_LINE  = `__gp_${tool}_line`
  const SRC_POLY  = `__gp_${tool}_poly`
  const SRC_PTS   = `__gp_${tool}_pts`
  const LYR_LINE  = `__gp_${tool}_line_lyr`
  const LYR_POLY_FILL    = `__gp_${tool}_poly_fill`
  const LYR_POLY_OUTLINE = `__gp_${tool}_poly_out`
  const LYR_PTS       = `__gp_${tool}_pts_lyr`
  const LYR_PTS_LABEL = `__gp_${tool}_pts_lbl`

  // --- state ---------------------------------------------------------------
  const [points, setPoints]       = useState<[number, number][]>([])
  const [hoverPt, setHoverPt]     = useState<[number, number] | null>(null)
  const [collecting, setCollecting] = useState(true)
  const [mode, setMode]           = useState<MeasureMode>('geodesic')
  const [elevProfile, setElevProfile] = useState<ElevationSample[] | null>(null)
  const [elevLoading, setElevLoading] = useState(false)
  const [panelPos, setPanelPos]   = useState(initialPosition)

  // --- refs ----------------------------------------------------------------
  const panelRef       = useRef<HTMLDivElement>(null)
  const dragRef        = useRef({ dragging: false, sx: 0, sy: 0, ox: 0, oy: 0 })
  const clickHRef      = useRef<((e: GlobeMouseEvent) => void) | null>(null)
  const moveHRef       = useRef<((e: GlobeMouseEvent) => void) | null>(null)
  const dblHRef        = useRef<((e: GlobeMouseEvent) => void) | null>(null)
  const pointsRef      = useRef(points)
  const collectingRef  = useRef(collecting)
  pointsRef.current    = points
  collectingRef.current = collecting

  const canCollect = tool !== 'elevation' || demAvailable

  // --- dragging ------------------------------------------------------------
  const handleDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    if (!active) onActivate()
    dragRef.current = { dragging: true, sx: e.clientX, sy: e.clientY, ox: panelPos.x, oy: panelPos.y }
    const onMove = (ev: MouseEvent) => {
      if (!dragRef.current.dragging) return
      setPanelPos({
        x: dragRef.current.ox + (ev.clientX - dragRef.current.sx),
        y: dragRef.current.oy + (ev.clientY - dragRef.current.sy)
      })
    }
    const onUp = () => {
      dragRef.current.dragging = false
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }, [active, onActivate, panelPos])

  // --- activate on any panel interaction -----------------------------------
  const handlePanelMouseDown = useCallback(() => {
    if (!active) onActivate()
  }, [active, onActivate])

  // --- map sources & layers (mount / unmount) ------------------------------
  useEffect(() => {
    if (!map) return
    const addSrc = (id: string) => { if (!map.getSource(id)) map.addSource(id, { type: 'geojson', data: emptyFC() }) }
    addSrc(SRC_LINE)
    addSrc(SRC_POLY)
    addSrc(SRC_PTS)

    // Polygon fill + outline (area only, but safe to add for all)
    if (!map.getLayer(LYR_POLY_FILL)) {
      map.addLayer({ id: LYR_POLY_FILL, type: 'fill', source: SRC_POLY, paint: { 'fill-color': cfg.lineColor, 'fill-opacity': 0.12 } })
    }
    if (!map.getLayer(LYR_POLY_OUTLINE)) {
      map.addLayer({ id: LYR_POLY_OUTLINE, type: 'line', source: SRC_POLY, paint: { 'line-color': cfg.lineColor, 'line-width': 2, 'line-dasharray': [4, 2] } })
    }
    // Line
    if (!map.getLayer(LYR_LINE)) {
      map.addLayer({ id: LYR_LINE, type: 'line', source: SRC_LINE, paint: { 'line-color': cfg.lineColor, 'line-width': 3 } })
    }
    // Points
    if (!map.getLayer(LYR_PTS)) {
      map.addLayer({
        id: LYR_PTS, type: 'circle', source: SRC_PTS,
        paint: { 'circle-radius': 5, 'circle-color': cfg.pointColor, 'circle-stroke-color': '#ffffff', 'circle-stroke-width': 2 }
      })
    }
    if (!map.getLayer(LYR_PTS_LABEL)) {
      map.addLayer({
        id: LYR_PTS_LABEL, type: 'symbol', source: SRC_PTS,
        layout: { 'text-field': ['to-string', ['get', 'idx']], 'text-size': 10, 'text-offset': [0, -1.4], 'text-allow-overlap': true },
        paint: { 'text-color': '#ffffff', 'text-halo-color': '#000000', 'text-halo-width': 1.5 }
      })
    }

    return () => {
      for (const id of [LYR_PTS_LABEL, LYR_PTS, LYR_LINE, LYR_POLY_OUTLINE, LYR_POLY_FILL]) {
        if (map.getLayer(id)) map.removeLayer(id)
      }
      for (const id of [SRC_LINE, SRC_POLY, SRC_PTS]) {
        if (map.getSource(id)) map.removeSource(id)
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map])

  // --- update map drawing --------------------------------------------------
  const updateMapDrawing = useCallback(
    (pts: [number, number][], hover: [number, number] | null) => {
      if (!map) return
      const blank = emptyFC()
      const all = hover ? [...pts, hover] : pts

      if (tool === 'area') {
        // Polygon
        const closed = all.length >= 3 ? [...all, all[0]] : []
        const polyData: GeoJSON.FeatureCollection = closed.length >= 4
          ? { type: 'FeatureCollection', features: [{ type: 'Feature', geometry: { type: 'Polygon', coordinates: [closed] }, properties: {} }] }
          : blank
        ;(map.getSource(SRC_POLY))?.setData(polyData)

        // Outline as line for better visibility
        const lineCoords = all.length >= 2 ? [...all, all[0]] : all
        ;(map.getSource(SRC_LINE))?.setData(
          lineCoords.length >= 2
            ? { type: 'FeatureCollection', features: [{ type: 'Feature', geometry: { type: 'LineString', coordinates: lineCoords }, properties: {} }] }
            : blank
        )
      } else {
        // Distance / Elevation – polyline
        ;(map.getSource(SRC_LINE))?.setData(
          all.length >= 2
            ? { type: 'FeatureCollection', features: [{ type: 'Feature', geometry: { type: 'LineString', coordinates: all }, properties: {} }] }
            : blank
        )
        ;(map.getSource(SRC_POLY))?.setData(blank)
      }

      // Points (committed only – not hover)
      ;(map.getSource(SRC_PTS))?.setData({
        type: 'FeatureCollection',
        features: pts.map((c, i) => ptFeature(c, i))
      })
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [map, tool]
  )

  // --- map click / move / dblclick handlers --------------------------------
  useEffect(() => {
    if (!map) return

    const shouldCollect = active && collecting && canCollect

    // Always clean up old handlers first
    if (clickHRef.current)  { map.off('click', clickHRef.current);   clickHRef.current = null }
    if (moveHRef.current)   { map.off('mousemove', moveHRef.current); moveHRef.current = null }
    if (dblHRef.current)    { map.off('dblclick', dblHRef.current);   dblHRef.current = null }

    if (!shouldCollect) {
      map.getCanvas().style.cursor = ''
      return
    }

    // Disable default double-click zoom while collecting
    map.doubleClickZoom.disable()
    map.getCanvas().style.cursor = 'crosshair'

    const onClick = (e: GlobeMouseEvent) => {
      if (!collectingRef.current) return
      const coord: [number, number] = [e.lngLat.lng, e.lngLat.lat]
      setPoints(prev => {
        const next = [...prev, coord]
        requestAnimationFrame(() => updateMapDrawing(next, null))
        return next
      })
      setHoverPt(null)
    }

    const onMove = (e: GlobeMouseEvent) => {
      if (!collectingRef.current) return
      const coord: [number, number] = [e.lngLat.lng, e.lngLat.lat]
      setHoverPt(coord)
      requestAnimationFrame(() => updateMapDrawing(pointsRef.current, coord))
    }

    const onDblClick = (e: GlobeMouseEvent) => {
      e.preventDefault()
      // Remove the extra point added by the second single-click of the double-click
      setPoints(prev => {
        const next = prev.length > 1 ? prev.slice(0, -1) : prev
        requestAnimationFrame(() => updateMapDrawing(next, null))
        return next
      })
      setHoverPt(null)
      setCollecting(false)
    }

    map.on('click', onClick)
    map.on('mousemove', onMove)
    map.on('dblclick', onDblClick)
    clickHRef.current = onClick
    moveHRef.current  = onMove
    dblHRef.current   = onDblClick

    return () => {
      map.off('click', onClick)
      map.off('mousemove', onMove)
      map.off('dblclick', onDblClick)
      clickHRef.current = null
      moveHRef.current  = null
      dblHRef.current   = null
      map.getCanvas().style.cursor = ''
      map.doubleClickZoom.enable()
    }
  }, [map, active, collecting, canCollect, updateMapDrawing])

  // --- cleanup cursor on unmount -------------------------------------------
  useEffect(() => () => { if (map) { map.getCanvas().style.cursor = ''; map.doubleClickZoom.enable() } }, [map])

  // --- clear / undo / new / finish -----------------------------------------
  const clearDrawing = useCallback(() => {
    setPoints([])
    setHoverPt(null)
    setElevProfile(null)
    setElevLoading(false)
    if (!map) return
    const blank = emptyFC()
    for (const sid of [SRC_LINE, SRC_POLY, SRC_PTS]) {
      (map.getSource(sid))?.setData(blank)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map])

  const undoLast = useCallback(() => {
    setPoints(prev => {
      const next = prev.slice(0, -1)
      requestAnimationFrame(() => updateMapDrawing(next, null))
      return next
    })
    setHoverPt(null)
    setElevProfile(null)
  }, [updateMapDrawing])

  const finishMeasurement = useCallback(() => {
    setCollecting(false)
    setHoverPt(null)
    requestAnimationFrame(() => updateMapDrawing(pointsRef.current, null))
  }, [updateMapDrawing])

  const newMeasurement = useCallback(() => {
    clearDrawing()
    setCollecting(true)
    if (!active) onActivate()
  }, [clearDrawing, active, onActivate])

  // --- computed measurements -----------------------------------------------

  const distanceResult = useMemo(() => {
    if (tool !== 'distance' || points.length < 2) return null
    const segs = segmentDistances(points, mode)
    return { segments: segs, total: segs.reduce((a, b) => a + b, 0) }
  }, [tool, points, mode])

  const areaResult = useMemo(() => {
    if (tool !== 'area' || points.length < 3) return null
    const ring: [number, number][] = [...points, points[0]]
    return {
      area: mode === 'geodesic' ? geodesicArea(ring) : planarArea(ring),
      perimeter: polylineLength(ring, mode)
    }
  }, [tool, points, mode])

  // --- elevation profile ---------------------------------------------------
  const computeElev = useCallback(async () => {
    if (!terrainSampler || points.length < 2) return
    setElevLoading(true)
    try {
      const zoom = map?.getZoom() ?? 12
      const n = Math.min(200, Math.max(50, points.length * 30))
      const profile = await sampleElevationProfile(points, terrainSampler, zoom, n)
      setElevProfile(profile)
    } catch (err) {
      console.error('[Geoprocessing] elevation profile error', err)
    } finally {
      setElevLoading(false)
    }
  }, [map, points, terrainSampler])

  // Auto-compute when done collecting (or when enough points)
  useEffect(() => {
    if (tool === 'elevation' && points.length >= 2 && terrainSampler) computeElev()
  }, [tool, points, terrainSampler, computeElev])

  const elevStats = useMemo(() => {
    if (!elevProfile) return null
    const valid = elevProfile.filter(s => s.elevation !== null).map(s => s.elevation as number)
    if (valid.length === 0) return null
    return {
      min: Math.min(...valid),
      max: Math.max(...valid),
      avg: valid.reduce((a, b) => a + b, 0) / valid.length,
      gain: valid.reduce((acc, v, i) => (i > 0 && v > valid[i - 1] ? acc + (v - valid[i - 1]) : acc), 0),
      loss: valid.reduce((acc, v, i) => (i > 0 && v < valid[i - 1] ? acc + (valid[i - 1] - v) : acc), 0),
      totalDist: elevProfile[elevProfile.length - 1]?.distance ?? 0
    }
  }, [elevProfile])

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  const isFinished = !collecting && points.length >= (tool === 'area' ? 3 : 2)

  return (
    <div
      ref={panelRef}
      className="fixed z-[100] shadow-2xl"
      style={{ left: panelPos.x, top: panelPos.y, width: 360, maxHeight: 'calc(100vh - 100px)' }}
      onMouseDown={handlePanelMouseDown}
    >
      <div className={cn(
        'bg-[#0a0a0a]/95 backdrop-blur-xl border rounded-sm overflow-hidden flex flex-col max-h-[calc(100vh-100px)] transition-colors',
        active ? 'border-white/20' : 'border-white/8'
      )}>
        {/* Title bar – draggable */}
        <div
          className="flex items-center justify-between px-3 py-2 border-b border-white/10 cursor-move select-none"
          onMouseDown={handleDragStart}
        >
          <div className="flex items-center gap-2">
            <div className={cn('w-1.5 h-1.5 rounded-full transition-colors', active ? `${cfg.activeDot} animate-pulse` : 'bg-white/20')} />
            <span className={cn('text-xs font-bold uppercase tracking-widest', active ? 'text-white' : 'text-white/50')}>
              {cfg.title}
            </span>
            {isFinished && (
              <span className="flex items-center gap-1 text-[9px] font-mono text-emerald-400/80">
                <CheckCircle2 className="w-3 h-3" />
                Done
              </span>
            )}
          </div>
          <button onClick={onClose} className="p-1 rounded hover:bg-white/10 text-white/50 hover:text-white transition-colors" title="Close">
            <X className="w-3.5 h-3.5" />
          </button>
        </div>

        {/* Not-active overlay hint */}
        {!active && collecting && (
          <div className="px-3 py-1.5 bg-white/[0.03] border-b border-white/5 text-center">
            <span className="text-[9px] font-mono text-white/30 uppercase tracking-widest">Click here to activate</span>
          </div>
        )}

        {/* Mode selector (distance & area) */}
        {(tool === 'distance' || tool === 'area') && (
          <div className="flex items-center gap-2 px-3 py-2 border-b border-white/5">
            <span className="text-[9px] text-white/40 font-mono uppercase tracking-widest">Method</span>
            <div className="flex ml-auto">
              {(['geodesic', 'planar'] as MeasureMode[]).map(m => (
                <button
                  key={m}
                  onClick={() => setMode(m)}
                  className={cn(
                    'px-3 py-1 text-[10px] font-bold uppercase tracking-wider border transition-all',
                    m === 'geodesic' ? 'rounded-l-sm' : 'rounded-r-sm border-l-0',
                    mode === m ? 'bg-white/10 text-white border-white/20' : 'bg-transparent text-white/40 border-white/10 hover:text-white/70'
                  )}
                >
                  {m}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Instruction / status */}
        <div className="px-3 py-2 border-b border-white/5 flex items-center gap-2">
          <MousePointerClick className="w-3 h-3 text-white/30 flex-shrink-0" />
          <span className="text-[10px] text-white/50 font-mono">
            {!canCollect
              ? 'No DEM loaded — load a DEM dataset first'
              : collecting
                ? tool === 'distance'
                  ? 'Click map to add points. Double-click to finish.'
                  : tool === 'area'
                    ? 'Click map to add vertices. Double-click to finish.'
                    : 'Click map to draw profile line. Double-click to finish.'
                : 'Measurement complete. Click New to start over.'}
          </span>
        </div>

        {/* Results */}
        <div className="flex-1 overflow-y-auto min-h-0 scrollbar-thin scrollbar-thumb-white/10 scrollbar-track-transparent">
          {/* ---- Distance ---- */}
          {tool === 'distance' && (
            <div className="px-3 py-2">
              {points.length < 2 ? (
                <div className="text-[10px] text-white/30 font-mono text-center py-4">Add at least 2 points to measure</div>
              ) : (
                <>
                  <div className={cn('p-3 rounded-sm border mb-2 bg-white/[0.02]', cfg.border)}>
                    <div className="text-[9px] text-white/40 font-mono uppercase tracking-widest mb-1">Total Distance ({mode})</div>
                    <div className={cn('text-lg font-mono font-bold', cfg.accent)}>{formatDistance(distanceResult?.total ?? 0)}</div>
                  </div>
                  {distanceResult && distanceResult.segments.length > 0 && (
                    <div className="border border-white/5 rounded-sm overflow-hidden">
                      <div className="px-2 py-1.5 bg-white/[0.03] border-b border-white/5">
                        <span className="text-[9px] text-white/40 font-mono uppercase tracking-widest">Segments</span>
                      </div>
                      <div className="max-h-[180px] overflow-y-auto">
                        {distanceResult.segments.map((d, i) => (
                          <div key={i} className="flex items-center justify-between px-2 py-1 text-[10px] font-mono border-b border-white/[0.03] hover:bg-white/[0.03] transition-colors">
                            <span className="text-white/50">{i + 1} → {i + 2}</span>
                            <span className="text-white/80">{formatDistance(d)}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>
          )}

          {/* ---- Area ---- */}
          {tool === 'area' && (
            <div className="px-3 py-2">
              {points.length < 3 ? (
                <div className="text-[10px] text-white/30 font-mono text-center py-4">Add at least 3 points to measure area</div>
              ) : (
                <>
                  <div className={cn('p-3 rounded-sm border mb-2 bg-white/[0.02]', cfg.border)}>
                    <div className="text-[9px] text-white/40 font-mono uppercase tracking-widest mb-1">Area ({mode})</div>
                    <div className={cn('text-lg font-mono font-bold', cfg.accent)}>{formatArea(areaResult?.area ?? 0)}</div>
                  </div>
                  <div className={cn('p-3 rounded-sm border bg-white/[0.02]', cfg.border)}>
                    <div className="text-[9px] text-white/40 font-mono uppercase tracking-widest mb-1">Perimeter ({mode})</div>
                    <div className="text-sm font-mono font-bold text-white/80">{formatDistance(areaResult?.perimeter ?? 0)}</div>
                  </div>
                </>
              )}
            </div>
          )}

          {/* ---- Elevation Profile ---- */}
          {tool === 'elevation' && (
            <div className="px-3 py-2">
              {!demAvailable ? (
                <div className="text-[10px] text-red-400/70 font-mono text-center py-4">
                  No DEM dataset loaded. Load a DEM/elevation raster first.
                </div>
              ) : points.length < 2 ? (
                <div className="text-[10px] text-white/30 font-mono text-center py-4">Add at least 2 points to generate profile</div>
              ) : elevLoading ? (
                <div className="flex items-center justify-center gap-2 py-6">
                  <div className="w-4 h-4 border-2 border-green-500/30 border-t-green-500 rounded-full animate-spin" />
                  <span className="text-[10px] text-white/50 font-mono">Sampling elevation...</span>
                </div>
              ) : elevProfile && elevStats ? (
                <>
                  <div className="grid grid-cols-3 gap-1.5 mb-2">
                    {[
                      { label: 'Min', value: `${elevStats.min.toFixed(1)} m` },
                      { label: 'Max', value: `${elevStats.max.toFixed(1)} m` },
                      { label: 'Avg', value: `${elevStats.avg.toFixed(1)} m` },
                    ].map(s => (
                      <div key={s.label} className="p-2 rounded-sm border border-green-500/20 bg-white/[0.02] text-center">
                        <div className="text-[8px] text-white/40 font-mono uppercase tracking-widest">{s.label}</div>
                        <div className="text-xs font-mono font-bold text-green-400">{s.value}</div>
                      </div>
                    ))}
                  </div>
                  <div className="grid grid-cols-3 gap-1.5 mb-3">
                    {[
                      { label: 'Elev Gain', value: `${elevStats.gain.toFixed(1)} m` },
                      { label: 'Elev Loss', value: `${elevStats.loss.toFixed(1)} m` },
                      { label: 'Distance', value: formatDistance(elevStats.totalDist) },
                    ].map(s => (
                      <div key={s.label} className="p-2 rounded-sm border border-white/5 bg-white/[0.02] text-center">
                        <div className="text-[8px] text-white/40 font-mono uppercase tracking-widest">{s.label}</div>
                        <div className="text-[10px] font-mono font-bold text-white/70">{s.value}</div>
                      </div>
                    ))}
                  </div>
                  <ElevationChart profile={elevProfile} />
                </>
              ) : null}
            </div>
          )}
        </div>

        {/* Actions bar */}
        <div className="flex items-center gap-1 px-3 py-2 border-t border-white/10">
          {collecting ? (
            <>
              <button
                onClick={undoLast}
                disabled={points.length === 0}
                className="flex items-center gap-1.5 px-2 py-1.5 text-[10px] font-mono text-white/50 hover:text-white hover:bg-white/5 rounded-sm transition-all disabled:opacity-30 disabled:pointer-events-none"
                title="Undo last point"
              >
                <RotateCcw className="w-3 h-3" />
                Undo
              </button>
              <button
                onClick={clearDrawing}
                disabled={points.length === 0}
                className="flex items-center gap-1.5 px-2 py-1.5 text-[10px] font-mono text-white/50 hover:text-red-400 hover:bg-red-500/10 rounded-sm transition-all disabled:opacity-30 disabled:pointer-events-none"
                title="Clear all"
              >
                <Trash2 className="w-3 h-3" />
                Clear
              </button>
              <div className="flex-1" />
              <button
                onClick={finishMeasurement}
                disabled={points.length < (tool === 'area' ? 3 : 2)}
                className={cn(
                  'flex items-center gap-1.5 px-3 py-1.5 text-[10px] font-bold uppercase tracking-wider rounded-sm border transition-all disabled:opacity-30 disabled:pointer-events-none',
                  cfg.border, cfg.accent, 'hover:bg-white/5'
                )}
                title="Finish measurement"
              >
                <CheckCircle2 className="w-3 h-3" />
                Finish
              </button>
            </>
          ) : (
            <>
              <button
                onClick={newMeasurement}
                className={cn(
                  'flex items-center gap-1.5 px-3 py-1.5 text-[10px] font-bold uppercase tracking-wider rounded-sm border transition-all',
                  cfg.border, cfg.accent, 'hover:bg-white/5'
                )}
                title="Start a new measurement"
              >
                <Play className="w-3 h-3" />
                New Measurement
              </button>
              <div className="flex-1" />
              <div className="text-[9px] font-mono text-white/20">{points.length} pt{points.length !== 1 ? 's' : ''}</div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Elevation Profile SVG Chart
// ---------------------------------------------------------------------------

function ElevationChart({ profile }: { profile: ElevationSample[] }) {
  const [hoverIdx, setHoverIdx] = useState<number | null>(null)

  const valid = profile.filter(s => s.elevation !== null)
  if (valid.length < 2) {
    return <div className="text-[10px] text-white/30 font-mono text-center py-3">Not enough elevation data</div>
  }

  const elevations = valid.map(s => s.elevation as number)
  const distances = valid.map(s => s.distance)
  const minElev = Math.min(...elevations)
  const maxElev = Math.max(...elevations)
  const elevRange = maxElev - minElev || 1
  const maxDist = distances[distances.length - 1]

  const W = 340, H = 140
  const PL = 42, PR = 8, PT = 12, PB = 24
  const plotW = W - PL - PR, plotH = H - PT - PB

  const toX = (d: number) => PL + (d / maxDist) * plotW
  const toY = (e: number) => PT + plotH - ((e - minElev) / elevRange) * plotH

  const linePath = valid.map((s, i) => `${i === 0 ? 'M' : 'L'}${toX(s.distance).toFixed(1)},${toY(s.elevation as number).toFixed(1)}`).join(' ')
  const fillPath = `${linePath} L${toX(valid[valid.length - 1].distance).toFixed(1)},${(PT + plotH).toFixed(1)} L${PL.toFixed(1)},${(PT + plotH).toFixed(1)} Z`

  const yTicks = 4
  const yLabels = Array.from({ length: yTicks + 1 }, (_, i) => ({ y: toY(minElev + (elevRange * i) / yTicks), label: (minElev + (elevRange * i) / yTicks).toFixed(0) }))
  const xTicks = 4
  const xLabels = Array.from({ length: xTicks + 1 }, (_, i) => ({ x: toX((maxDist * i) / xTicks), label: (maxDist * i) / xTicks >= 1000 ? `${((maxDist * i) / xTicks / 1000).toFixed(1)}k` : ((maxDist * i) / xTicks).toFixed(0) }))

  const hoveredSample = hoverIdx !== null ? valid[hoverIdx] : null

  const handleMouseMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const distAtMouse = ((e.clientX - rect.left - PL) / plotW) * maxDist
    let closest = 0, bestDelta = Infinity
    for (let i = 0; i < valid.length; i++) {
      const delta = Math.abs(valid[i].distance - distAtMouse)
      if (delta < bestDelta) { bestDelta = delta; closest = i }
    }
    setHoverIdx(closest)
  }

  return (
    <div className="border border-white/5 rounded-sm bg-white/[0.02] p-1">
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ aspectRatio: `${W}/${H}` }} onMouseMove={handleMouseMove} onMouseLeave={() => setHoverIdx(null)}>
        {yLabels.map((yl, i) => <line key={`yg${i}`} x1={PL} y1={yl.y} x2={W - PR} y2={yl.y} stroke="rgba(255,255,255,0.06)" strokeWidth={0.5} />)}
        <path d={fillPath} fill="rgba(76,175,80,0.12)" />
        <path d={linePath} fill="none" stroke="#4caf50" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" />
        {yLabels.map((yl, i) => <text key={`yl${i}`} x={PL - 4} y={yl.y + 3} textAnchor="end" fontSize={8} fontFamily="monospace" fill="rgba(255,255,255,0.35)">{yl.label}m</text>)}
        {xLabels.map((xl, i) => <text key={`xl${i}`} x={xl.x} y={H - 4} textAnchor="middle" fontSize={8} fontFamily="monospace" fill="rgba(255,255,255,0.35)">{xl.label}m</text>)}
        {hoveredSample && (
          <>
            <line x1={toX(hoveredSample.distance)} y1={PT} x2={toX(hoveredSample.distance)} y2={PT + plotH} stroke="rgba(255,255,255,0.25)" strokeWidth={1} strokeDasharray="3,2" />
            <circle cx={toX(hoveredSample.distance)} cy={toY(hoveredSample.elevation as number)} r={3.5} fill="#4caf50" stroke="#fff" strokeWidth={1.5} />
            <rect x={toX(hoveredSample.distance) - 36} y={toY(hoveredSample.elevation as number) - 20} width={72} height={14} rx={2} fill="rgba(0,0,0,0.85)" stroke="rgba(76,175,80,0.4)" strokeWidth={0.5} />
            <text x={toX(hoveredSample.distance)} y={toY(hoveredSample.elevation as number) - 10} textAnchor="middle" fontSize={8} fontFamily="monospace" fill="#4caf50">
              {(hoveredSample.elevation as number).toFixed(1)}m @ {formatDistance(hoveredSample.distance)}
            </text>
          </>
        )}
      </svg>
    </div>
  )
}

