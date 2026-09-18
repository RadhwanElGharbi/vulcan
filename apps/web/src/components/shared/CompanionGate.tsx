'use client'

import { useState, type ReactNode } from 'react'
import { ArrowRight, Download, Loader2 } from 'lucide-react'
import { API_BASE_URL } from '@/lib/api-client'
import { CloudWorkspaceGate } from './CloudWorkspaceGate'

export function CompanionGate({ children }: { children: ReactNode }) {
  const [connected, setConnected] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  if (process.env.NEXT_PUBLIC_WEB_PREVIEW === '1') return <>{children}
    <div role="status" className="fixed bottom-4 left-1/2 -translate-x-1/2 z-40 rounded border border-white/10 bg-black/70 backdrop-blur-md px-4 py-2 text-xs text-white/60">
      Preview · Cloud project storage and fetching are not connected yet.
    </div>
  </>
  if (process.env.NEXT_PUBLIC_CLOUD_MODE === '1') return <CloudWorkspaceGate>{children}</CloudWorkspaceGate>
  if (process.env.NEXT_PUBLIC_COMPANION_MODE !== '1' || connected) return <>{children}</>

  const connect = async () => {
    setBusy(true); setError(null)
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 15000)
    try {
      const response = await fetch(`${API_BASE_URL}/health`, { cache: 'no-store', signal: controller.signal })
      const health = await response.json()
      if (!response.ok || health.application !== 'VULCAN' || health.companion_protocol !== 1) throw new Error('Update and start the VULCAN companion, then reconnect.')
      setConnected(true)
    } catch {
      setError('Start the VULCAN companion, then try again. If your browser asks for local network access, allow it for this site. Use a current version of Chrome or Edge on Windows.')
    } finally { clearTimeout(timeout); setBusy(false) }
  }

  return <main className="min-h-screen bg-[#090b0c] text-white flex items-center justify-center p-6">
    <section className="w-full max-w-xl border border-white/10 bg-white/[0.025] p-8 md:p-12 rounded-sm shadow-2xl">
      <div className="font-[family-name:var(--font-cinzel)] text-4xl text-[#ef4438] tracking-wide">Vulcan</div>
      <h1 className="mt-8 text-2xl font-medium">Your world. Your workspace.</h1>
      <p className="mt-3 text-sm leading-6 text-white/55">Explore the globe and acquire geospatial datasets. The local companion runs your fetch jobs and saves projects in a folder you choose on your computer.</p>
      <ol className="mt-7 space-y-4 text-sm text-white/75 list-decimal pl-5">
        <li><a href="/downloads/vulcan-companion-windows.zip" download className="inline-flex items-center gap-2 text-white underline underline-offset-4"><Download className="h-4 w-4" />Download the Windows companion</a></li>
        <li>Extract the ZIP, then double-click <strong className="font-medium text-white">start-companion.cmd</strong>. The first start installs the required runtime and may take several minutes.</li>
        <li>Keep the companion running and connect below.</li>
      </ol>
      <button disabled={busy} onClick={() => void connect()} className="mt-8 flex w-full items-center justify-center gap-3 bg-[#e52325] hover:bg-[#f02e30] disabled:opacity-60 px-5 py-3 text-sm font-medium rounded-sm">
        {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <ArrowRight className="h-4 w-4" />}{busy ? 'Connecting...' : 'Connect local companion'}
      </button>
      {error && <p role="alert" className="mt-4 text-sm leading-6 text-red-300">{error}</p>}
      <p className="mt-5 text-xs leading-5 text-white/35">Windows 64-bit · Your saved projects and downloaded data stay on your computer.</p>
    </section>
  </main>
}
