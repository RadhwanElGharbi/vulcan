import { AOI_LAYER_HINTS,ManagedLayer,VectorDetail,formatMetadata } from '@/lib/map-utils'
import { cn } from '@/lib/utils'
import {
ArrowDown,
ArrowUp,
ChevronDown,
ChevronRight,
Eye,
EyeOff,
Info,
Loader2,
Minus,
Paintbrush,
Plus,
Table,
Terminal
} from 'lucide-react'
import React,{ useEffect,useMemo,useRef,useState } from 'react'

interface LayerManagerProps {
  layers: ManagedLayer[]
  selectedLayerId: string | null
  loadingMessage: string | null
  currentProject: string | null
  vectorDetails: Record<string, VectorDetail>
  onSelectLayer: (id: string) => void
  onToggleVisibility: (id: string) => void
  onOpacityChange: (id: string, value: number) => void
  onMoveLayer: (id: string, direction: 'up' | 'down') => void
  onReorderLayers: (draggedId: string, targetId: string, position: 'above' | 'below') => void
  onOpenTable: (layerId: string) => void
  onOpenStyle: (layerId: string) => void
  onOpenDatasetIndexForLayer: (layerId: string) => void
}

export function LayerManager({
  layers,
  selectedLayerId,
  loadingMessage,
  currentProject,
  vectorDetails,
  onSelectLayer,
  onToggleVisibility,
  onOpacityChange,
  onMoveLayer,
  onReorderLayers,
  onOpenTable,
  onOpenStyle,
  onOpenDatasetIndexForLayer
}: LayerManagerProps) {
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>({
    aoi: false,
    rasters: false,
    vectors: false
  })
  const [draggedId, setDraggedId] = useState<string | null>(null)
  const [dropTarget, setDropTarget] = useState<{ id: string; position: 'above' | 'below' } | null>(null)
  const inspectorScrollRef = useRef<HTMLDivElement | null>(null)

  const selectedLayer = layers.find(layer => layer.id === selectedLayerId)
  const selectedDetails = selectedLayer ? vectorDetails[selectedLayer.id] : null
  // Sort layers descending by order (Highest order = Top of list = Top of map)
  const orderedLayers = [...layers].sort((a, b) => b.order - a.order)

  const isAoiLayer = (layer: ManagedLayer) => {
    if (layer.isAoi) return true
    if (layer.id === 'start-point' || layer.id === 'end-point') return true
    const nameLower = (layer.name || '').toLowerCase()
    if (AOI_LAYER_HINTS.some((hint) => nameLower.includes(hint))) return true
    const path = (layer.path || '').toLowerCase()
    if (path.includes('/aoi/')) return true
    return false
  }

  const grouped = useMemo(() => {
    const aoi = orderedLayers.filter((l) => isAoiLayer(l))
    const rasters = orderedLayers.filter((l) => l.type === 'raster')
    const vectors = orderedLayers.filter((l) => l.type === 'vector' && !isAoiLayer(l))
    return { aoi, rasters, vectors }
  }, [orderedLayers])

  useEffect(() => {
    if (!selectedLayer?.id) return
    if (!inspectorScrollRef.current) return
    inspectorScrollRef.current.scrollTop = 0
  }, [selectedLayer?.id])

  const groupMeta = useMemo(() => {
    const compute = (items: ManagedLayer[]) => {
      const total = items.length
      const visible = items.filter((l) => l.visible).length
      return {
        total,
        visible,
        allVisible: total > 0 && visible === total,
        allHidden: total > 0 && visible === 0
      }
    }
    return {
      aoi: compute(grouped.aoi),
      rasters: compute(grouped.rasters),
      vectors: compute(grouped.vectors)
    }
  }, [grouped])

  const toggleGroupVisibility = (items: ManagedLayer[]) => {
    if (!items.length) return
    const targetVisible = items.some((l) => !l.visible)
    items.forEach((layer) => {
      if (layer.visible !== targetVisible) {
        onToggleVisibility(layer.id)
      }
    })
  }

  const renderGroupHeader = (key: 'aoi' | 'rasters' | 'vectors', label: string) => {
    const meta = groupMeta[key]
    const collapsed = collapsedGroups[key]
    const hasLayers = meta.total > 0
    const eyeTitle = meta.allVisible ? `Hide all ${label}` : `Show all ${label}`

    const groupLayers = key === 'aoi' ? grouped.aoi : key === 'rasters' ? grouped.rasters : grouped.vectors

    return (
      <div className="flex items-center justify-between px-1 py-1">
        <button
          type="button"
          className={cn(
            "flex items-center gap-1.5 min-w-0 text-left",
            hasLayers ? "text-white/60 hover:text-white/80" : "text-white/25 cursor-not-allowed"
          )}
          onClick={() => {
            if (!hasLayers) return
            setCollapsedGroups((prev) => ({ ...prev, [key]: !prev[key] }))
          }}
          title={hasLayers ? `Toggle ${label} group` : `No ${label} loaded`}
          disabled={!hasLayers}
        >
          <span className="text-white/30">
            {collapsed ? <ChevronRight className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
          </span>
          <span className="text-[9px] font-bold uppercase tracking-widest truncate">{label}</span>
          <span className="text-[8px] font-mono text-white/25 shrink-0">
            {meta.total === 0 ? '0' : `${meta.visible}/${meta.total}`}
          </span>
        </button>

        <button
          type="button"
          className={cn(
            "p-0.5 rounded transition-colors shrink-0",
            !hasLayers
              ? "text-white/10 cursor-not-allowed"
              : meta.allHidden
                ? "text-white/20 hover:text-white/50"
                : "text-emerald-400/80 hover:text-emerald-400"
          )}
          onClick={() => hasLayers && toggleGroupVisibility(groupLayers)}
          title={eyeTitle}
          disabled={!hasLayers}
        >
          {meta.allHidden ? <EyeOff className="w-3 h-3" /> : <Eye className="w-3 h-3" />}
        </button>
      </div>
    )
  }

  const handleDragStart = (e: React.DragEvent, id: string) => {
    setDraggedId(id)
    e.dataTransfer.effectAllowed = 'move'
    // Ghost image
    const ghost = document.createElement('div')
    ghost.style.width = '200px'
    ghost.style.height = '40px'
    ghost.style.backgroundColor = '#333'
    ghost.style.opacity = '0.8'
    ghost.innerText = layers.find(l => l.id === id)?.name || 'Layer'
    ghost.style.position = 'absolute'
    ghost.style.top = '-1000px'
    document.body.appendChild(ghost)
    e.dataTransfer.setDragImage(ghost, 0, 0)
    setTimeout(() => document.body.removeChild(ghost), 0)
  }

  const handleDragOver = (e: React.DragEvent, id: string) => {
    e.preventDefault()
    if (id === draggedId) {
        setDropTarget(null)
        return
    }

    const rect = e.currentTarget.getBoundingClientRect()
    const midY = rect.top + rect.height / 2
    const position = e.clientY < midY ? 'above' : 'below'
    
    setDropTarget({ id, position })
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    if (draggedId && dropTarget) {
        onReorderLayers(draggedId, dropTarget.id, dropTarget.position)
    }
    setDraggedId(null)
    setDropTarget(null)
  }

  const clampOpacity = (value: number) => {
    return Math.min(1, Math.max(0, Math.round(value * 100) / 100))
  }

  const renderLayerRow = (layer: ManagedLayer) => {
    const displayName = typeof layer.metadata?.dataset_name === 'string' ? layer.metadata.dataset_name : layer.name
    const isSelected = selectedLayerId === layer.id
    const isError = layer.status === 'error'
    const isDragged = draggedId === layer.id

    return (
      <div
        key={layer.id}
        draggable
        onDragStart={(e) => handleDragStart(e, layer.id)}
        onDragOver={(e) => handleDragOver(e, layer.id)}
        onDrop={handleDrop}
        title={layer.message || displayName}
        className={cn(
          "group relative flex items-start gap-2.5 px-3 py-2.5 border rounded-none transition-all duration-150 cursor-pointer select-none",
          isSelected
            ? "bg-primary/[0.08] border-primary/30"
            : "bg-white/[0.02] border-white/[0.05] hover:bg-white/[0.05] hover:border-white/[0.1]",
          isError && "bg-destructive/[0.06] border-destructive/20",
          isDragged && "opacity-40 scale-[0.98]"
        )}
        onClick={() => onSelectLayer(layer.id)}
        onDoubleClick={() => onOpenDatasetIndexForLayer(layer.id)}
      >
        {/* Drop Indicators */}
        {dropTarget?.id === layer.id && (
          <div
            className={cn(
              "absolute left-0 right-0 h-0.5 bg-primary z-50 shadow-[0_0_8px_rgba(var(--primary),0.8)]",
              dropTarget.position === 'above' ? "top-0" : "bottom-0"
            )}
          />
        )}

        {/* Left accent bar for selected */}
        {isSelected && (
          <div className="absolute left-0 top-1.5 bottom-1.5 w-[2px] bg-primary rounded-full" />
        )}

        {/* Visibility Toggle */}
        <button
          onClick={(e) => {
            e.stopPropagation()
            onToggleVisibility(layer.id)
          }}
          className={cn(
            "mt-0.5 p-1 rounded transition-colors shrink-0 z-10",
            layer.visible
              ? "text-emerald-400 bg-emerald-500/10"
              : "text-white/20 hover:text-white/40"
          )}
        >
          {layer.visible ? <Eye className="w-3.5 h-3.5" /> : <EyeOff className="w-3.5 h-3.5" />}
        </button>

        {/* Content */}
        <div className="flex-1 min-w-0 flex flex-col gap-1.5 z-10">
          <div className="flex items-center justify-between gap-2">
            <span
              className={cn(
                "text-[11px] font-medium truncate leading-tight",
                isError ? "text-destructive" : isSelected ? "text-white" : "text-white/80 group-hover:text-white"
              )}
            >
              {displayName}
            </span>

            {/* Status badge */}
            {layer.status !== 'ready' ? (
              <span
                className={cn(
                  "text-[8px] uppercase font-bold px-1.5 py-0.5 rounded-sm shrink-0",
                  isError ? "bg-destructive/15 text-destructive/80" : "bg-amber-500/15 text-amber-500/80"
                )}
              >
                {isError ? "ERR" : "LOAD"}
              </span>
            ) : (
              <div
                className={cn(
                  "w-1.5 h-1.5 rounded-full transition-colors shrink-0",
                  layer.visible ? "bg-emerald-500 shadow-[0_0_4px_rgba(16,185,129,0.6)]" : "bg-white/10"
                )}
              />
            )}
          </div>

          {isSelected && (
            <>
              {/* Visibility/Opacity control */}
              <div className="flex items-center gap-1.5">
                <button
                  onClick={(e) => {
                    e.stopPropagation()
                    onOpacityChange(layer.id, clampOpacity(layer.opacity - 0.1))
                  }}
                  className="p-0.5 rounded-sm text-white/35 hover:text-white/70 hover:bg-white/10 transition-colors shrink-0"
                  title="Decrease opacity"
                >
                  <Minus className="w-3 h-3" />
                </button>

                <div className="relative h-5 flex-1">
                  <div className="absolute left-0 right-0 top-1/2 -translate-y-1/2 h-1 rounded-full bg-white/[0.08]" />
                  <div
                    className={cn(
                      "absolute left-0 top-1/2 -translate-y-1/2 h-1 rounded-full transition-all duration-200",
                      layer.visible ? "bg-primary/75" : "bg-white/20"
                    )}
                    style={{ width: `${layer.opacity * 100}%` }}
                  />
                  <div
                    className={cn(
                      "absolute top-1/2 -translate-x-1/2 -translate-y-1/2 w-3 h-3 rounded-full border shadow-sm transition-colors",
                      layer.visible
                        ? "bg-primary border-primary/80 shadow-[0_0_8px_rgba(var(--primary),0.45)]"
                        : "bg-white/20 border-white/30"
                    )}
                    style={{ left: `${layer.opacity * 100}%` }}
                  />
                  <input
                    type="range"
                    min={0}
                    max={1}
                    step={0.05}
                    value={layer.opacity}
                    onChange={(e) => onOpacityChange(layer.id, Number(e.target.value))}
                    onClick={(e) => e.stopPropagation()}
                    className="absolute inset-0 w-full h-full opacity-0 cursor-ew-resize"
                    title="Layer opacity"
                  />
                </div>

                <button
                  onClick={(e) => {
                    e.stopPropagation()
                    onOpacityChange(layer.id, clampOpacity(layer.opacity + 0.1))
                  }}
                  className="p-0.5 rounded-sm text-white/35 hover:text-white/70 hover:bg-white/10 transition-colors shrink-0"
                  title="Increase opacity"
                >
                  <Plus className="w-3 h-3" />
                </button>

                <span className="text-[8px] w-7 text-right tabular-nums text-white/35 shrink-0">
                  {Math.round(layer.opacity * 100)}%
                </span>
              </div>
            </>
          )}
        </div>

        {/* Reorder Controls */}
        <div className="flex flex-col -space-y-px opacity-0 group-hover:opacity-100 transition-opacity shrink-0 z-10 mt-0.5">
          <button
            onClick={(e) => {
              e.stopPropagation()
              onMoveLayer(layer.id, 'up')
            }}
            className="p-0.5 hover:bg-white/10 rounded-t-sm disabled:opacity-20 text-white/30 hover:text-primary"
            disabled={orderedLayers[0]?.id === layer.id}
          >
            <ArrowUp className="w-2.5 h-2.5" />
          </button>
          <button
            onClick={(e) => {
              e.stopPropagation()
              onMoveLayer(layer.id, 'down')
            }}
            className="p-0.5 hover:bg-white/10 rounded-b-sm disabled:opacity-20 text-white/30 hover:text-primary"
            disabled={orderedLayers[orderedLayers.length - 1]?.id === layer.id}
          >
            <ArrowDown className="w-2.5 h-2.5" />
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="w-full h-full min-h-0 font-mono flex flex-col">
      {/* Main Layer List Panel */}
      <div className={cn("bg-transparent flex flex-col min-h-0", selectedLayer ? "h-3/4" : "flex-1")}>

        {/* Status / Empty State */}
        {!currentProject && (
          <div className="p-4 text-center text-white/40 text-xs border-b border-white/5">
            <Terminal className="w-8 h-8 mx-auto mb-2 opacity-20" />
            NO ACTIVE PROJECT LINKED
          </div>
        )}

        {currentProject && orderedLayers.length === 0 && (
          <div className="p-4 text-center text-white/40 text-xs border-b border-white/5">
            NO DATASETS IN BUFFER
          </div>
        )}

        {loadingMessage && (
          <div className="px-3 py-2 border-b border-white/5">
            <div className="inline-flex items-center gap-1.5 px-2 py-1 bg-amber-500/10 border border-amber-500/20 rounded-sm">
              <Loader2 className="w-3 h-3 animate-spin text-amber-500" />
              <span className="text-[10px] text-amber-500 uppercase tracking-wider">{loadingMessage}</span>
            </div>
          </div>
        )}

        {/* Layer List */}
        <div
            className="px-2 py-2 space-y-0.5 flex-1 min-h-0 overflow-y-auto"
            onMouseLeave={() => setDropTarget(null)}
        >
          <div className="space-y-3">
            <div className="space-y-1.5">
              {renderGroupHeader('aoi', 'AOI')}
              {!collapsedGroups.aoi && grouped.aoi.length > 0 && (
                <div className="space-y-1">{grouped.aoi.map((layer) => renderLayerRow(layer))}</div>
              )}
            </div>

            <div className="space-y-1.5">
              {renderGroupHeader('rasters', 'RASTERS')}
              {!collapsedGroups.rasters && grouped.rasters.length > 0 && (
                <div className="space-y-1">{grouped.rasters.map((layer) => renderLayerRow(layer))}</div>
              )}
            </div>

            <div className="space-y-1.5">
              {renderGroupHeader('vectors', 'VECTORS')}
              {!collapsedGroups.vectors && grouped.vectors.length > 0 && (
                <div className="space-y-1">{grouped.vectors.map((layer) => renderLayerRow(layer))}</div>
              )}
            </div>
          </div>
        </div>
      </div>

      {selectedLayer && (
        <div className="h-1/3 min-h-[180px] border-t border-white/[0.06] bg-transparent flex flex-col">
          <div className="flex items-center justify-between px-3 py-2 border-b border-white/10 bg-white/[0.02]">
            <div className="flex items-center gap-2 text-xs font-bold text-white uppercase tracking-wider">
              <Info className="w-3.5 h-3.5 text-primary" />
              <span>Inspector</span>
            </div>
            <span className="text-[9px] font-mono text-white/40 uppercase px-1.5 py-0.5 border border-white/10 rounded-sm">
              {selectedLayer.type}
            </span>
          </div>

          <div ref={inspectorScrollRef} className="flex-1 min-h-0 overflow-y-auto p-3 space-y-4 bg-black/20">
            {/* Basic Info */}
            <div className="space-y-2">
                <div className="text-[10px] text-white/30 font-mono uppercase tracking-widest border-b border-white/5 pb-1">Properties</div>
                <div className="grid grid-cols-2 gap-2 text-[10px]">
                    <div className="text-white/50">Visibility State</div>
                    <div className={selectedLayer.visible ? "text-emerald-400" : "text-amber-500"}>{selectedLayer.visible ? 'ACTIVE' : 'HIDDEN'}</div>
                    
                    <div className="text-white/50">Opacity Level</div>
                    <div className="font-mono">{Math.round(selectedLayer.opacity * 100)}%</div>
                    
                    {selectedLayer.featureCount !== undefined && (
                        <>
                            <div className="text-white/50">Feature Count</div>
                            <div className="font-mono text-primary">{selectedLayer.featureCount}</div>
                        </>
                    )}
                    {selectedLayer.geometryType && (
                        <>
                            <div className="text-white/50">Geometry</div>
                            <div className="font-mono uppercase">{selectedLayer.geometryType}</div>
                        </>
                    )}
                </div>
            </div>

            {/* Metadata Table */}
            {selectedLayer.metadata && (
              <div className="space-y-2">
                <div className="text-[10px] text-white/30 font-mono uppercase tracking-widest border-b border-white/5 pb-1">Metadata</div>
                {formatMetadata(selectedLayer.metadata).length > 0 ? (
                  <div className="overflow-x-auto border border-white/5 rounded-sm bg-black/40">
                    <table className="w-full text-left border-collapse text-[10px]">
                      <tbody>
                        {formatMetadata(selectedLayer.metadata).map((row, idx) => (
                          <tr key={idx} className="border-b border-white/5 last:border-0 hover:bg-white/5">
                            <td className="py-1 px-2 font-medium text-white/50 border-r border-white/5 whitespace-nowrap w-20 bg-white/[0.02]">
                              {row.label}
                            </td>
                            <td className="py-1 px-2 text-white/80 break-all font-mono">
                              {row.value}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <pre className="bg-black/40 p-2 rounded-sm border border-white/5 text-[9px] text-white/60 overflow-x-auto font-mono">
                    {JSON.stringify(selectedLayer.metadata, null, 2)}
                  </pre>
                )}
              </div>
            )}

            {/* Actions */}
            {selectedDetails && selectedDetails.properties.length > 0 && (
              <div className="space-y-2 pt-2">
                 <div className="flex items-center justify-between">
                    <div className="text-[10px] text-white/30 font-mono uppercase tracking-widest">Data Actions</div>
                 </div>
                 <div className="flex gap-2">
                    <button
                        onClick={() => onOpenTable(selectedLayer.id)}
                        className="flex-1 flex items-center justify-center gap-2 px-3 py-2 bg-white/5 border border-white/10 hover:bg-primary/10 hover:border-primary/30 hover:text-white text-white/70 rounded-sm transition-all text-[10px] uppercase font-bold tracking-wide"
                    >
                        <Table className="w-3 h-3" />
                        Data Table
                    </button>
                    <button
                        onClick={() => onOpenStyle(selectedLayer.id)}
                        className="flex-1 flex items-center justify-center gap-2 px-3 py-2 bg-white/5 border border-white/10 hover:bg-primary/10 hover:border-primary/30 hover:text-white text-white/70 rounded-sm transition-all text-[10px] uppercase font-bold tracking-wide"
                    >
                        <Paintbrush className="w-3 h-3" />
                        Styler
                    </button>
                 </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
