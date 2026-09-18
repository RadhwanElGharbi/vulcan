'use client'

import { cn } from '@/lib/utils'
import { Info,X } from 'lucide-react'
import { useEffect,useMemo,useState } from 'react'
import { createPortal } from 'react-dom'

export type GoToCoordinateFormat = 'DD' | 'DMS' | 'DDM' | 'UTM' | 'MGRS'

type Props = {
  open: boolean
  seed?: { lng: number; lat: number } | null
  onClose: () => void
  onGoTo: (lng: number, lat: number) => void
}

type ParseResult = { ok: true; lng: number; lat: number } | { ok: false; error: string }

function validateLonLat(lng: number, lat: number): ParseResult {
  if (!Number.isFinite(lng) || !Number.isFinite(lat)) return { ok: false, error: 'Invalid coordinate values.' }
  if (lat < -90 || lat > 90) return { ok: false, error: 'Latitude must be between -90 and 90.' }
  if (lng < -180 || lng > 180) return { ok: false, error: 'Longitude must be between -180 and 180.' }
  return { ok: true, lng, lat }
}

function parseDecimalDegrees(value: string, axis: 'lng' | 'lat'): number | null {
  const raw = value.trim()
  if (!raw) return null
  const upper = raw.toUpperCase()

  let signFromCardinal: number | null = null
  if (/[WS]/.test(upper)) signFromCardinal = -1
  if (/[EN]/.test(upper)) signFromCardinal = 1

  const m = upper.match(/-?\d+(?:\.\d+)?/)
  if (!m) return null

  let n = Number(m[0])
  if (!Number.isFinite(n)) return null

  if (signFromCardinal !== null) n = Math.abs(n) * signFromCardinal

  if (axis === 'lat' && (n < -90 || n > 90)) return null
  if (axis === 'lng' && (n < -180 || n > 180)) return null
  return n
}

function parseDmsLike(value: string, axis: 'lng' | 'lat', kind: 'DMS' | 'DDM'): number | null {
  const raw = value.trim()
  if (!raw) return null
  const upper = raw.toUpperCase()

  let sign = 1
  if (/[WS]/.test(upper)) sign = -1
  if (/[EN]/.test(upper)) sign = 1
  if (/^\s*-/.test(upper)) sign = -1

  const parts = upper.match(/\d+(?:\.\d+)?/g)?.map(Number) ?? []
  if (parts.length === 0) return null

  const deg = parts[0]
  const min = parts[1] ?? 0
  const sec = kind === 'DMS' ? (parts[2] ?? 0) : 0

  if (!Number.isFinite(deg) || !Number.isFinite(min) || !Number.isFinite(sec)) return null
  if (min >= 60 || sec >= 60) return null

  const dd = Math.abs(deg) + min / 60 + sec / 3600
  const val = dd * sign

  if (axis === 'lat' && (val < -90 || val > 90)) return null
  if (axis === 'lng' && (val < -180 || val > 180)) return null
  return val
}

