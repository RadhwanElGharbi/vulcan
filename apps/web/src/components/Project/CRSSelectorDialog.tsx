'use client'

import { cn } from '@/lib/utils'
import { CheckCircle2,Globe,Search,X } from 'lucide-react'
import { useEffect,useMemo,useState } from 'react'
import { createPortal } from 'react-dom'

// ----------------------------------------------------------------------------
// CRS Data Definitions
// ----------------------------------------------------------------------------

export interface CRSEntry {
  epsg: number
  name: string
  category: string
  type: 'Geographic' | 'Projected'
  area?: string
}

const BASE_CRS_DB: CRSEntry[] = [
  // Geographic
  { epsg: 4326, name: 'WGS 84', category: 'Geographic', type: 'Geographic', area: 'World' },
  { epsg: 4269, name: 'NAD83', category: 'Geographic', type: 'Geographic', area: 'North America' },
  { epsg: 4267, name: 'NAD27', category: 'Geographic', type: 'Geographic', area: 'North America' },
  { epsg: 4258, name: 'ETRS89', category: 'Geographic', type: 'Geographic', area: 'Europe' },
  { epsg: 4230, name: 'ED50', category: 'Geographic', type: 'Geographic', area: 'Europe' },
  { epsg: 4277, name: 'OSGB 1936', category: 'Geographic', type: 'Geographic', area: 'UK' },
  { epsg: 4283, name: 'GDA94', category: 'Geographic', type: 'Geographic', area: 'Australia' },
  { epsg: 4284, name: 'Pulkovo 1942', category: 'Geographic', type: 'Geographic', area: 'Russia' },
  
  // Web / Popular Projected
  { epsg: 3857, name: 'WGS 84 / Pseudo-Mercator', category: 'Projected', type: 'Projected', area: 'World (Web)' },
  { epsg: 3395, name: 'WGS 84 / World Mercator', category: 'Projected', type: 'Projected', area: 'World' },
]

// Generate UTM Zones (1-60 N/S)
const UTM_CRS_DB: CRSEntry[] = []
for (let i = 1; i <= 60; i++) {
  UTM_CRS_DB.push({
    epsg: 32600 + i,
    name: `WGS 84 / UTM zone ${i}N`,
    category: 'UTM',
    type: 'Projected',
    area: `Northern Hemisphere - ${i * 6 - 180}E to ${i * 6 - 174}E`
  })
  UTM_CRS_DB.push({
    epsg: 32700 + i,
    name: `WGS 84 / UTM zone ${i}S`,
    category: 'UTM',
    type: 'Projected',
    area: `Southern Hemisphere - ${i * 6 - 180}E to ${i * 6 - 174}E`
  })
}

const FULL_CRS_DB = [...BASE_CRS_DB, ...UTM_CRS_DB]

// ----------------------------------------------------------------------------
// Component
// ----------------------------------------------------------------------------

interface CRSSelectorDialogProps {
  open: boolean
  onClose: () => void
  onSelect: (crs: CRSEntry) => void
  currentEpsg?: number
}

