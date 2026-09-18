import { cn } from '@/lib/utils'
import type { CesiumMap } from '@/lib/map/CesiumMap'
import React,{ useEffect,useState } from 'react'

interface CompassProps {
  map: CesiumMap | null
  className?: string
}

export function Compass({ map, className }: CompassProps) {
  const [bearing, setBearing] = useState(0)
  const [pitch, setPitch] = useState(0)

  useEffect(() => {
    if (!map) return

    const updateState = () => {
      setBearing(map.getBearing())
      setPitch(map.getPitch())
    }

    map.on('rotate', updateState)
    map.on('pitch', updateState)
    // Initial state
    updateState()

    return () => {
      map.off('rotate', updateState)
      map.off('pitch', updateState)
    }
  }, [map])

  const handleResetView = (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (!map) return

    // Reset both bearing and pitch (top-down North)
    map.easeTo({
      bearing: 0,
      pitch: 0,
      duration: 1000,
      padding: { top: 0, bottom: 0, left: 0, right: 0 }
    })
  }
  
  const handleResetNorth = (e: React.MouseEvent) => {
      e.preventDefault()
      e.stopPropagation()
      if (!map) return
      
      // Reset only bearing
      map.easeTo({
          bearing: 0,
          duration: 1000
      })
  }

  return (
    <div 
      className={cn(
        "absolute bottom-8 left-4 z-[50] flex flex-col gap-2 pointer-events-auto",
        className
      )}
    >
        <div 
            className="group relative w-12 h-12 cursor-pointer transition-transform duration-200 hover:scale-110"
            onClick={handleResetNorth}
            onDoubleClick={handleResetView}
            title="Click: Reset North | Double-click: Top-down view"
        >
            {/* Background Disc */}
            <div className="absolute inset-0 bg-black/40 backdrop-blur-md rounded-full border border-white/20 shadow-lg" />

            {/* Rotating Compass Rose */}
            <div 
                className="absolute inset-0 flex items-center justify-center transition-transform duration-100 ease-out will-change-transform"
                style={{ transform: `rotate(${-bearing}deg)` }}
            >
                <svg viewBox="0 0 100 100" className="w-8 h-8 drop-shadow-sm filter">
                    {/* South (White) */}
                    <path d="M50 90 L62 50 L38 50 Z" fill="white" className="opacity-90" />
                    {/* North (Red) */}
                    <path d="M50 10 L62 50 L38 50 Z" fill="#ef4444" />
                </svg>
                
                {/* N Label - Rotates with the needle so it's always at the red tip */}
                <div className="absolute top-[-4px] left-1/2 -translate-x-1/2 font-bold text-white text-[10px] select-none tracking-wider drop-shadow-md">
                    N
                </div>
            </div>

            {/* Tilt/Pitch visual cue (optional inner ring that flattens) */}
            <div 
                className="absolute inset-3 rounded-full border-2 border-white/30 pointer-events-none"
                style={{ 
                    transform: `rotateX(${pitch}deg)`,
                    opacity: pitch > 0 ? 0.8 : 0.2 
                }}
            />
        </div>
    </div>
  )
}