async function parseInputs(format: GoToCoordinateFormat, x: string, y: string): Promise<ParseResult> {
  switch (format) {
    case 'DD': {
      const lng = parseDecimalDegrees(x, 'lng')
      const lat = parseDecimalDegrees(y, 'lat')
      if (lng === null || lat === null) return { ok: false, error: 'Enter valid longitude and latitude (DD).' }
      return validateLonLat(lng, lat)
    }
    case 'DMS': {
      const lng = parseDmsLike(x, 'lng', 'DMS')
      const lat = parseDmsLike(y, 'lat', 'DMS')
      if (lng === null || lat === null) return { ok: false, error: 'Enter valid longitude and latitude (DMS).' }
      return validateLonLat(lng, lat)
    }
    case 'DDM': {
      const lng = parseDmsLike(x, 'lng', 'DDM')
      const lat = parseDmsLike(y, 'lat', 'DDM')
      if (lng === null || lat === null) return { ok: false, error: 'Enter valid longitude and latitude (DDM).' }
      return validateLonLat(lng, lat)
    }
    case 'UTM': {
      const raw = x.trim()
      if (!raw) return { ok: false, error: 'Enter a UTM coordinate.' }

      // Expect: "33N 500000 4649776" (zone + easting + northing)
      // Also accept: "33 N 500000 4649776" or "33 500000 4649776" (defaults to northern hemisphere).
      const tokens = raw.toUpperCase().split(/[\s,]+/).filter(Boolean)
      if (tokens.length < 3) {
        return { ok: false, error: 'UTM must look like: "33N 500000 4649776".' }
      }

      let zoneToken = tokens[0]
      let restIndex = 1

      // If zone letter is separated, combine: "33 N ..." -> "33N ..."
      if (/^\d{1,2}$/.test(zoneToken) && tokens.length >= 4 && /^[A-Z]$/.test(tokens[1])) {
        zoneToken = `${zoneToken}${tokens[1]}`
        restIndex = 2
      }

      const eastingToken = tokens[restIndex]
      const northingToken = tokens[restIndex + 1]
      if (!eastingToken || !northingToken) {
        return { ok: false, error: 'UTM must look like: "33N 500000 4649776".' }
      }

      const zoneMatch = zoneToken.match(/^(\d{1,2})([A-Z])?$/)
      if (!zoneMatch) return { ok: false, error: 'Invalid UTM zone (expected like 33N).' }

      const zoneNum = Number(zoneMatch[1])
      if (!Number.isFinite(zoneNum) || zoneNum < 1 || zoneNum > 60) {
        return { ok: false, error: 'UTM zone number must be between 1 and 60.' }
      }

      const letter = zoneMatch[2]
      let zoneLetter: string | undefined
      let northern: boolean | undefined

      if (letter) {
        // Keep it simple: allow hemisphere shorthand N/S, otherwise treat as UTM band letter.
        if (letter === 'N') northern = true
        else if (letter === 'S') northern = false
        else zoneLetter = letter
      } else {
        // Default to northern hemisphere if not specified.
        northern = true
      }

      const easting = Number(eastingToken.replace(/[^0-9.+-]/g, ''))
      const northing = Number(northingToken.replace(/[^0-9.+-]/g, ''))
      if (!Number.isFinite(easting) || !Number.isFinite(northing)) {
        return { ok: false, error: 'UTM easting/northing must be numbers.' }
      }

      try {
        const utmModule = await import('utm')
        const utm = (utmModule as any).default ?? utmModule
        const res = (utm as any).toLatLon(easting, northing, zoneNum, zoneLetter, northern, true) as {
          latitude: number
          longitude: number
        }
        return validateLonLat(res.longitude, res.latitude)
      } catch {
        return { ok: false, error: 'Failed to parse UTM. Try a different zone or format.' }
      }
    }
    case 'MGRS': {
      const mgrsText = x.trim().replace(/\s+/g, '')
      if (!mgrsText) return { ok: false, error: 'Enter an MGRS coordinate.' }
      try {
        const mgrsModule = await import('mgrs')
        const mgrs = (mgrsModule as any).default ?? mgrsModule
        const point = (mgrs as any).toPoint(mgrsText) as [number, number]
        return validateLonLat(point[0], point[1])
      } catch {
        return { ok: false, error: 'Failed to parse MGRS. Check the string and try again.' }
      }
    }
    default:
      return { ok: false, error: 'Unsupported coordinate format.' }
  }
}

type CoordinateFormatsInfoDialogProps = {
  open: boolean
  onClose: () => void
}

function DiagramDD() {
  return (
    <svg viewBox="0 0 240 80" className="w-full h-20 text-primary/80">
      <rect x="0.5" y="0.5" width="239" height="79" rx="6" fill="rgba(255,255,255,0.03)" stroke="rgba(255,255,255,0.10)" />
      <g stroke="rgba(255,255,255,0.18)" strokeWidth="1">
        <circle cx="48" cy="40" r="26" fill="rgba(0,0,0,0.25)" />
        <path d="M22 40H74" />
        <path d="M48 14V66" />
        <path d="M27 28C35 34 61 34 69 28" fill="none" />
        <path d="M27 52C35 46 61 46 69 52" fill="none" />
      </g>
      <g stroke="currentColor" strokeWidth="1.5" fill="currentColor">
        <circle cx="58" cy="34" r="2.5" />
        <path d="M58 22V30" />
        <path d="M46 34H54" />
      </g>
      <text x="92" y="34" fill="rgba(255,255,255,0.78)" fontSize="10" fontFamily="ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace">
        lon = -122.4194°
      </text>
      <text x="92" y="52" fill="rgba(255,255,255,0.78)" fontSize="10" fontFamily="ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace">
        lat = 37.7749°
      </text>
    </svg>
  )
}

