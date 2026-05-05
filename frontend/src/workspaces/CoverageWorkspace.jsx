/* ──────────────────────────────────────────────────────────────────
 * CoverageWorkspace — port of ui/workspaces/coverage_workspace.py.
 * Surfaces:
 *   - Empty CSI divisions (the "holes" in the takeoff)
 *   - Per-division row counts
 *   - Silent-skip sheets (productive sheets with no rows)
 * ────────────────────────────────────────────────────────────────── */

import { useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2 } from 'lucide-react'
import { apiFetch } from '../hooks/useApi.js'
import useProjectStore from '../stores/projectStore.js'

export default function CoverageWorkspace() {
  const extractionId = useProjectStore((s) => s.extraction.extractionId)
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!extractionId) return
    apiFetch(`/api/extractions/${extractionId}/coverage`)
      .then(setData)
      .catch((e) => setError(e.message))
  }, [extractionId])

  if (!extractionId) {
    return (
      <div className="content">
        <div className="content__header">
          <div>
            <h1 className="content__title">Coverage</h1>
            <p className="content__subtitle">
              Run an extraction to see division coverage + flag the silent
              skips.
            </p>
          </div>
        </div>
        <section className="empty-card empty-card--soft">
          <h2 className="empty-card__title">Waiting for an extraction</h2>
          <p className="empty-card__body">
            The Coverage view shows which CSI divisions came up empty
            (likely missing scope) and which sheets the parser thought
            should be productive but produced no rows.
          </p>
        </section>
      </div>
    )
  }

  if (error) {
    return (
      <div className="content">
        <div className="content__header">
          <div>
            <h1 className="content__title">Coverage</h1>
            <p className="content__subtitle">{error}</p>
          </div>
        </div>
      </div>
    )
  }

  if (!data) {
    return (
      <div className="content">
        <div className="content__header">
          <div>
            <h1 className="content__title">Coverage</h1>
            <p className="content__subtitle">Loading coverage…</p>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="content">
      <div className="content__header">
        <div>
          <h1 className="content__title">Coverage</h1>
          <p className="content__subtitle">
            {data.total_divisions_used} of {data.total_divisions_available}{' '}
            CSI divisions populated · {data.total_rows} line items.
          </p>
        </div>
      </div>

      {data.empty_divisions.length > 0 && (
        <section className="coverage-empty">
          <div className="coverage-empty__title">
            <AlertTriangle size={14} /> Empty divisions ({data.empty_divisions.length})
          </div>
          <div className="coverage-empty__body">
            No line items were extracted for these divisions. Either the
            scope genuinely doesn't include them, or the AI missed scope
            on a related sheet.
          </div>
          <div className="coverage-chips">
            {data.empty_divisions.map((d) => (
              <span key={d} className="coverage-chip coverage-chip--empty">
                {d}
              </span>
            ))}
          </div>
        </section>
      )}

      <section className="data-table">
        <div className="data-table__header">
          <div className="data-table__title-block">
            <h3 className="data-table__title">All divisions</h3>
            <span className="data-table__count">
              {data.division_summary.length}
            </span>
          </div>
        </div>
        <div className="data-table__scroll">
          <table className="data-table__table">
            <thead>
              <tr>
                <th className="dt-col-status" />
                <th>Division</th>
                <th className="dt-col-num">Row count</th>
              </tr>
            </thead>
            <tbody>
              {data.division_summary.map((d) => (
                <tr key={d.division}>
                  <td className="dt-col-status">
                    {d.row_count > 0 ? (
                      <CheckCircle2
                        size={13}
                        style={{ color: '#34D399' }}
                      />
                    ) : (
                      <AlertTriangle
                        size={13}
                        style={{ color: '#FCA5A5' }}
                      />
                    )}
                  </td>
                  <td>{d.division}</td>
                  <td className="dt-col-num">{d.row_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {data.silent_skips.length > 0 && (
        <section className="coverage-empty">
          <div className="coverage-empty__title">
            <AlertTriangle size={14} /> Silent-skip sheets ({data.silent_skips.length})
          </div>
          <div className="coverage-empty__body">
            These sheets were classified as productive but produced no
            rows. Worth a manual review.
          </div>
          <div className="coverage-chips">
            {data.silent_skips.map((s) => (
              <span key={s} className="coverage-chip">
                {s}
              </span>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}
