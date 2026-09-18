'use client'

import { cn } from '@/lib/utils'
import { Brain,Database,Globe } from 'lucide-react'
import { useEffect,useRef,useState } from 'react'
import { createPortal } from 'react-dom'

interface ZeusLoadingDialogProps {
  open: boolean
  onComplete: () => void
}

const LOADING_MESSAGES = [
  "ANALYZING TERRAIN TOPOLOGY...",
  "MAPPING DATASET AVAILABILITY MATRIX...",
  "PREPARING GEOPROCESSING KERNELS...",
  "QUERYING GLOBAL CATALOG INDEX...",
  "RESOLVING SPATIAL REFERENCE SYSTEMS...",
  "VALIDATING NETWORK TOPOLOGY...",
  "OPTIMIZING SPATIAL INDICES...",
  "VERIFYING DATA INTEGRITY CHECKS..."
]

const TERMINAL_LOGS = [
  'init_sequence_alpha()',
  'loading_modules: [geo, dem, hydro]',
  'verifying_api_handshake... OK',
  'establishing_secure_channel...',
  'scanning_project_aoi()',
  'generating_dense_waypoints()',
  'compiling_route_constraints()',
  'hydrology_kernel.ready = true',
  'resolving_epsg_mismatch...',
  'buffering_vector_layers()',
  'fetching_catalog_metadata()',
  'prioritizing_local_sources()',
  'computing_cost_surface()',
  'aggregating_rl_metrics()',
  'validating_dataset_checksums()',
  'seeding_geoprocess_graph()',
  'evaluating_nodata_thresholds()',
  'processing_request...'
]

