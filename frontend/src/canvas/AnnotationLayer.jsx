/* ──────────────────────────────────────────────────────────────────
 * AnnotationLayer — SVG overlay that renders annotations on top of a
 * PDF page render. Phase 4 / commit 15 lands the foundation:
 *
 *   - Reads annotations for (pdfId, sheetNumber) from the store
 *   - Renders each annotation by type (highlight / cloud / callout
 *     / dimension / text_box / legend)
 *   - Click-to-select; click-on-empty deselects
 *   - Keyboard shortcuts: Esc clears selection, Del deletes selected
 *
 * The 6 tool variants (active drag-to-create) ship in commits 16–19.
 * For now this layer is render-only — sufficient for the canvas to
 * display annotations created from the desktop app or via direct
 * API calls.
 *
 * The component is deliberately framework-light. A future commit
 * mounts it inside a PDF.js page (via react-pdf) once we wire that
 * library in; until then it can render against any container with a
 * known coordinate space (CSS-pixel or normalised 0–1).
 * ────────────────────────────────────────────────────────────────── */

import { useEffect } from 'react'
import useAnnotationStore from '../stores/annotationStore.js'


export default function AnnotationLayer({
  pdfId,
  sheetNumber,
  width,
  height,
}) {
  const annotations = useAnnotationStore(
    (s) => (s.byPdf[pdfId] ?? {})[sheetNumber] ?? [],
  )
  const selection = useAnnotationStore((s) => s.selection)
  const fetchForPdf = useAnnotationStore((s) => s.fetchForPdf)
  const selectOnly = useAnnotationStore((s) => s.selectOnly)
  const clearSelection = useAnnotationStore((s) => s.clearSelection)
  const deleteSelection = useAnnotationStore((s) => s.deleteSelection)

  useEffect(() => {
    if (pdfId) fetchForPdf(pdfId).catch(() => {})
  }, [pdfId, fetchForPdf])

  useEffect(() => {
    function onKey(e) {
      if (e.key === 'Escape') clearSelection()
      if (e.key === 'Delete' || e.key === 'Backspace') {
        if (selection.size > 0) {
          e.preventDefault()
          deleteSelection().catch(() => {})
        }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [selection.size, clearSelection, deleteSelection])

  return (
    <svg
      className="annotation-layer"
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      onClick={(e) => {
        // Click on empty SVG (not a child) clears selection.
        if (e.target.tagName === 'svg') clearSelection()
      }}
    >
      {annotations.map((a) => (
        <AnnotationShape
          key={a.id}
          annotation={a}
          selected={selection.has(a.id)}
          onClick={() => selectOnly(a.id)}
        />
      ))}
    </svg>
  )
}


function AnnotationShape({ annotation, selected, onClick }) {
  const { type, geometry, color } = annotation
  const stroke = selected ? 'var(--violet)' : color
  const strokeWidth = selected ? 3 : 1.5
  const fill = `${color}33`  // ~20% alpha hex

  switch (type) {
    case 'highlight': {
      const { x, y, w, h } = geometry
      return (
        <rect
          x={x}
          y={y}
          width={w}
          height={h}
          fill={fill}
          stroke={stroke}
          strokeWidth={strokeWidth}
          onClick={(e) => {
            e.stopPropagation()
            onClick()
          }}
          style={{ cursor: 'pointer' }}
        />
      )
    }
    case 'cloud': {
      const { points = [] } = geometry
      // points = [[x1, y1], [x2, y2], …]; render with rough wavy edge
      // approximated as a polyline for now (full bezier-cloud rendering
      // lands in commit 18).
      const d = points.length === 0
        ? ''
        : 'M ' + points.map(([x, y]) => `${x},${y}`).join(' L ') + ' Z'
      return (
        <path
          d={d}
          fill={fill}
          stroke={stroke}
          strokeWidth={strokeWidth}
          strokeDasharray="6 4"
          onClick={(e) => {
            e.stopPropagation()
            onClick()
          }}
          style={{ cursor: 'pointer' }}
        />
      )
    }
    case 'dimension': {
      const { start, end, label } = geometry
      const midX = (start[0] + end[0]) / 2
      const midY = (start[1] + end[1]) / 2
      return (
        <g
          onClick={(e) => {
            e.stopPropagation()
            onClick()
          }}
          style={{ cursor: 'pointer' }}
        >
          <line
            x1={start[0]}
            y1={start[1]}
            x2={end[0]}
            y2={end[1]}
            stroke={stroke}
            strokeWidth={strokeWidth}
          />
          {label && (
            <text
              x={midX}
              y={midY - 4}
              fill={stroke}
              fontSize={11}
              fontFamily="var(--font-mono)"
              textAnchor="middle"
            >
              {label}
            </text>
          )}
        </g>
      )
    }
    case 'callout': {
      const { anchor, leader, label } = geometry
      return (
        <g
          onClick={(e) => {
            e.stopPropagation()
            onClick()
          }}
          style={{ cursor: 'pointer' }}
        >
          {leader && anchor && (
            <line
              x1={anchor[0]}
              y1={anchor[1]}
              x2={leader[0]}
              y2={leader[1]}
              stroke={stroke}
              strokeWidth={strokeWidth}
            />
          )}
          {leader && label && (
            <text
              x={leader[0]}
              y={leader[1]}
              fill={stroke}
              fontSize={11}
              fontFamily="var(--font-sans)"
            >
              {label}
            </text>
          )}
        </g>
      )
    }
    case 'text_box': {
      const { x, y, label } = geometry
      return (
        <text
          x={x}
          y={y}
          fill={stroke}
          fontSize={12}
          fontFamily="var(--font-sans)"
          onClick={(e) => {
            e.stopPropagation()
            onClick()
          }}
          style={{ cursor: 'pointer' }}
        >
          {label || annotation.label || ''}
        </text>
      )
    }
    case 'legend': {
      // Legend renders as an outlined rect with the label text inside
      // — full categorical-cell grid lands in commit 19.
      const { x, y, w, h } = geometry
      return (
        <g
          onClick={(e) => {
            e.stopPropagation()
            onClick()
          }}
          style={{ cursor: 'pointer' }}
        >
          <rect
            x={x}
            y={y}
            width={w}
            height={h}
            fill={fill}
            stroke={stroke}
            strokeWidth={strokeWidth}
          />
          <text
            x={x + 6}
            y={y + 16}
            fill={stroke}
            fontSize={11}
            fontFamily="var(--font-sans)"
          >
            {annotation.label || 'Legend'}
          </text>
        </g>
      )
    }
    default:
      return null
  }
}
