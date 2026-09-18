'use client'

import { useEffect, useState, type ReactNode } from 'react'
import { Loader2 } from 'lucide-react'
import { API_BASE_URL } from '@/lib/api-client'

export function CloudWorkspaceGate({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let mounted = true
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 30000)
    setError(null)
    fetch(`${API_BASE_URL}/session`, { method: 'POST', credentials: 'same-origin', signal: controller.signal })
      .then(async response => {
        const body = await response.json()
        if (!response.ok) throw new Error(body.detail || 'Temporary storage is unavailable. Try again shortly.')
        if (mounted && !controller.signal.aborted) {
          // Never reuse another expired workspace's cached project list.
          localStorage.removeItem('agrs_projects_cache')
          setReady(true)
        }
      }).catch(reason => {
        if (!mounted) return
        if (reason.name !== 'AbortError') setError(reason.message)
        else setError('Opening the workspace took too long. Please try again.')
      }).finally(() => clearTimeout(timer))
    return () => { mounted = false; controller.abort(); clearTimeout(timer) }
  }, [attempt])
  if (ready) return <>{children}</>
  return <main className="flex min-h-screen items-center justify-center bg-[#090b0c] text-white">
    <div className="max-w-sm px-6 text-center">
      <div className="font-[family-name:var(--font-cinzel)] text-4xl text-[#ef4438]">Vulcan</div>
      {error ? <><p role="alert" className="mt-5 text-sm text-white/60">{error}</p>
        <button onClick={() => setAttempt(value => value + 1)} className="mt-4 rounded-md border border-white/20 px-4 py-2 text-sm">Try again</button></>
        : <><Loader2 className="mx-auto mt-6 h-5 w-5 animate-spin text-red-500" /><p className="mt-4 text-sm text-white/50">Opening your workspace…</p></>}
    </div>
  </main>
}
