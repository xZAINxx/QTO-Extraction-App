/* ──────────────────────────────────────────────────────────────────
 * DataTable — paginated, filterable line-item table.
 *
 * Backed by `/api/extractions/{id}/rows` with cursor-based pagination
 * (the cursor is the position to start AFTER). The component fetches
 * the first page on mount + whenever the filter chips change, then
 * appends pages on the "Load more" button.
 *
 * Status pill column derives its tone from confidence + confirmed +
 * needs_review:
 *   confirmed         → yellow (the desktop's "I counted this" stamp)
 *   needs_review      → red
 *   confidence ≥ 0.9  → green
 *   confidence ≥ 0.6  → amber
 *   else              → red
 *
 * Click a confidence pill to toggle confirmed → calls PATCH /api/rows/{id}.
 * ────────────────────────────────────────────────────────────────── */

import { useCallback, useEffect, useState } from 'react'
import { CheckCircle2, AlertTriangle, Loader2 } from 'lucide-react'
import { apiFetch } from '../hooks/useApi.js'

const PAGE_LIMIT = 200

export default function DataTable({ extractionId }) {
  const [rows, setRows] = useState([])
  const [cursor, setCursor] = useState(0)
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [filters, setFilters] = useState({
    trade_division: null,
    source_sheet: null,
    needs_review: null,
    confirmed: null,
  })

  const fetchPage = useCallback(
    async ({ append = false, fromCursor = 0 } = {}) => {
      if (!extractionId) return
      setLoading(true)
      setError(null)
      try {
        const params = new URLSearchParams()
        params.set('cursor', String(fromCursor))
        params.set('limit', String(PAGE_LIMIT))
        for (const [k, v] of Object.entries(filters)) {
          if (v !== null && v !== undefined) params.set(k, String(v))
        }
        const data = await apiFetch(
          `/api/extractions/${extractionId}/rows?${params}`,
        )
        setRows((prev) => (append ? [...prev, ...data.rows] : data.rows))
        setCursor(data.next_cursor ?? 0)
        setTotal(data.total)
      } catch (err) {
        setError(err.message)
      } finally {
        setLoading(false)
      }
    },
    [extractionId, filters],
  )

  // Initial load + refetch on filter change. Resets cursor.
  useEffect(() => {
    setRows([])
    setCursor(0)
    fetchPage({ append: false, fromCursor: 0 }).catch(() => {})
  }, [fetchPage])

  const togglConfirmed = async (row) => {
    try {
      const next = !row.confirmed
      const updated = await apiFetch(`/api/rows/${row.id}`, {
        method: 'PATCH',
        body: { confirmed: next },
      })
      setRows((prev) => prev.map((r) => (r.id === row.id ? updated : r)))
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error('toggle-confirmed failed', err)
    }
  }

  if (!extractionId) {
    return (
      <section className="data-table data-table--empty">
        <p>Run an extraction to populate the line-item table.</p>
      </section>
    )
  }

  return (
    <section className="data-table">
      <div className="data-table__header">
        <div className="data-table__title-block">
          <h3 className="data-table__title">Line items</h3>
          <span className="data-table__count">
            {rows.length} of {total}
          </span>
        </div>
        {error && <div className="data-table__error">{error}</div>}
      </div>

      <div className="data-table__filters">
        <FilterToggle
          label="Needs review only"
          active={filters.needs_review === true}
          onClick={() =>
            setFilters((f) => ({
              ...f,
              needs_review: f.needs_review === true ? null : true,
            }))
          }
        />
        <FilterToggle
          label="Confirmed only"
          active={filters.confirmed === true}
          onClick={() =>
            setFilters((f) => ({
              ...f,
              confirmed: f.confirmed === true ? null : true,
            }))
          }
        />
      </div>

      <div className="data-table__scroll">
        <table className="data-table__table">
          <thead>
            <tr>
              <th className="dt-col-status" />
              <th className="dt-col-num">#</th>
              <th>Description</th>
              <th>Sheet</th>
              <th>Division</th>
              <th className="dt-col-num">Qty</th>
              <th>Units</th>
              <th className="dt-col-num">$/unit</th>
              <th className="dt-col-num">Confidence</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr
                key={r.id}
                className={
                  'dt-row' +
                  (r.confirmed ? ' dt-row--confirmed' : '') +
                  (r.needs_review ? ' dt-row--review' : '') +
                  (r.is_header_row ? ' dt-row--header' : '')
                }
              >
                <td className="dt-col-status">
                  <StatusPill row={r} onToggle={() => togglConfirmed(r)} />
                </td>
                <td className="dt-col-num">{r.s_no ?? '—'}</td>
                <td className="dt-col-desc">
                  {r.description ?? <span className="dt-muted">—</span>}
                  {r.tag && <span className="dt-tag">{r.tag}</span>}
                </td>
                <td>{r.source_sheet ?? '—'}</td>
                <td>{r.trade_division ?? '—'}</td>
                <td className="dt-col-num">{formatNum(r.qty)}</td>
                <td>{r.units ?? '—'}</td>
                <td className="dt-col-num">{formatMoney(r.unit_price)}</td>
                <td className="dt-col-num">
                  {r.confidence != null
                    ? `${(r.confidence * 100).toFixed(0)}%`
                    : '—'}
                </td>
              </tr>
            ))}
            {rows.length === 0 && !loading && (
              <tr>
                <td colSpan={9} className="dt-empty-row">
                  {total === 0
                    ? 'No rows yet — run an extraction to populate.'
                    : 'No rows match the current filters.'}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="data-table__footer">
        {loading && (
          <div className="data-table__loading">
            <Loader2 size={14} className="upload-dropzone__spin" /> loading…
          </div>
        )}
        {cursor != null && cursor > 0 && !loading && (
          <button
            type="button"
            className="btn btn--ghost"
            onClick={() =>
              fetchPage({ append: true, fromCursor: cursor }).catch(() => {})
            }
          >
            Load more
          </button>
        )}
      </div>
    </section>
  )
}


// ──────────────────────────────────────────────────────────────────
// Bits
// ──────────────────────────────────────────────────────────────────


function StatusPill({ row, onToggle }) {
  // Confirmed always wins visually — yellow stamp.
  if (row.confirmed) {
    return (
      <button
        type="button"
        className="status-pill status-pill--confirmed"
        onClick={onToggle}
        title="Click to unconfirm"
      >
        <CheckCircle2 size={11} />
      </button>
    )
  }
  if (row.needs_review) {
    return (
      <span className="status-pill status-pill--review" title="Needs review">
        <AlertTriangle size={11} />
      </span>
    )
  }
  const conf = row.confidence ?? 0
  const tone =
    conf >= 0.9 ? 'high' : conf >= 0.6 ? 'mid' : 'low'
  return (
    <button
      type="button"
      className={`status-pill status-pill--${tone}`}
      onClick={onToggle}
      title={`Confidence ${(conf * 100).toFixed(0)}% · click to confirm`}
    >
      {(conf * 100).toFixed(0)}
    </button>
  )
}


function FilterToggle({ label, active, onClick }) {
  return (
    <button
      type="button"
      className={'chip' + (active ? ' chip--active' : '')}
      onClick={onClick}
    >
      {label}
    </button>
  )
}


function formatNum(value) {
  if (value == null) return '—'
  if (Number.isInteger(value)) return value.toLocaleString()
  return value.toLocaleString(undefined, { maximumFractionDigits: 2 })
}


function formatMoney(value) {
  if (value == null) return '—'
  return `$${Number(value).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`
}