function DiagramDMS() {
  return (
    <svg viewBox="0 0 240 80" className="w-full h-20 text-primary/80">
      <rect x="0.5" y="0.5" width="239" height="79" rx="6" fill="rgba(255,255,255,0.03)" stroke="rgba(255,255,255,0.10)" />
      <g fill="rgba(255,255,255,0.06)" stroke="rgba(255,255,255,0.12)">
        <rect x="16" y="18" width="60" height="44" rx="6" />
        <rect x="90" y="18" width="60" height="44" rx="6" />
        <rect x="164" y="18" width="60" height="44" rx="6" />
      </g>
      <g fill="rgba(255,255,255,0.78)" fontSize="12" fontFamily="ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace">
        <text x="46" y="46" textAnchor="middle">122°</text>
        <text x="120" y="46" textAnchor="middle">25′</text>
        <text x="194" y="46" textAnchor="middle">10″</text>
      </g>
      <g fill="rgba(255,255,255,0.55)" fontSize="9" fontFamily="ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace">
        <text x="46" y="66" textAnchor="middle">deg</text>
        <text x="120" y="66" textAnchor="middle">min</text>
        <text x="194" y="66" textAnchor="middle">sec</text>
      </g>
      <path d="M16 10H224" stroke="currentColor" strokeWidth="1" opacity="0.55" />
      <text x="120" y="14" textAnchor="middle" fill="rgba(255,255,255,0.55)" fontSize="9" fontFamily="ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace">
        Degrees + minutes + seconds
      </text>
    </svg>
  )
}

function DiagramDDM() {
  return (
    <svg viewBox="0 0 240 80" className="w-full h-20 text-primary/80">
      <rect x="0.5" y="0.5" width="239" height="79" rx="6" fill="rgba(255,255,255,0.03)" stroke="rgba(255,255,255,0.10)" />
      <g fill="rgba(255,255,255,0.06)" stroke="rgba(255,255,255,0.12)">
        <rect x="18" y="18" width="78" height="44" rx="6" />
        <rect x="106" y="18" width="116" height="44" rx="6" />
      </g>
      <g fill="rgba(255,255,255,0.78)" fontSize="12" fontFamily="ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace">
        <text x="57" y="46" textAnchor="middle">122°</text>
        <text x="164" y="46" textAnchor="middle">25.167′</text>
      </g>
      <g fill="rgba(255,255,255,0.55)" fontSize="9" fontFamily="ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace">
        <text x="57" y="66" textAnchor="middle">deg</text>
        <text x="164" y="66" textAnchor="middle">dec-min</text>
      </g>
      <path d="M16 10H224" stroke="currentColor" strokeWidth="1" opacity="0.55" />
      <text x="120" y="14" textAnchor="middle" fill="rgba(255,255,255,0.55)" fontSize="9" fontFamily="ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace">
        Degrees + decimal minutes
      </text>
    </svg>
  )
}

function DiagramUTM() {
  return (
    <svg viewBox="0 0 240 80" className="w-full h-20 text-primary/80">
      <rect x="0.5" y="0.5" width="239" height="79" rx="6" fill="rgba(255,255,255,0.03)" stroke="rgba(255,255,255,0.10)" />
      <g opacity="0.9">
        <rect x="16" y="14" width="208" height="46" fill="rgba(0,0,0,0.25)" stroke="rgba(255,255,255,0.12)" />
        {/* zones */}
        <g>
          {Array.from({ length: 8 }).map((_, i) => (
            <rect
              key={i}
              x={16 + i * 26}
              y={14}
              width={26}
              height={46}
              fill={i === 4 ? 'rgba(255,255,255,0.06)' : 'rgba(255,255,255,0.02)'}
              stroke="rgba(255,255,255,0.06)"
            />
          ))}
        </g>
        {/* grid */}
        <g stroke="rgba(255,255,255,0.08)">
          {Array.from({ length: 4 }).map((_, i) => (
            <path key={`h${i}`} d={`M16 ${24 + i * 10}H224`} />
          ))}
        </g>
        <rect x="16 + 4 * 26" y="14" width="26" height="46" fill="rgba(0,0,0,0)" stroke="currentColor" strokeWidth="1.5" />
      </g>
      <text x="120" y="12" textAnchor="middle" fill="rgba(255,255,255,0.65)" fontSize="9" fontFamily="ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace">
        UTM zones (6° wide) + meters (E/N)
      </text>
      <text x="16 + 4 * 26 + 13" y="72" textAnchor="middle" fill="rgba(255,255,255,0.78)" fontSize="10" fontFamily="ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace">
        Zone 33
      </text>
    </svg>
  )
}

