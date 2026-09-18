'use client'

import {
DatasetFetchJob,
subscribeToDatasetJob
} from '@/lib/api/dataClient'
import { cn } from '@/lib/utils'
import { CheckCircle2,Database,Loader2,Maximize2,XCircle } from 'lucide-react'
import { useEffect,useState } from 'react'

interface BackgroundJobIndicatorProps {
  jobId: string | null
  onExpand: () => void
  onJobFinished?: (job: DatasetFetchJob) => void
}

export function BackgroundJobIndicator({ jobId, onExpand, onJobFinished }: BackgroundJobIndicatorProps) {
  const [job, setJob] = useState<DatasetFetchJob | null>(null)
  const [isVisible, setIsVisible] = useState(false)
  const [completionHandled, setCompletionHandled] = useState(false)

  useEffect(() => {
    if (!jobId) {
      setJob(null)
      setIsVisible(false)
      setCompletionHandled(false)
      return
    }

    setIsVisible(true)
    setCompletionHandled(false)

    const unsubscribe = subscribeToDatasetJob(
      jobId,
      (payload) => {
        setJob(payload)
        if (!completionHandled && (payload.status === 'succeeded' || payload.status === 'failed' || payload.status === 'partial' || payload.status === 'cancelled')) {
          setCompletionHandled(true)
          onJobFinished?.(payload)
        }
      },
      (err) => {
        console.error('Background job subscription error:', err)
      }
    )

    return () => unsubscribe()
  }, [jobId, onJobFinished, completionHandled])

  if (!isVisible || !jobId || !job) return null

  const isComplete = job.status === 'succeeded' || job.status === 'failed' || job.status === 'partial' || job.status === 'cancelled'
  const isSuccess = job.status === 'succeeded'
  const isPartial = job.status === 'partial'
  const progress = Math.round((job.progress ?? 0) * 100)

  return (
    <div className="fixed bottom-4 right-4 z-50">
      <button
        onClick={onExpand}
        className={cn(
          "group relative flex items-center gap-3 px-4 py-3 rounded-lg border backdrop-blur-md shadow-lg transition-all duration-300 hover:scale-105",
          isComplete
            ? isSuccess
              ? "bg-emerald-500/20 border-emerald-500/40 hover:bg-emerald-500/30"
              : isPartial
                ? "bg-amber-500/20 border-amber-500/40 hover:bg-amber-500/30"
                : "bg-red-500/20 border-red-500/40 hover:bg-red-500/30"
            : "bg-black/80 border-primary/40 hover:bg-black/90 hover:border-primary/60"
        )}
      >
        {/* Animated pulse for running jobs */}
        {!isComplete && (
          <div className="absolute inset-0 rounded-lg animate-pulse bg-primary/5" />
        )}

        {/* Icon */}
        <div className={cn(
          "relative p-2 rounded-md",
          isComplete
            ? isSuccess
              ? "bg-emerald-500/20"
              : isPartial
                ? "bg-amber-500/20"
                : "bg-red-500/20"
            : "bg-primary/20"
        )}>
          {isComplete ? (
            isSuccess ? (
              <CheckCircle2 className="w-4 h-4 text-emerald-400" />
            ) : isPartial ? (
              <CheckCircle2 className="w-4 h-4 text-amber-400" />
            ) : (
              <XCircle className="w-4 h-4 text-red-400" />
            )
          ) : (
            <Loader2 className="w-4 h-4 text-primary animate-spin" />
          )}
        </div>

        {/* Content */}
        <div className="flex flex-col items-start min-w-[140px]">
          <div className="flex items-center gap-2">
            <Database className="w-3 h-3 text-white/50" />
            <span className="text-[10px] font-mono uppercase tracking-wider text-white/70">
              Dataset Fetch
            </span>
          </div>

          <div className="flex items-center gap-2 mt-1">
            <span className={cn(
              "text-xs font-bold uppercase",
              isComplete
                ? isSuccess
                  ? "text-emerald-400"
                  : isPartial
                    ? "text-amber-400"
                    : "text-red-400"
                : "text-primary"
            )}>
              {isComplete
                ? isSuccess
                  ? "COMPLETE"
                  : isPartial
                    ? "PARTIAL"
                    : job.status === 'cancelled' ? 'CANCELLED' : "FAILED"
                : job.status === 'awaiting_approval' ? 'REVIEW REQUIRED' : job.status === 'cancelling' ? 'STOPPING WORKER' : job.current_category?.toUpperCase() || "PROCESSING"}
            </span>
            {!isComplete && (
              <span className="text-[10px] font-mono text-white/50">
                {progress}%
              </span>
            )}
          </div>

          {/* Progress bar */}
          {!isComplete && (
            <div className="w-full h-1 bg-white/10 rounded-full overflow-hidden mt-2">
              <div
                className="h-full bg-primary transition-all duration-300 shadow-[0_0_6px_rgba(var(--primary),0.6)]"
                style={{ width: `${progress}%` }}
              />
            </div>
          )}
        </div>

        {/* Expand hint */}
        <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
          <Maximize2 className="w-3 h-3 text-white/40" />
          <span className="text-[9px] text-white/40 uppercase">Expand</span>
        </div>
      </button>
    </div>
  )
}
