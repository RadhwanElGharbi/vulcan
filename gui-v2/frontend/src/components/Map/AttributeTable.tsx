import { ManagedLayer,VectorDetail } from '@/lib/map-utils'
import { cn } from '@/lib/utils'
import { Table } from 'lucide-react'
import React,{ useEffect,useMemo,useRef,useState } from 'react'

interface AttributeTableProps {
  layer: ManagedLayer
  details: VectorDetail
  sortedRows: { row: Record<string, any>; feature: any }[]
  sortConfig: { column: string | null; direction: 'asc' | 'desc' }
  isDocked: boolean
  dockHeight: number
  onClose: () => void
  onToggleDock: () => void
  onSort: (column: string) => void
  onRowDoubleClick: (feature: any) => void
  onResizeStart: (event: React.MouseEvent) => void
  dockContainerRef: React.RefObject<HTMLDivElement>
}

const ROW_HEIGHT = 32
const BUFFER = 15

export const AttributeTable = React.memo(function AttributeTable({
  layer,
  details,
  sortedRows,
  sortConfig,
  isDocked,
  dockHeight,
  onClose,
  onToggleDock,
  onSort,
  onRowDoubleClick,
  onResizeStart,
  dockContainerRef
}: AttributeTableProps) {
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const [scrollTop, setScrollTop] = useState(0)
  const [containerHeight, setContainerHeight] = useState(400)
  const [isClosing, setIsClosing] = useState(false)

  const handleClose = () => {
    setIsClosing(true)
    setTimeout(onClose, 150)
  }

  // Measure container height for virtualization
  useEffect(() => {
    if (!scrollContainerRef.current) return
    const updateHeight = () => {
      if (scrollContainerRef.current) {
        setContainerHeight(scrollContainerRef.current.clientHeight)
      }
    }
    updateHeight()
    // We can use ResizeObserver to detect container size changes
    const observer = new ResizeObserver(updateHeight)
    observer.observe(scrollContainerRef.current)
    return () => observer.disconnect()
  }, [isDocked, dockHeight])

  const handleScroll = (e: React.UIEvent<HTMLDivElement>) => {
    setScrollTop(e.currentTarget.scrollTop)
  }

  const totalCount = sortedRows.length
  const startIndex = Math.floor(scrollTop / ROW_HEIGHT)
  const endIndex = Math.min(
    totalCount,
    Math.floor((scrollTop + containerHeight) / ROW_HEIGHT) + BUFFER
  )

  const visibleRows = useMemo(() => {
    const start = Math.max(0, startIndex - BUFFER)
    const end = Math.min(totalCount, endIndex)
    return sortedRows.slice(start, end).map((row, index) => ({
      ...row,
      virtualIndex: start + index
    }))
  }, [sortedRows, startIndex, endIndex, totalCount])

  const paddingTop = Math.max(0, startIndex - BUFFER) * ROW_HEIGHT
  const paddingBottom = Math.max(0, totalCount - endIndex) * ROW_HEIGHT

  return (
    <div
      className={cn(
        "fixed",
        isDocked
          ? 'absolute z-40 bottom-0 left-0 right-0'
          : 'inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4',
        isClosing ? "animate-fade-out" : "animate-fade-in"
      )}
      style={!isDocked ? { position: 'fixed' } : { position: 'absolute' }}
    >
      <div
        className={`bg-card text-foreground border border-border rounded-lg shadow-2xl overflow-hidden flex flex-col ${
          isDocked ? 'w-full rounded-none border-x-0 border-b-0' : 'max-w-5xl w-full max-h-[80vh]'
        }`}
        style={
          isDocked
            ? {
                margin: 0,
                borderRadius: 0,
                height: `${dockHeight}vh`,
                maxHeight: `${dockHeight}vh`
              }
            : undefined
        }
        ref={isDocked ? dockContainerRef : undefined}
      >
        {isDocked && (
          <div
            className="absolute top-0 left-0 right-0 h-2 cursor-ns-resize hover:bg-primary/20 transition-colors z-50"
            style={{ transform: 'translateY(-2px)' }}
            onMouseDown={onResizeStart}
            title="Drag to resize height"
          />
        )}
        <div className="flex items-center justify-between px-4 py-3 border-b border-border bg-card flex-shrink-0 h-[50px]">
          <div className="flex items-center gap-2">
            <Table className="w-4 h-4" />
            <div className="text-sm font-semibold">
              {layer.name} · attributes ({details.rows.length} rows)
            </div>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={onToggleDock}
              className="text-xs font-medium text-primary hover:underline"
            >
              {isDocked ? 'Undock' : 'Dock to bottom'}
            </button>
            <button
              onClick={handleClose}
              className="text-xs font-medium text-primary hover:underline"
            >
              Close
            </button>
          </div>
        </div>
        
        <div 
          className="flex-1 overflow-auto bg-card" 
          ref={scrollContainerRef}
          onScroll={handleScroll}
        >
          <table className="min-w-full text-[11px] table-fixed">
            <thead className="bg-card sticky top-0 z-10 shadow-sm h-[32px]">
              <tr>
                {details.properties.map((prop) => {
                  const isActive = sortConfig.column === prop
                  const direction = isActive ? sortConfig.direction : null
                  return (
                    <th
                      key={prop}
                      className="px-2 py-1 text-left font-semibold cursor-pointer select-none hover:bg-muted/50 transition-colors bg-card"
                      onClick={() => onSort(prop)}
                      style={{ height: ROW_HEIGHT }}
                    >
                      <div className="flex items-center gap-1">
                        <span>{prop}</span>
                        {direction && <span className="text-[10px]">{direction === 'asc' ? '▲' : '▼'}</span>}
                      </div>
                    </th>
                  )
                })}
              </tr>
            </thead>
            <tbody>
              {paddingTop > 0 && (
                <tr>
                  <td style={{ height: paddingTop }} colSpan={details.properties.length} />
                </tr>
              )}
              {visibleRows.map((entry) => (
                <tr
                  key={entry.virtualIndex}
                  className="odd:bg-background even:bg-muted/30 hover:bg-primary/10 cursor-pointer transition-colors"
                  style={{ height: ROW_HEIGHT }}
                  onDoubleClick={() => onRowDoubleClick(entry.feature)}
                >
                  {details.properties.map((prop) => (
                    <td key={prop} className="px-2 py-1 whitespace-nowrap max-w-[200px] truncate border-b border-border/30">
                      {entry.row?.[prop] !== undefined ? String(entry.row[prop]) : ''}
                    </td>
                  ))}
                </tr>
              ))}
              {paddingBottom > 0 && (
                <tr>
                  <td style={{ height: paddingBottom }} colSpan={details.properties.length} />
                </tr>
              )}
              {visibleRows.length === 0 && (
                <tr>
                  <td className="px-2 py-2 text-muted-foreground" colSpan={details.properties.length}>
                    No attribute rows available.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
})

