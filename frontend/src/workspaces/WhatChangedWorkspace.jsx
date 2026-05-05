/* ──────────────────────────────────────────────────────────────────
 * WhatChangedWorkspace — set-diff between two extractions on the same
 * project.
 *
 * PR #2 ships the surface + a stub diff route; the real diff payload
 * (changed-pages list, $ impact column, sheet roster) lands in PR #3
 * alongside the rest of the annotation toolkit. This workspace stays
 * useful as-is — picks two extractions to compare, shows their
 * metadata side-by-side, and surfaces the route's stub message.
 * ────────────────────────────────────────────────────────────────── */

import { useEffect, useMemo, useState } from 'react'
import { GitCompareArrows } from 'lucide-react'
import { apiFetch } from '../hooks/useApi.js'
import useProjectStore from '../stores/projectStore.js'

export default function WhatChangedWorkspace() {
  const projects = useProjectStore((s) => s.projects)
  const activeProjectId = useProjectStore((s) => s.activeProjectId)
  const activeProject = projects.find((p) => p.id === activeProjectId)
  const pdfs = useProjectStore((s) => s.pdfs)
  const extraction = useProjectStore((s) => s.extraction)

  const [extractions, setExtractions] = useState([])
  const [baseId, setBaseId] = useState(null)
  const [compareId, setCompareId] = useState(null)
  const [diff, setDiff] = useState(null)
  const [error, setError] = useState(null)

  // Fetch the project's recent extractions. The backend doesn't yet
  // expose a "list extractions" route — we synthesise from the active
  // run + recent state. Full list endpoint lands in a follow-up.
  useEffect(() => {
    if (!extraction.extractionId) return
    setExtractions((prev) => {
      if (prev.find((e) => e.id === extraction.extractionId)) return prev
      return [
        ...prev,
        {
          id: extraction.extractionId,
          status: extraction.status,
          rowCount: extraction.rowCount,
          cost: extraction.cost.cost_usd,
        },
      ]
    })
  }, [extraction.extractionId, extraction.status, extraction.rowCount, extraction.cost.cost_usd])

  useEffect(() => {
    if (!baseId || !compareId) return
    apiFetch(`/api/extractions/${baseId}/diff/${compareId}`)
      .then(setDiff)
      .catch((e) => setError(e.message))
  }, [baseId, compareId])

  const canDiff = useMemo(
    () => extractions.length >= 2,
    [extractions.length],
  )

  return (
    <div className="content">
      <div className="content__header">
        <div>
          <h1 className="content__title">What Changed</h1>
          <p className="content__subtitle">
            Compare two extraction runs on{' '}
            {activeProject?.name ?? 'this project'}. Useful for revision
            sets — line up the bid against the latest drawings.
          </p>
        </div>
      </div>

      <section className="data-table">
        <div className="data-table__header">
          <div className="data-table__title-block">
            <h3 className="data-table__title">Pick two extractions</h3>
          </div>
        </div>
        <div style={{ padding: 16, display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <ExtractionPicker
            label="Base"
            extractions={extractions}
            value={baseId}
            onChange={setBaseId}
          />
          <ExtractionPicker
            label="Compare"
            extractions={extractions}
            value={compareId}
            onChange={setCompareId}
          />
        </div>
        {!canDiff && (
          <div
            style={{
              padding: '12px 16px',
              borderTop: '1px solid var(--border-subtle)',
              fontSize: 12,
              color: 'var(--text-3)',
            }}
          >
            Run at least two extractions on this project to enable the
            diff. {pdfs.length === 0
              ? 'Upload a PDF first.'
              : 'Run another extraction (or upload a revised PDF and re-run).'}
          </div>
        )}
      </section>

      {error && (
        <section className="empty-card empty-card--danger">
          <h2 className="empty-card__title">Diff failed</h2>
          <p className="empty-card__body">{error}</p>
        </section>
      )}

      {diff && (
        <section className="diff-summary">
          <div className="diff-summary__heading">
            <GitCompareArrows size={14} /> Set diff
          </div>
          <p className="diff-summary__line">{diff.summary}</p>
          <p className="diff-summary__note">{diff.note}</p>
        </section>
      )}
    </div>
  )
}


function ExtractionPicker({ label, extractions, value, onChange }) {
  return (
    <label
      style={{ display: 'flex', flexDirection: 'column', gap: 4 }}
    >
      <span
        style={{
          fontSize: 11,
          fontWeight: 600,
          color: 'var(--text-3)',
          letterSpacing: '0.04em',
          textTransform: 'uppercase',
        }}
      >
        {label}
      </span>
      <select
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value || null)}
        style={{
          height: 32,
          padding: '0 12px',
          borderRadius: 8,
          border: '1px solid var(--border-default)',
          background: 'var(--surface-2)',
          color: 'var(--text-1)',
          fontFamily: 'inherit',
          fontSize: 12,
          minWidth: 240,
        }}
      >
        <option value="">— pick —</option>
        {extractions.map((e) => (
          <option key={e.id} value={e.id}>
            {e.id.slice(0, 8)} · {e.status} · {e.rowCount ?? 0} rows
          </option>
        ))}
      </select>
    </label>
  )
}