export function ZeusLoadingDialog({ open, onComplete }: ZeusLoadingDialogProps) {
  const [progress, setProgress] = useState(0)
  const [messageIndex, setMessageIndex] = useState(0)
  const [stage, setStage] = useState<'initializing' | 'processing' | 'finalizing'>('initializing')
  const [logLines, setLogLines] = useState<string[]>([])
  const [mounted, setMounted] = useState(false)
  const [isClosing, setIsClosing] = useState(false)

  const progressRef = useRef(0)
  const timeoutRef = useRef<NodeJS.Timeout | null>(null)
  const completionTimeoutRef = useRef<NodeJS.Timeout | null>(null)
  const startTimeRef = useRef(0)
  const durationRef = useRef(0)
  const finalHoldRef = useRef(false)

  useEffect(() => {
    setMounted(true)
  }, [])

  useEffect(() => {
    if (open) setIsClosing(false)
  }, [open])

  const clearTimers = () => {
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current)
      timeoutRef.current = null
    }
    if (completionTimeoutRef.current) {
      clearTimeout(completionTimeoutRef.current)
      completionTimeoutRef.current = null
    }
  }

  const updateStageForProgress = (value: number) => {
    if (value < 30) setStage('initializing')
    else if (value < 85) setStage('processing')
    else setStage('finalizing')
  }

  const updateLogsForProgress = (value: number, forceAll?: boolean) => {
    const totalEntries = TERMINAL_LOGS.length
    const desiredCount = forceAll
      ? totalEntries
      : Math.min(
          totalEntries,
          Math.max(1, Math.floor((value / 100) * totalEntries))
        )

    setLogLines((prev) => {
      if (prev.length >= desiredCount) return prev
      return TERMINAL_LOGS.slice(0, desiredCount)
    })
  }

  const startFinalHold = () => {
    if (finalHoldRef.current) return
    finalHoldRef.current = true

    setProgress(99)
    progressRef.current = 99
    updateStageForProgress(99)
    updateLogsForProgress(99, true)
    setMessageIndex((prev) => (prev + 1) % LOADING_MESSAGES.length)

    completionTimeoutRef.current = setTimeout(() => {
      setProgress(100)
      progressRef.current = 100
      updateStageForProgress(100)
      updateLogsForProgress(100, true)
      timeoutRef.current = setTimeout(() => {
        setIsClosing(true)
        setTimeout(() => {
          onComplete()
        }, 150)
      }, 400)
    }, 5000)
  }

  useEffect(() => {
    if (!open) {
      clearTimers()
      setProgress(0)
      setMessageIndex(0)
      setStage('initializing')
      setLogLines([])
      progressRef.current = 0
      finalHoldRef.current = false
      return
    }

    startTimeRef.current = Date.now()
    durationRef.current = Math.floor(Math.random() * (15000 - 10000) + 10000)
    finalHoldRef.current = false
    progressRef.current = 0
    setLogLines([TERMINAL_LOGS[0]])

    const runTick = () => {
      if (finalHoldRef.current) return

      const elapsed = Date.now() - startTimeRef.current
      const normalized = Math.min(elapsed / durationRef.current, 1)

      const baseProgress = normalized * 96 // keep below 99 until final hold
      const jitter = Math.random() * 2.5
      const candidate = Math.max(progressRef.current, Math.min(baseProgress + jitter, 96.5))

      const shouldPause = Math.random() < 0.25
      const nextDelay = shouldPause
        ? Math.floor(Math.random() * (1400 - 700) + 700)
        : Math.floor(Math.random() * (450 - 130) + 130)

      progressRef.current = candidate
      setProgress(candidate)
      updateStageForProgress(candidate)
      updateLogsForProgress(candidate)

      if (Math.random() < 0.3) {
        setMessageIndex((prev) => (prev + 1) % LOADING_MESSAGES.length)
      }

      if (candidate >= 96 || normalized >= 0.98) {
        startFinalHold()
        return
      }

      timeoutRef.current = setTimeout(runTick, nextDelay)
    }

    timeoutRef.current = setTimeout(runTick, 300)

    return () => {
      clearTimers()
    }
  }, [open, onComplete])

  // Don't render if not open
  if (!open || !mounted) return null

  return createPortal(
    <>
      <div className={cn(
        "fixed inset-0 z-[200] flex items-center justify-center bg-black/90 backdrop-blur-xl",
        isClosing ? "animate-fade-out" : "animate-fade-in"
      )}>
        {/* Background Effects */}
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_center,rgba(var(--primary),0.1),transparent_70%)]" />
        <div className="absolute inset-0 bg-[linear-gradient(to_right,rgba(255,255,255,0.02)_1px,transparent_1px),linear-gradient(to_bottom,rgba(255,255,255,0.02)_1px,transparent_1px)] bg-[size:40px_40px]" />

        <div className="relative w-[600px] bg-black/40 border border-white/10 rounded-sm p-8 flex flex-col gap-8 overflow-hidden shadow-[0_0_100px_-20px_rgba(var(--primary),0.3)]">
          
          {/* Central HUD Graphic */}
          <div className="relative h-32 flex items-center justify-center">
             {/* Rotating Rings */}
             <div className="absolute w-32 h-32 border border-primary/20 rounded-full animate-spin-slow [animation-duration:10s]" />
             <div className="absolute w-24 h-24 border border-primary/40 rounded-full border-dashed animate-spin-slow [animation-duration:15s] [animation-direction:reverse]" />
             
             {/* Core */}
             <div className="relative z-10 bg-black/50 border border-primary/50 p-4 rounded-full shadow-[0_0_30px_rgba(var(--primary),0.5)] animate-pulse">
                <Brain className="w-10 h-10 text-primary" />
             </div>

             {/* Orbiting Data */}
             <div className="absolute w-40 h-40 animate-spin-slow [animation-duration:8s]">
                <div className="absolute top-0 left-1/2 -translate-x-1/2 -translate-y-1/2 bg-black border border-white/20 p-1 rounded-sm">
                    <Database className="w-3 h-3 text-white/50" />
                </div>
             </div>
             <div className="absolute w-40 h-40 animate-spin-slow [animation-duration:12s] [animation-direction:reverse]">
                <div className="absolute bottom-0 left-1/2 -translate-x-1/2 translate-y-1/2 bg-black border border-white/20 p-1 rounded-sm">
                    <Globe className="w-3 h-3 text-white/50" />
                </div>
             </div>
          </div>

          {/* Text & Progress */}
          <div className="space-y-4 text-center">
             <div>
                <h3 className="text-xl font-bold text-white tracking-[0.2em] uppercase">
                    AGRS ZEUS <span className="text-primary">AI AGENT</span>
                </h3>
                <div className="flex items-center justify-center gap-2 text-[10px] text-white/40 font-mono mt-1 uppercase tracking-widest">
                    <span className="w-1.5 h-1.5 bg-emerald-500 rounded-full animate-ping" />
                    Processing Request Sequence
                </div>
             </div>

             <div className="h-8 flex items-center justify-center">
                <p className="text-xs font-mono text-primary/80 uppercase tracking-wider animate-pulse">
                    {LOADING_MESSAGES[messageIndex]}
                </p>
             </div>

             <div className="space-y-1">
                <div className="h-1 w-full bg-white/5 rounded-full overflow-hidden">
                    <div 
                        className="h-full bg-primary shadow-[0_0_10px_rgba(var(--primary),0.8)] transition-all duration-200 ease-linear"
                        style={{ width: `${progress}%` }}
                    />
                </div>
                <div className="flex justify-between text-[9px] font-mono text-white/30 uppercase">
                    <span>Sequence Progress</span>
                    <span>{Math.round(progress)}%</span>
                </div>
             </div>
          </div>

          {/* Terminal Output */}
          <div className="border-t border-white/10 pt-4">
             <div className="bg-black/50 p-4 rounded-sm border border-white/5 h-36 font-mono text-[10px] text-left relative overflow-hidden">
                {/* Top fade mask */}
                <div className="absolute top-0 left-0 right-0 h-8 bg-gradient-to-b from-black/80 to-transparent z-10 pointer-events-none" />
                
                <div className="flex flex-col justify-end h-full space-y-1.5 relative z-0">
                    {logLines.slice(-7).map((line, i) => (
                        <div key={i} className="text-white/60 truncate flex items-center animate-in slide-in-from-bottom-2 duration-300 fade-in">
                            <span className="text-primary/50 mr-2 shrink-0">{'>'}</span>
                            {line}
                        </div>
                    ))}
                    <div className="flex items-center">
                        <span className="text-primary/50 mr-2">{'>'}</span>
                        <span className="w-2 h-4 bg-primary/50 animate-pulse" />
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

