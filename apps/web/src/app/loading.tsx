export default function Loading() {
  return (
    <div className="fixed inset-0 bg-black z-50 flex items-center justify-center">
      <div className="flex flex-col items-center gap-4">
        {/* Minimalist pulse loader */}
        <div className="w-12 h-12 relative">
          <div className="absolute inset-0 border-2 border-white/20 rounded-full"></div>
          <div className="absolute inset-0 border-2 border-primary border-t-transparent rounded-full animate-spin"></div>
        </div>
        <div className="text-xs font-mono uppercase tracking-[0.3em] text-white/40 animate-pulse">
          Loading
        </div>
      </div>
    </div>
  )
}


