function DiagramMGRS() {
  return (
    <svg viewBox="0 0 240 80" className="w-full h-20 text-primary/80">
      <rect x="0.5" y="0.5" width="239" height="79" rx="6" fill="rgba(255,255,255,0.03)" stroke="rgba(255,255,255,0.10)" />
      <rect x="16" y="14" width="116" height="52" fill="rgba(0,0,0,0.25)" stroke="rgba(255,255,255,0.12)" />
      <g stroke="rgba(255,255,255,0.10)">
        {Array.from({ length: 5 }).map((_, i) => (
          <path key={`h${i}`} d={`M16 ${14 + i * 10.4}H132`} />
        ))}
        {Array.from({ length: 6 }).map((_, i) => (
          <path key={`v${i}`} d={`M${16 + i * 23.2} 14V66`} />
        ))}
      </g>
      <rect x="62" y="35" width="23.2" height="10.4" fill="rgba(0,0,0,0)" stroke="currentColor" strokeWidth="1.5" />
      <g fill="rgba(255,255,255,0.78)" fontSize="10" fontFamily="ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace">
        <text x="154" y="34">33TWN</text>
        <text x="154" y="52">12345 67890</text>
      </g>
      <text x="154" y="68" fill="rgba(255,255,255,0.55)" fontSize="9" fontFamily="ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace">
        zone + 100km + digits
      </text>
    </svg>
  )
}

