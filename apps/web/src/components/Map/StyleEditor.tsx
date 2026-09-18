'use client'

import { LayerStyleOptions,LineStyle } from '@/lib/map-utils'
import { cn } from '@/lib/utils'
import { Check,CircleDot,Eye,EyeOff,Minus,Paintbrush,Palette,RotateCcw,Square,X } from 'lucide-react'
import React,{ useCallback,useState } from 'react'
import { createPortal } from 'react-dom'

// Line style options with visual preview
const LINE_STYLES: { value: LineStyle; label: string; preview: string }[] = [
  { value: 'solid', label: 'Solid', preview: '━━━━━━━━━━━━' },
  { value: 'dashed', label: 'Dashed', preview: '━━ ━━ ━━ ━━' },
  { value: 'dotted', label: 'Dotted', preview: '• • • • • • • •' },
  { value: 'dash-dot', label: 'Dash-Dot', preview: '━━ • ━━ • ━━' },
  { value: 'long-dash', label: 'Long Dash', preview: '━━━  ━━━  ━━━' },
  { value: 'short-dash', label: 'Short Dash', preview: '━ ━ ━ ━ ━ ━' },
]

interface LayerInfo {
  id: string
  name: string
  type: 'vector' | 'raster'
  geometryType?: string
}

interface StyleEditorProps {
  layer: LayerInfo
  styleDraft: LayerStyleOptions
  onChange: (style: LayerStyleOptions) => void
  onApply: () => void
  onReset: () => void
  onCancel: () => void
}

// Preset colors including category colors and additional options
const PRESET_COLORS = [
  { color: '#f97316', label: 'Roads (Orange)' },
  { color: '#22c55e', label: 'Railways (Green)' },
  { color: '#eab308', label: 'Powerlines (Yellow)' },
  { color: '#06b6d4', label: 'Waterways (Cyan)' },
  { color: '#ef4444', label: 'Pipelines (Red)' },
  { color: '#a855f7', label: 'Purple' },
  { color: '#3b82f6', label: 'Blue' },
  { color: '#ec4899', label: 'Pink' },
  { color: '#14b8a6', label: 'Teal' },
  { color: '#f59e0b', label: 'Amber' },
  { color: '#6366f1', label: 'Indigo' },
  { color: '#84cc16', label: 'Lime' },
]

function ColorPicker({
  value,
  onChange,
  label
}: {
  value: string
  onChange: (color: string) => void
  label: string
}) {
  const [showPicker, setShowPicker] = useState(false)

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-[10px] text-white/50 uppercase tracking-wider">{label}</span>
        <span className="text-[10px] font-mono text-white/30">{value}</span>
      </div>

      <div className="flex items-center gap-2">
        {/* Current color display + native picker */}
        <div className="relative">
          <button
            onClick={() => setShowPicker(!showPicker)}
            className="w-8 h-8 rounded-sm border border-white/20 hover:border-white/40 transition-colors shadow-inner"
            style={{ backgroundColor: value }}
          />
          <input
            type="color"
            value={value}
            onChange={(e) => onChange(e.target.value)}
            className="absolute inset-0 opacity-0 cursor-pointer"
          />
        </div>

        {/* Preset color swatches */}
        <div className="flex-1 flex flex-wrap gap-1">
          {PRESET_COLORS.slice(0, 6).map((preset) => (
            <button
              key={preset.color}
              onClick={() => onChange(preset.color)}
              className={cn(
                "w-5 h-5 rounded-sm border transition-all hover:scale-110",
                value === preset.color
                  ? "border-white ring-1 ring-white/50"
                  : "border-white/10 hover:border-white/30"
              )}
              style={{ backgroundColor: preset.color }}
              title={preset.label}
            />
          ))}
        </div>
      </div>

      {/* More colors row */}
      <div className="flex flex-wrap gap-1">
        {PRESET_COLORS.slice(6).map((preset) => (
          <button
            key={preset.color}
            onClick={() => onChange(preset.color)}
            className={cn(
              "w-5 h-5 rounded-sm border transition-all hover:scale-110",
              value === preset.color
                ? "border-white ring-1 ring-white/50"
                : "border-white/10 hover:border-white/30"
            )}
            style={{ backgroundColor: preset.color }}
            title={preset.label}
          />
        ))}
      </div>
    </div>
  )
}

function SliderControl({
  value,
  onChange,
  min,
  max,
  step,
  label,
  unit = '',
  formatValue = (v) => v.toString()
}: {
  value: number
  onChange: (value: number) => void
  min: number
  max: number
  step: number
  label: string
  unit?: string
  formatValue?: (value: number) => string
}) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-[10px] text-white/50 uppercase tracking-wider">{label}</span>
        <span className="text-[10px] font-mono text-primary">{formatValue(value)}{unit}</span>
      </div>
      <div className="relative h-2 bg-white/5 rounded-full overflow-hidden">
        <div
          className="absolute top-0 left-0 bottom-0 bg-primary/50 rounded-full"
          style={{ width: `${((value - min) / (max - min)) * 100}%` }}
        />
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          className="absolute inset-0 w-full opacity-0 cursor-pointer"
        />
      </div>
    </div>
  )
}

