'use client'

import { useMapView } from '@/lib/context/MapViewContext'
import { useProject } from '@/lib/context/ProjectContext'
import { cn } from '@/lib/utils'
import { ChevronDown, Layers, Mountain, Pentagon, Ruler, SlidersHorizontal } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

interface HeaderProps { devMode: boolean; isBackendOnline: boolean; onDevModeChange:(v:boolean)=>void; activeView:string; className?:string }
type Menu = 'datasets' | 'tools'

export function Header({isBackendOnline, className}: HeaderProps) {
  const {currentProject} = useProject()
  const {gis} = useMapView()
  const [menu, setMenu] = useState<Menu|null>(null)
  const container = useRef<HTMLDivElement>(null)
  const triggers = useRef<Partial<Record<Menu,HTMLButtonElement|null>>>({})
  const dropdown = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const close=(event:PointerEvent)=>{if(!container.current?.contains(event.target as Node))setMenu(null)}
    document.addEventListener('pointerdown',close)
    return ()=>document.removeEventListener('pointerdown',close)
  },[])
  useEffect(()=>{setMenu(null)},[currentProject])
  useEffect(()=>{if(menu)dropdown.current?.querySelector<HTMLButtonElement>('button')?.focus()},[menu])

  const action=(fn:()=>void)=>{if(menu)triggers.current[menu]?.focus();setMenu(null);fn()}
  const entries = menu === 'datasets' ? [
    // Standalone fetch entry retained in ./archived/fetchDatasetsEntry.ts for restoration.
    {label:'Digital Twin',icon:Layers,run:gis.openDatasetDigitalTwin},
    {label:'Manage datasets',icon:Layers,run:gis.openDatasetIndex},
  ] : [
    {label:'Measure distance',icon:Ruler,run:()=>gis.openMeasureTool('distance')},
    {label:'Measure area',icon:Pentagon,run:()=>gis.openMeasureTool('area')},
    {label:'Elevation profile',icon:Mountain,run:()=>gis.openMeasureTool('elevation')},
  ]

  return <header className={cn('relative z-50 flex h-12 shrink-0 items-center justify-between border-b border-white/[0.06] bg-[#0a0a0a]/75 px-4 backdrop-blur-xl',className)}>
    <div ref={container} className="flex min-w-0 items-center gap-1" onBlur={event=>{if(!event.currentTarget.contains(event.relatedTarget as Node|null))setMenu(null)}}>
      {(['datasets','tools'] as const).map(name=>{
        const Icon=name==='datasets'?Layers:SlidersHorizontal
        const expanded=menu===name
        return <div key={name} className="relative">
          <button ref={node=>{triggers.current[name]=node}} type="button" aria-haspopup="menu" aria-expanded={expanded} aria-controls={expanded?`map-${name}-menu`:undefined} disabled={!currentProject}
            onClick={()=>setMenu(expanded?null:name)} onKeyDown={event=>{if(event.key==='ArrowDown'){event.preventDefault();setMenu(name)}}}
            className={cn('group flex h-8 items-center gap-2 rounded-md px-3 text-[12px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-white/30 disabled:cursor-default disabled:opacity-30',expanded?'bg-white/[0.07] text-white':'text-white/60 hover:bg-white/[0.04] hover:text-white/90')}>
            <Icon className="h-3.5 w-3.5 text-white/40" strokeWidth={1.5}/><span>{name==='datasets'?'Datasets':'Tools'}</span><ChevronDown className={cn('ml-1 h-3 w-3 text-white/30 transition-transform duration-150 motion-reduce:transition-none',expanded&&'rotate-180')}/>
          </button>
          {expanded && <div ref={dropdown} id={`map-${name}-menu`} role="menu" aria-label={name==='datasets'?'Dataset actions':'Map tools'}
            className="absolute left-0 top-full mt-2 w-52 rounded-lg border border-white/[0.09] bg-[#111314]/95 p-1.5 shadow-[0_12px_36px_rgba(0,0,0,0.35)] backdrop-blur-xl"
            onKeyDown={event=>{
              const items=Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="menuitem"]'))
              const index=items.indexOf(document.activeElement as HTMLButtonElement)
              if(event.key==='Escape'){event.preventDefault();event.stopPropagation();setMenu(null);triggers.current[name]?.focus()}
              else if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)){
                event.preventDefault()
                const next=event.key==='Home'?0:event.key==='End'?items.length-1:(index+(event.key==='ArrowDown'?1:-1)+items.length)%items.length
                items[next]?.focus()
              }
            }}>
            {entries.map(entry=><button key={entry.label} type="button" role="menuitem" tabIndex={-1} onClick={()=>action(entry.run)} className="flex w-full items-center gap-3 rounded-md px-3 py-2.5 text-left text-xs text-white/65 transition-colors hover:bg-white/[0.06] hover:text-white focus:bg-white/[0.06] focus:text-white focus:outline-none"><entry.icon className="h-3.5 w-3.5 text-white/35" strokeWidth={1.5}/>{entry.label}</button>)}
          </div>}
        </div>
      })}
      {!currentProject&&<span className="ml-4 hidden truncate text-[11px] text-white/30 sm:inline">Select a project to begin</span>}
    </div>
    <div className="flex items-center gap-3">
      {!isBackendOnline && <span role="status" className="ml-3 flex shrink-0 items-center gap-2 text-[11px] text-red-400/80"><span className="h-1.5 w-1.5 rounded-full bg-red-400"/>Backend offline</span>}
    </div>
  </header>
}