function CoordinateFormatsInfoDialog({ open, onClose }: CoordinateFormatsInfoDialogProps) {
  const [mounted, setMounted] = useState(false)
  useEffect(() => setMounted(true), [])

  if (!mounted || !open) return null

  return createPortal(
    <>
      <div className="fixed inset-0 bg-black/80 backdrop-blur-md z-[130]" onClick={onClose} />

      <div className="fixed inset-0 z-[131] flex items-center justify-center p-4 pointer-events-none font-mono">
        <div
          role="dialog"
          aria-modal="true"
          className="relative w-[920px] max-w-[96vw] max-h-[86vh] bg-[#0a0a0a]/95 border border-white/10 rounded-sm shadow-[0_0_50px_-10px_rgba(0,0,0,0.85)] flex flex-col pointer-events-auto overflow-hidden animate-in fade-in zoom-in-95 duration-200"
          onClick={(e) => e.stopPropagation()}
        >
          <header className="px-5 py-4 border-b border-white/10 bg-white/[0.02] flex items-center justify-between gap-3 shrink-0">
            <div className="flex items-center gap-3 min-w-0">
              <div className="p-2 bg-primary/10 border border-primary/20 rounded-sm">
                <Info className="w-5 h-5 text-primary" />
              </div>
              <div className="min-w-0">
                <div className="text-[10px] text-white/40 uppercase tracking-widest font-bold">Coordinate formats</div>
                <div className="text-sm text-white/90 truncate font-medium">How DD / DMS / DDM / UTM / MGRS represent locations</div>
              </div>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="p-2 hover:bg-white/5 border border-transparent hover:border-white/10 rounded-sm text-white/50 hover:text-white transition-all"
              title="Close"
            >
              <X className="w-5 h-5" />
            </button>
          </header>

          <div className="p-5 overflow-y-auto custom-scrollbar">
            <div className="mb-4 text-xs text-white/60 leading-relaxed">
              All formats below ultimately resolve to WGS84 longitude/latitude for the map. Summaries are based on Wikipedia (kept brief).
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              <div className="border border-white/10 rounded-sm bg-black/20 overflow-hidden hover:border-white/20 transition-colors group">
                <div className="px-4 py-3 border-b border-white/10 bg-white/[0.02] group-hover:bg-white/[0.04] transition-colors">
                  <div className="text-[10px] text-primary/70 uppercase tracking-widest font-bold mb-0.5">DD (Decimal Degrees)</div>
                  <div className="text-xs text-white/70">Latitude/longitude expressed as decimal degrees.</div>
                </div>
                <div className="p-4 space-y-3">
                  <DiagramDD />
                  <ul className="text-[11px] text-white/70 space-y-1.5 list-disc pl-4 marker:text-white/30">
                    <li>Use signs (±) or N/S/E/W for hemisphere.</li>
                    <li>Most common format for web maps.</li>
                  </ul>
                  <div className="space-y-1.5 pt-1">
                    <div className="text-[11px] text-white/50">
                      Example: <span className="text-white/90 font-medium">lon -122.4194, lat 37.7749</span>
                    </div>
                    <div className="text-[11px] text-primary/60 bg-primary/5 px-2 py-1.5 rounded border border-primary/10">
                      <span className="uppercase text-[9px] tracking-wider font-bold mr-2 opacity-70">INPUT</span>
                      <span className="text-primary/90">Lon in first field, Lat in second.</span>
                    </div>
                  </div>
                </div>
              </div>

              <div className="border border-white/10 rounded-sm bg-black/20 overflow-hidden hover:border-white/20 transition-colors group">
                <div className="px-4 py-3 border-b border-white/10 bg-white/[0.02] group-hover:bg-white/[0.04] transition-colors">
                  <div className="text-[10px] text-primary/70 uppercase tracking-widest font-bold mb-0.5">DMS (Degrees Minutes Seconds)</div>
                  <div className="text-xs text-white/70">Split into degrees (°), minutes (′), seconds (″).</div>
                </div>
                <div className="p-4 space-y-3">
                  <DiagramDMS />
                  <ul className="text-[11px] text-white/70 space-y-1.5 list-disc pl-4 marker:text-white/30">
                    <li>1° = 60′, 1′ = 60″.</li>
                    <li>Often used in surveying and navigation.</li>
                  </ul>
                  <div className="space-y-1.5 pt-1">
                    <div className="text-[11px] text-white/50">
                      Example: <span className="text-white/90 font-medium">122°25′10″W, 37°46′30″N</span>
                    </div>
                    <div className="text-[11px] text-primary/60 bg-primary/5 px-2 py-1.5 rounded border border-primary/10">
                      <span className="uppercase text-[9px] tracking-wider font-bold mr-2 opacity-70">INPUT</span>
                      <span className="text-primary/90">Lon in first field, Lat in second.</span>
                    </div>
                  </div>
                </div>
              </div>

              <div className="border border-white/10 rounded-sm bg-black/20 overflow-hidden hover:border-white/20 transition-colors group">
                <div className="px-4 py-3 border-b border-white/10 bg-white/[0.02] group-hover:bg-white/[0.04] transition-colors">
                  <div className="text-[10px] text-primary/70 uppercase tracking-widest font-bold mb-0.5">DDM (Degrees Decimal Minutes)</div>
                  <div className="text-xs text-white/70">Degrees plus minutes with a decimal fraction.</div>
                </div>
                <div className="p-4 space-y-3">
                  <DiagramDDM />
                  <ul className="text-[11px] text-white/70 space-y-1.5 list-disc pl-4 marker:text-white/30">
                    <li>1° = 60′ (minutes are decimal).</li>
                    <li>Common on some GPS devices and charts.</li>
                  </ul>
                  <div className="space-y-1.5 pt-1">
                    <div className="text-[11px] text-white/50">
                      Example: <span className="text-white/90 font-medium">122°25.167′W, 37°46.500′N</span>
                    </div>
                    <div className="text-[11px] text-primary/60 bg-primary/5 px-2 py-1.5 rounded border border-primary/10">
                      <span className="uppercase text-[9px] tracking-wider font-bold mr-2 opacity-70">INPUT</span>
                      <span className="text-primary/90">Lon in first field, Lat in second.</span>
                    </div>
                  </div>
                </div>
              </div>

              <div className="border border-white/10 rounded-sm bg-black/20 overflow-hidden hover:border-white/20 transition-colors group">
                <div className="px-4 py-3 border-b border-white/10 bg-white/[0.02] group-hover:bg-white/[0.04] transition-colors">
                  <div className="text-[10px] text-primary/70 uppercase tracking-widest font-bold mb-0.5">UTM (Universal Transverse Mercator)</div>
                  <div className="text-xs text-white/70">Projected coordinates in meters within a zone.</div>
                </div>
                <div className="p-4 space-y-3">
                  <DiagramUTM />
                  <ul className="text-[11px] text-white/70 space-y-1.5 list-disc pl-4 marker:text-white/30">
                    <li>Earth is split into 60 zones; each coordinate includes a zone.</li>
                    <li>Easting/Northing are meters in that zone.</li>
                  </ul>
                  <div className="space-y-1.5 pt-1">
                    <div className="text-[11px] text-white/50">
                      Example: <span className="text-white/90 font-medium">33N 500000 4649776</span>
                    </div>
                    <div className="text-[11px] text-primary/60 bg-primary/5 px-2 py-1.5 rounded border border-primary/10">
                      <span className="uppercase text-[9px] tracking-wider font-bold mr-2 opacity-70">INPUT</span>
                      <span className="text-primary/90">One field: zone + easting + northing.</span>
                    </div>
                  </div>
                </div>
              </div>

              <div className="border border-white/10 rounded-sm bg-black/20 overflow-hidden lg:col-span-2 hover:border-white/20 transition-colors group">
                <div className="px-4 py-3 border-b border-white/10 bg-white/[0.02] group-hover:bg-white/[0.04] transition-colors">
                  <div className="text-[10px] text-primary/70 uppercase tracking-widest font-bold mb-0.5">MGRS (Military Grid Reference System)</div>
                  <div className="text-xs text-white/70">Compact grid reference based on UTM.</div>
                </div>
                <div className="p-4 space-y-3">
                  <DiagramMGRS />
                  <ul className="text-[11px] text-white/70 space-y-1.5 list-disc pl-4 marker:text-white/30">
                    <li>Uses Grid Zone Designator + 100km square + numeric digits.</li>
                    <li>More digits = higher precision.</li>
                  </ul>
                  <div className="space-y-1.5 pt-1">
                    <div className="text-[11px] text-white/50">
                      Example: <span className="text-white/90 font-medium">33TWN1234567890</span> <span className="text-white/40">(spaces allowed)</span>
                    </div>
                    <div className="text-[11px] text-primary/60 bg-primary/5 px-2 py-1.5 rounded border border-primary/10">
                      <span className="uppercase text-[9px] tracking-wider font-bold mr-2 opacity-70">INPUT</span>
                      <span className="text-primary/90">One field: full MGRS string.</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </>,
    document.body
  )
}