export function StyleEditor({
  layer,
  styleDraft,
  onChange,
  onApply,
  onReset,
  onCancel
}: StyleEditorProps) {
  const [mounted, setMounted] = React.useState(false)
  const [livePreview, setLivePreview] = useState(true)
  const [isClosing, setIsClosing] = useState(false)

  React.useEffect(() => {
    setMounted(true)
  }, [])

  const handleClose = useCallback(() => {
    setIsClosing(true)
    setTimeout(() => {
      onCancel()
    }, 150)
  }, [onCancel])

  const handleApply = useCallback(() => {
    setIsClosing(true)
    setTimeout(() => {
      onApply()
    }, 150)
  }, [onApply])

  const handleReset = useCallback(() => {
    onReset()
  }, [onReset])

  // Determine which controls to show based on layer type
  const isRaster = layer.type === 'raster'
  const geomType = layer.geometryType?.toLowerCase() || 'mixed'
  const showFill = !isRaster && (geomType === 'polygon' || geomType === 'mixed')
  const showLine = !isRaster && (geomType === 'polygon' || geomType === 'line' || geomType === 'mixed')
  const showPoint = !isRaster && (geomType === 'point' || geomType === 'mixed')

  // Get geometry icon
  const GeomIcon = geomType === 'polygon' ? Square : geomType === 'line' ? Minus : geomType === 'point' ? CircleDot : Palette

  if (!mounted) return null

  const content = (
    <div
      className={cn(
        "fixed inset-0 z-[100] flex items-center justify-center p-4 transition-all duration-150",
        isClosing ? "opacity-0" : "opacity-100"
      )}
      onClick={handleClose}
    >
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" />

      {/* Dialog */}
      <div
        className={cn(
          "relative w-full max-w-md bg-[#0c0c0c] border border-white/10 rounded-sm shadow-2xl overflow-hidden transition-all duration-150",
          isClosing ? "scale-95 opacity-0" : "scale-100 opacity-100"
        )}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Background pattern */}
        <div
          className="absolute inset-0 opacity-[0.02]"
          style={{
            backgroundImage: `linear-gradient(rgba(255,255,255,0.03) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.03) 1px, transparent 1px)`,
            backgroundSize: '20px 20px'
          }}
        />

        {/* Header */}
        <header className="relative px-5 py-4 border-b border-white/10 flex items-center justify-between bg-black/30">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-primary/10 rounded-sm border border-primary/20">
              <Paintbrush className="w-4 h-4 text-primary" />
            </div>
            <div className="flex flex-col">
              <span className="text-[10px] text-white/40 uppercase tracking-wider">Layer Styler</span>
              <span className="text-sm font-bold text-white truncate max-w-[200px]">{layer.name}</span>
            </div>
          </div>

          <div className="flex items-center gap-2">
            {/* Layer type badge */}
            <div className="flex items-center gap-1.5 px-2 py-1 bg-white/5 border border-white/10 rounded-sm">
              <GeomIcon className="w-3 h-3 text-white/50" />
              <span className="text-[9px] font-mono text-white/50 uppercase">
                {isRaster ? 'Raster' : geomType}
              </span>
            </div>

            <button
              onClick={handleClose}
              className="p-1.5 hover:bg-white/10 rounded-sm transition-colors text-white/50 hover:text-white"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </header>

        {/* Content */}
        <div className="relative p-5 space-y-5 max-h-[60vh] overflow-y-auto">

          {/* Live Preview Toggle */}
          <div className="flex items-center justify-between p-3 bg-white/[0.02] border border-white/5 rounded-sm">
            <div className="flex items-center gap-2">
              {livePreview ? (
                <Eye className="w-4 h-4 text-emerald-400" />
              ) : (
                <EyeOff className="w-4 h-4 text-white/30" />
              )}
              <span className="text-xs text-white/70">Live Preview</span>
            </div>
            <button
              onClick={() => setLivePreview(!livePreview)}
              className={cn(
                "relative w-10 h-5 rounded-full transition-colors",
                livePreview ? "bg-emerald-500/30" : "bg-white/10"
              )}
            >
              <div
                className={cn(
                  "absolute top-0.5 w-4 h-4 rounded-full transition-all",
                  livePreview
                    ? "left-[22px] bg-emerald-400"
                    : "left-0.5 bg-white/30"
                )}
              />
            </button>
          </div>

          {/* Global Opacity (all layer types) */}
          <div className="p-4 bg-white/[0.02] border border-white/5 rounded-sm space-y-3">
            <div className="text-[10px] text-white/30 font-mono uppercase tracking-widest border-b border-white/5 pb-2">
              Opacity
            </div>
            <SliderControl
              value={styleDraft.opacity ?? 1}
              onChange={(v) => onChange({ ...styleDraft, opacity: v })}
              min={0}
              max={1}
              step={0.05}
              label="Layer Opacity"
              formatValue={(v) => Math.round(v * 100).toString()}
              unit="%"
            />
          </div>

          {/* Fill Style (polygons) */}
          {showFill && (
            <div className="p-4 bg-white/[0.02] border border-white/5 rounded-sm space-y-3">
              <div className="flex items-center gap-2 text-[10px] text-white/30 font-mono uppercase tracking-widest border-b border-white/5 pb-2">
                <Square className="w-3 h-3" />
                Fill Style
              </div>
              <ColorPicker
                value={styleDraft.fillColor ?? '#22d3ee'}
                onChange={(c) => onChange({ ...styleDraft, fillColor: c })}
                label="Fill Color"
              />
            </div>
          )}

          {/* Line/Stroke Style */}
          {showLine && (
            <div className="p-4 bg-white/[0.02] border border-white/5 rounded-sm space-y-3">
              <div className="flex items-center gap-2 text-[10px] text-white/30 font-mono uppercase tracking-widest border-b border-white/5 pb-2">
                <Minus className="w-3 h-3" />
                {showFill ? 'Stroke Style' : 'Line Style'}
              </div>
              <ColorPicker
                value={styleDraft.lineColor ?? '#06b6d4'}
                onChange={(c) => onChange({ ...styleDraft, lineColor: c })}
                label={showFill ? 'Stroke Color' : 'Line Color'}
              />
              <SliderControl
                value={styleDraft.lineWidth ?? 2}
                onChange={(v) => onChange({ ...styleDraft, lineWidth: v })}
                min={0.5}
                max={8}
                step={0.5}
                label="Line Width"
                unit="px"
              />

              {/* Line Pattern Selector */}
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-[10px] text-white/50 uppercase tracking-wider">Line Pattern</span>
                  <span className="text-[10px] font-mono text-white/30 capitalize">{styleDraft.lineStyle ?? 'solid'}</span>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  {LINE_STYLES.map((style) => (
                    <button
                      key={style.value}
                      onClick={() => onChange({
                        ...styleDraft,
                        lineStyle: style.value
                      })}
                      className={cn(
                        "flex flex-col items-center gap-1.5 p-2 rounded-sm border transition-all",
                        (styleDraft.lineStyle ?? 'solid') === style.value
                          ? "bg-primary/10 border-primary/40 text-white"
                          : "bg-white/[0.02] border-white/10 text-white/60 hover:bg-white/5 hover:border-white/20"
                      )}
                    >
                      {/* SVG Line Preview */}
                      <svg width="60" height="12" className="overflow-visible">
                        <line
                          x1="0"
                          y1="6"
                          x2="60"
                          y2="6"
                          stroke="currentColor"
                          strokeWidth="2"
                          strokeLinecap="round"
                          strokeDasharray={
                            style.value === 'solid' ? 'none' :
                            style.value === 'dashed' ? '8,4' :
                            style.value === 'dotted' ? '2,4' :
                            style.value === 'dash-dot' ? '8,4,2,4' :
                            style.value === 'long-dash' ? '12,6' :
                            '4,4'
                          }
                        />
                      </svg>
                      <span className="text-[9px] uppercase tracking-wide">{style.label}</span>
                    </button>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* Point Style */}
          {showPoint && (
            <div className="p-4 bg-white/[0.02] border border-white/5 rounded-sm space-y-3">
              <div className="flex items-center gap-2 text-[10px] text-white/30 font-mono uppercase tracking-widest border-b border-white/5 pb-2">
                <CircleDot className="w-3 h-3" />
                Point Style
              </div>
              <ColorPicker
                value={styleDraft.pointColor ?? '#22d3ee'}
                onChange={(c) => onChange({ ...styleDraft, pointColor: c })}
                label="Point Color"
              />
              <SliderControl
                value={styleDraft.pointSize ?? 6}
                onChange={(v) => onChange({ ...styleDraft, pointSize: v })}
                min={2}
                max={16}
                step={1}
                label="Point Size"
                unit="px"
              />
            </div>
          )}

          {/* Raster-only options */}
          {isRaster && (
            <div className="p-3 bg-amber-500/5 border border-amber-500/20 rounded-sm">
              <p className="text-[10px] text-amber-500/70">
                Raster layers only support opacity adjustment. Color modifications require raster processing.
              </p>
            </div>
          )}
        </div>

        {/* Footer */}
        <footer className="relative px-5 py-4 border-t border-white/10 bg-black/30 flex items-center justify-between">
          <button
            onClick={handleReset}
            className="flex items-center gap-2 px-3 py-2 text-white/50 hover:text-white hover:bg-white/5 rounded-sm transition-colors text-xs"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            Reset to Default
          </button>

          <div className="flex items-center gap-2">
            <button
              onClick={handleClose}
              className="px-4 py-2 text-white/70 hover:text-white hover:bg-white/5 border border-white/10 rounded-sm transition-colors text-xs font-medium"
            >
              Cancel
            </button>
            <button
              onClick={handleApply}
              className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground hover:bg-primary/90 rounded-sm transition-colors text-xs font-medium"
            >
              <Check className="w-3.5 h-3.5" />
              Apply Style
            </button>
          </div>
        </footer>
      </div>
    </div>
  )

  return createPortal(content, document.body)
}