export function CRSSelectorDialog({ open, onClose, onSelect, currentEpsg }: CRSSelectorDialogProps) {
  const [searchTerm, setSearchTerm] = useState('')
  const [selectedCategory, setSelectedCategory] = useState<string>('All')
  const [selectedEpsg, setSelectedEpsg] = useState<number | null>(currentEpsg || null)
  const [mounted, setMounted] = useState(false)
  const [isClosing, setIsClosing] = useState(false)

  useEffect(() => {
    setMounted(true)
  }, [])

  useEffect(() => {
    if (open) setIsClosing(false)
  }, [open])

  const handleClose = () => {
    setIsClosing(true)
    setTimeout(onClose, 150)
  }

  useEffect(() => {
    if (open && currentEpsg) {
      setSelectedEpsg(currentEpsg)
    }
  }, [open, currentEpsg])

  const filteredList = useMemo(() => {
    let list = FULL_CRS_DB

    // Category Filter
    if (selectedCategory !== 'All') {
      if (selectedCategory === 'Geographic') {
        list = list.filter(c => c.category === 'Geographic')
      } else if (selectedCategory === 'Projected') {
        list = list.filter(c => c.category === 'Projected')
      } else if (selectedCategory === 'UTM') {
        list = list.filter(c => c.category === 'UTM')
      }
    }

    // Search Filter
    if (searchTerm.trim()) {
      const lower = searchTerm.toLowerCase()
      list = list.filter(c => 
        c.name.toLowerCase().includes(lower) || 
        String(c.epsg).includes(lower) ||
        (c.area && c.area.toLowerCase().includes(lower))
      )
    }

    return list
  }, [selectedCategory, searchTerm])

  const selectedEntry = useMemo(() => 
    FULL_CRS_DB.find(c => c.epsg === selectedEpsg), 
    [selectedEpsg]
  )

  if (!open || !mounted) return null

  const categories = ['All', 'Geographic', 'Projected', 'UTM']

  return createPortal(
    <>
      {/* Backdrop */}
      <div 
        className={cn(
          "fixed inset-0 bg-black/80 backdrop-blur-md z-[150]",
          isClosing ? "animate-fade-out" : "animate-fade-in"
        )}
        onClick={handleClose}
      >
        <div className="absolute inset-0 bg-[linear-gradient(to_right,#ffffff05_1px,transparent_1px),linear-gradient(to_bottom,#ffffff05_1px,transparent_1px)] bg-[size:40px_40px] pointer-events-none" />
      </div>

      {/* Dialog */}
      <div className="fixed inset-0 z-[151] flex items-center justify-center p-4 pointer-events-none">
        <div className={cn(
          "relative w-[900px] max-w-[95vw] h-[700px] max-h-[90vh] bg-[#0a0a0a]/95 border border-white/10 rounded-sm shadow-[0_0_50px_-10px_rgba(0,0,0,0.8)] flex flex-col pointer-events-auto overflow-hidden",
          isClosing ? "animate-fade-out" : "animate-fade-in"
        )}>
          
          {/* Header */}
          <header className="px-6 py-4 border-b border-white/10 flex items-center justify-between bg-black/20 shrink-0">
            <div className="flex flex-col gap-1">
              <div className="flex items-center gap-2 text-[10px] text-white/40 uppercase tracking-[0.2em] font-mono">
                <Globe className="w-3 h-3" />
                <span>Reference System Database</span>
              </div>
              <h2 className="text-lg font-bold text-white uppercase tracking-wide font-mono">
                Select Project CRS
              </h2>
            </div>
            <button 
              onClick={handleClose}
              className="p-2 hover:bg-white/5 border border-transparent hover:border-white/10 rounded-sm text-white/50 hover:text-white transition-all"
            >
              <X className="w-5 h-5" />
            </button>
          </header>

          {/* Toolbar */}
          <div className="p-4 border-b border-white/10 bg-white/[0.02] flex gap-4 shrink-0">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-white/30" />
              <input 
                type="text"
                placeholder="Search by Name, EPSG Code, or Region..."
                className="w-full bg-black border border-white/10 rounded-sm pl-9 pr-4 py-2 text-xs font-mono text-white placeholder:text-white/20 focus:outline-none focus:border-primary/50 transition-all"
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                autoFocus
              />
            </div>
          </div>

          {/* Main Content Area */}
          <div className="flex flex-1 min-h-0">
            
            {/* Sidebar Categories */}
            <div className="w-48 border-r border-white/10 bg-black/20 p-2 flex flex-col gap-1 overflow-y-auto shrink-0">
              <div className="px-3 py-2 text-[10px] font-mono uppercase tracking-wider text-white/30">Categories</div>
              {categories.map(cat => (
                <button
                  key={cat}
                  onClick={() => setSelectedCategory(cat)}
                  className={cn(
                    "px-3 py-2 text-left text-xs font-mono rounded-sm transition-all",
                    selectedCategory === cat 
                      ? "bg-primary/10 text-primary border border-primary/20" 
                      : "text-white/60 hover:bg-white/5 hover:text-white border border-transparent"
                  )}
                >
                  {cat}
                </button>
              ))}
            </div>

            {/* List View */}
            <div className="flex-1 flex flex-col min-w-0 bg-[linear-gradient(to_right,#ffffff02_1px,transparent_1px),linear-gradient(to_bottom,#ffffff02_1px,transparent_1px)] bg-[size:20px_20px]">
              
              {/* Table Header */}
              <div className="flex items-center px-4 py-2 border-b border-white/10 text-[10px] font-mono uppercase tracking-wider text-white/30 bg-black/40">
                <div className="w-20">EPSG</div>
                <div className="flex-1">Name</div>
                <div className="w-24 text-right">Type</div>
              </div>

              {/* Scrollable List */}
              <div className="flex-1 overflow-y-auto">
                {filteredList.length === 0 ? (
                  <div className="flex flex-col items-center justify-center h-full text-white/30 gap-2">
                    <Search className="w-8 h-8 opacity-20" />
                    <span className="text-xs font-mono">NO MATCHING CRS FOUND</span>
                  </div>
                ) : (
                  <div className="divide-y divide-white/5">
                    {filteredList.map(crs => {
                      const isSelected = selectedEpsg === crs.epsg
                      return (
                        <div 
                          key={crs.epsg}
                          onClick={() => setSelectedEpsg(crs.epsg)}
                          className={cn(
                            "flex items-center px-4 py-3 cursor-pointer transition-all hover:bg-white/[0.02]",
                            isSelected && "bg-primary/[0.05] border-l-2 border-primary pl-[14px]"
                          )}
                        >
                          <div className={cn("w-20 font-mono text-xs", isSelected ? "text-primary" : "text-white/50")}>
                            {crs.epsg}
                          </div>
                          <div className="flex-1 min-w-0 pr-4">
                            <div className={cn("text-xs font-bold truncate", isSelected ? "text-white" : "text-white/80")}>
                              {crs.name}
                            </div>
                            {crs.area && (
                              <div className="text-[10px] text-white/30 truncate mt-0.5 font-mono">
                                {crs.area}
                              </div>
                            )}
                          </div>
                          <div className="w-24 text-right text-[10px] font-mono text-white/40 uppercase">
                            {crs.type}
                          </div>
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>
            </div>

            {/* Inspector Panel (Right) */}
            <div className="w-64 border-l border-white/10 bg-black/40 p-4 flex flex-col gap-6 shrink-0">
              <div className="text-[10px] font-mono uppercase tracking-wider text-white/30">Selection Details</div>
              
              {selectedEntry ? (
                <>
                  <div className="space-y-4">
                    <div className="p-3 bg-primary/5 border border-primary/20 rounded-sm">
                      <div className="text-[10px] text-primary/50 font-mono mb-1">EPSG CODE</div>
                      <div className="text-2xl font-bold text-primary font-mono">{selectedEntry.epsg}</div>
                    </div>

                    <div>
                      <div className="text-[10px] text-white/30 font-mono mb-1">NAME</div>
                      <div className="text-sm text-white font-bold leading-tight">{selectedEntry.name}</div>
                    </div>

                    <div>
                      <div className="text-[10px] text-white/30 font-mono mb-1">CATEGORY</div>
                      <div className="text-xs text-white/70 font-mono">{selectedEntry.category}</div>
                    </div>

                    <div>
                      <div className="text-[10px] text-white/30 font-mono mb-1">AREA OF USE</div>
                      <div className="text-xs text-white/60 leading-relaxed">
                        {selectedEntry.area || 'Global'}
                      </div>
                    </div>
                  </div>

                  <div className="mt-auto">
                    <button
                      onClick={() => onSelect(selectedEntry)}
                      className="w-full py-3 bg-primary text-black font-bold font-mono uppercase tracking-wider text-xs rounded-sm hover:bg-primary/90 transition-all shadow-[0_0_20px_-5px_rgba(var(--primary),0.4)] flex items-center justify-center gap-2"
                    >
                      <CheckCircle2 className="w-4 h-4" />
                      Confirm Selection
                    </button>
                  </div>
                </>
              ) : (
                <div className="flex-1 flex items-center justify-center text-center p-4 border border-dashed border-white/10 rounded-sm">
                  <span className="text-[10px] font-mono text-white/20 uppercase">
                    Select a Reference System from the list to view details
                  </span>
                </div>
              )}
            </div>

          </div>
        </div>
      </div>
    </>,
    document.body
  )
}