export function GoToCoordinatesBar({ open, seed, onClose, onGoTo }: Props) {
  const [format, setFormat] = useState<GoToCoordinateFormat>('DD')
  const [x, setX] = useState('')
  const [y, setY] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [infoOpen, setInfoOpen] = useState(false)

  useEffect(() => {
    if (!open) return
    setError(null)
    setBusy(false)
    setInfoOpen(false)
    if (seed) {
      setFormat('DD')
      setX(seed.lng.toFixed(6))
      setY(seed.lat.toFixed(6))
    } else {
      setFormat('DD')
      setX('')
      setY('')
    }
  }, [open, seed])

  useEffect(() => {
    if (!open) return
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      if (infoOpen) setInfoOpen(false)
      else onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [infoOpen, open, onClose])

  const ui = useMemo(() => {
    switch (format) {
      case 'UTM':
        return {
          singleField: true,
          xLabel: 'UTM',
          yLabel: '',
          xPlaceholder: 'e.g. 33N 500000 4649776',
          yPlaceholder: ''
        }
      case 'MGRS':
        return {
          singleField: true,
          xLabel: 'MGRS',
          yLabel: '',
          xPlaceholder: 'e.g. 33TWN 12345 67890',
          yPlaceholder: ''
        }
      case 'DMS':
        return {
          singleField: false,
          xLabel: 'LONGITUDE',
          yLabel: 'LATITUDE',
          xPlaceholder: 'e.g. 122°25\'10"W',
          yPlaceholder: 'e.g. 37°46\'30"N'
        }
      case 'DDM':
        return {
          singleField: false,
          xLabel: 'LONGITUDE',
          yLabel: 'LATITUDE',
          xPlaceholder: 'e.g. 122 25.167 W',
          yPlaceholder: 'e.g. 37 46.500 N'
        }
      default:
        return {
          singleField: false,
          xLabel: 'LONGITUDE',
          yLabel: 'LATITUDE',
          xPlaceholder: 'e.g. -122.4194',
          yPlaceholder: 'e.g. 37.7749'
        }
    }
  }, [format])

  if (!open) return null

  const handleSubmit = async () => {
    setError(null)
    setBusy(true)
    try {
      const res = await parseInputs(format, x, y)
      if (!res.ok) {
        setError(res.error)
        return
      }
      onGoTo(res.lng, res.lat)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <div className="pointer-events-none absolute bottom-16 left-1/2 -translate-x-1/2 z-40 w-[min(920px,calc(100%-2rem))]">
        <div
          className="pointer-events-auto bg-black/85 backdrop-blur-md border border-white/10 rounded-sm shadow-[0_0_24px_-8px_rgba(0,0,0,0.85)] overflow-hidden"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex items-center justify-between gap-3 px-3 py-2 border-b border-white/10 bg-white/[0.02]">
            <div className="min-w-0">
              <div className="text-[10px] font-mono text-white/40 uppercase tracking-widest">Go to Coordinates</div>
              <div className="text-[10px] font-mono text-white/60 truncate">Enter coordinates, choose a format, then hit GOTO.</div>
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setInfoOpen(true)}
                className="p-1 rounded hover:bg-white/10 text-white/60 hover:text-white transition-colors"
                title="Format help"
              >
                <Info className="w-4 h-4" />
              </button>
              <button
                type="button"
                onClick={onClose}
                className="p-1 rounded hover:bg-white/10 text-white/60 hover:text-white transition-colors"
                title="Close"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          </div>

          <form
            className="px-3 py-3 flex flex-col gap-2"
            onSubmit={(e) => {
              e.preventDefault()
              handleSubmit().catch(() => setError('Failed to process coordinates.'))
            }}
          >
            <div className="flex flex-col lg:flex-row lg:items-end gap-2">
              <div className="flex flex-col gap-1">
                <span className="text-[9px] font-mono text-white/40 uppercase tracking-widest">FORMAT</span>
                <select
                  value={format}
                  onChange={(e) => setFormat(e.target.value as GoToCoordinateFormat)}
                  className="h-9 bg-black/60 border border-white/10 rounded-sm px-2 text-xs font-mono text-white/90 focus:outline-none focus:ring-2 focus:ring-primary/30"
                >
                  <option value="DD">DD</option>
                  <option value="DMS">DMS</option>
                  <option value="DDM">DDM</option>
                  <option value="UTM">UTM</option>
                  <option value="MGRS">MGRS</option>
                </select>
              </div>

              <div className={cn('flex-1 grid gap-2', ui.singleField ? 'grid-cols-1' : 'grid-cols-1 md:grid-cols-2')}>
                <div className="flex flex-col gap-1">
                  <span className="text-[9px] font-mono text-white/40 uppercase tracking-widest">{ui.xLabel}</span>
                  <input
                    value={x}
                    onChange={(e) => setX(e.target.value)}
                    placeholder={ui.xPlaceholder}
                    className="h-9 bg-black/60 border border-white/10 rounded-sm px-2 text-xs font-mono text-white/90 placeholder:text-white/30 focus:outline-none focus:ring-2 focus:ring-primary/30"
                  />
                </div>

                {!ui.singleField && (
                  <div className="flex flex-col gap-1">
                    <span className="text-[9px] font-mono text-white/40 uppercase tracking-widest">{ui.yLabel}</span>
                    <input
                      value={y}
                      onChange={(e) => setY(e.target.value)}
                      placeholder={ui.yPlaceholder}
                      className="h-9 bg-black/60 border border-white/10 rounded-sm px-2 text-xs font-mono text-white/90 placeholder:text-white/30 focus:outline-none focus:ring-2 focus:ring-primary/30"
                    />
                  </div>
                )}
              </div>

              <button
                type="submit"
                disabled={busy}
                className={cn(
                  'h-9 px-4 rounded-sm border text-xs font-mono uppercase tracking-widest transition-all',
                  busy
                    ? 'bg-white/5 border-white/10 text-white/40 cursor-not-allowed'
                    : 'bg-primary/20 border-primary/40 text-primary hover:bg-primary/30 hover:border-primary/60'
                )}
              >
                {busy ? '...' : 'GOTO'}
              </button>
            </div>

            {error && (
              <div className="text-[10px] font-mono text-red-300 bg-red-500/10 border border-red-500/20 rounded-sm px-2 py-1">
                {error}
              </div>
            )}
          </form>
        </div>
      </div>

      <CoordinateFormatsInfoDialog open={infoOpen} onClose={() => setInfoOpen(false)} />
    </>
  )
}


