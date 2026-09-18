'use client'

import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

export function ProjectLoadingDialog({ open }: { open: boolean }) {
  const [mounted, setMounted] = useState(false)
  const indicator = useRef<HTMLDivElement>(null)
  useEffect(() => setMounted(true), [])
  useEffect(() => {
    if (!open || !mounted) return
    const workspace = document.getElementById('zeus-workspace')
    const previousFocus = document.activeElement as HTMLElement | null
    workspace?.setAttribute('inert', '')
    indicator.current?.focus()
    return () => {
      workspace?.removeAttribute('inert')
      if (previousFocus?.isConnected) previousFocus.focus()
    }
  }, [open, mounted])

  if (!open || !mounted) return null
  return createPortal(
    <div ref={indicator} role="status" aria-label="Loading project" tabIndex={-1}
      onKeyDown={event => { if (event.key === 'Tab') event.preventDefault() }}
      className="fixed inset-0 z-[2147483647] flex items-center justify-center bg-black/20 backdrop-blur-lg outline-none cursor-wait">
      <div aria-hidden="true" className="h-14 w-14 rounded-full border-4 border-red-600/20 border-t-red-600 animate-spin" />
      <span className="sr-only">Loading project</span>
    </div>, document.body,
  )
}
