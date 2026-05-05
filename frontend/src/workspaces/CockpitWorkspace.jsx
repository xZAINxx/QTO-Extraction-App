/* ──────────────────────────────────────────────────────────────────
 * CockpitWorkspace — bid-day surface ported from
 * ui/workspaces/cockpit_workspace.py:117-379.
 *
 * Three sections:
 *   1. Hero total — base $ + marked-up $ + deadline countdown
 *   2. Markup sliders (overhead / profit / contingency, additive)
 *   3. Division breakdown bar chart (recharts)
 *   4. Sub-bid table (top 50 by line-item total)
 * ────────────────────────────────────────────────────────────────── */

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { Clock } from 'lucide-react'
import { apiFetch } from '../hooks/useApi.js'
import useProjectStore from '../stores/projectStore.js'

export default function CockpitWorkspace() {
  const extractionId = useProjectStore((s) => s.extraction.extractionId)
  const projects = useProjectStore((s) => s.projects)
  const activeProjectId = useProjectStore((s) => s.activeProjectId)
  const activeProject = projects.find((p) => p.id === activeProjectId)
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  // Local mirror of the markup so sliders feel instant; persisted to
  // /api/projects/{id} on slider release (debounced 500ms).
  const [overhead, setOverhead] = useState(activeProject?.markup_overhead ?? 10)
  const [profit, setProfit] = useState(activeProject?.markup_profit ?? 8)
  const [contingency, setContingency] = useState(
    activeProject?.markup_contingency ?? 5,
  )

  useEffect(() => {
    if (activeProject) {
      setOverhead(activeProject.markup_overhead ?? 10)
      setProfit(activeProject.markup_profit ?? 8)
      setContingency(activeProject.markup_contingency ?? 5)
    }
  }, [activeProject])

  useEffect(() => {
    if (!extractionId) return
    apiFetch(`/api/extractions/${extractionId}/cockpit`)
      .then(setData)
      .catch((e) => setError(e.message))
  }, [extractionId])

  // Debounced PATCH whenever the local markup state stabilises.
  useEffect(() => {
    if (!activeProjectId) return
    const t = setTimeout(() => {
      apiFetch(`/api/projects/${activeProjectId}`, {
        method: 'PATCH',
        body: {
          markup_overhead: overhead,
          markup_profit: profit,
          markup_contingency: contingency,
        },
      }).catch(() => {})
    }, 500)
    return () => clearTimeout(t)
  }, [overhead, profit, contingency, activeProjectId])

  const markedUp = useMemo(() => {
    if (!data) return 0
    return data.base_total * (1 + (overhead + profit + contingency) / 100)
  }, [data, overhead, profit, contingency])

  if (!extractionId) {
    return (
      <div className="content">
        <div className="content__header">
          <div>
            <h1 className="content__title">Cockpit</h1>
            <p className="content__subtitle">
              Run an extraction to see the cockpit fill in with totals,
              markup math, and division breakdown.
            </p>
          </div>
        </div>
        <section className="empty-card empty-card--soft">
          <h2 className="empty-card__title">Waiting for an extraction</h2>
          <p className="empty-card__body">
            Upload a PDF on the Takeoff tab and click Run Extraction.
            Cockpit data refreshes once the run completes.
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
            <h1 className="content__title">Cockpit</h1>
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
            <h1 className="content__title">Cockpit</h1>
            <p className="content__subtitle">Loading cockpit data…</p>
          </div>
        </div>
      </div>
    )
  }

  const deadlineLabel = describeDeadline(data.deadline)

  return (
    <div className="content">
      <div className="content__header">
        <div>
          <h1 className="content__title">Cockpit</h1>
          <p className="content__subtitle">
            {data.project_name} · {data.row_count} line items.
          </p>
        </div>
      </div>

      <section className="cockpit-total-card">
        <div className="cockpit-total-card__total">
          ${formatBig(data.base_total)}
        </div>
        <div className="cockpit-total-card__markup">
          marked up: ${formatBig(markedUp)}
          {' '}
          (+{(overhead + profit + contingency).toFixed(1)}%)
        </div>
        {deadlineLabel && (
          <div
            className={
              'cockpit-total-card__deadline cockpit-total-card__deadline--' +
              deadlineLabel.tone
            }
          >
            <Clock size={12} />
            <span>{deadlineLabel.text}</span>
          </div>
        )}
      </section>

      <section className="markup-controls">
        <MarkupSlider
          label="Overhead"
          value={overhead}
          onChange={setOverhead}
          dollarImpact={(data.base_total * overhead) / 100}
        />
        <MarkupSlider
          label="Profit"
          value={profit}
          onChange={setProfit}
          dollarImpact={(data.base_total * profit) / 100}
        />
        <MarkupSlider
          label="Contingency"
          value={contingency}
          onChange={setContingency}
          dollarImpact={(data.base_total * contingency) / 100}
        />
      </section>

      <section className="cockpit-divisions">
        <div className="data-table__header">
          <div className="data-table__title-block">
            <h3 className="data-table__title">Division breakdown</h3>
            <span className="data-table__count">
              {data.by_division.length} divisions
            </span>
          </div>
        </div>
        <div style={{ width: '100%', height: 240, padding: '12px 16px' }}>
          <ResponsiveContainer>
            <BarChart data={data.by_division} layout="vertical" margin={{ left: 80 }}>
              <CartesianGrid stroke="rgba(148,163,184,0.10)" horizontal={false} />
              <XAxis
                type="number"
                tick={{ fill: '#94A3B8', fontSize: 11 }}
                tickFormatter={(v) => `$${formatBig(v)}`}
              />
              <YAxis
                type="category"
                dataKey="division"
                tick={{ fill: '#94A3B8', fontSize: 11 }}
                width={90}
              />
              <Tooltip
                cursor={{ fill: 'rgba(22,163,74,0.10)' }}
                contentStyle={{
                  background: '#1B1F26',
                  border: '1px solid rgba(148,163,184,0.18)',
                  borderRadius: 8,
                  fontSize: 12,
                }}
                formatter={(v) => [`$${formatBig(v)}`, 'Subtotal']}
              />
              <Bar dataKey="subtotal" fill="#16A34A" radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </section>

      <section className="data-table">
        <div className="data-table__header">
          <div className="data-table__title-block">
            <h3 className="data-table__title">Sub-bid (top 50 by total)</h3>
            <span className="data-table__count">
              {data.sub_bid_truncated
                ? `top 50 of ${data.sub_bid_total_count}`
                : `${data.sub_bid_total_count} rows`}
            </span>
          </div>
        </div>
        <div className="data-table__scroll">
          <table className="data-table__table">
            <thead>
              <tr>
                <th>Description</th>
                <th className="dt-col-num">Qty</th>
                <th>Units</th>
                <th className="dt-col-num">$/unit</th>
                <th className="dt-col-num">Total</th>
              </tr>
            </thead>
            <tbody>
              {data.sub_bid.map((r, i) => (
                <tr key={i}>
                  <td className="dt-col-desc">{r.description || '—'}</td>
                  <td className="dt-col-num">{formatNum(r.qty)}</td>
                  <td>{r.units || '—'}</td>
                  <td className="dt-col-num">${r.unit_price.toFixed(2)}</td>
                  <td className="dt-col-num">${formatBig(r.total)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}


// ──────────────────────────────────────────────────────────────────


function MarkupSlider({ label, value, onChange, dollarImpact }) {
  return (
    <div className="markup-control">
      <div className="markup-control__row">
        <span className="markup-control__label">{label}</span>
        <span className="markup-control__value">
          {value.toFixed(1)}%
          {dollarImpact > 0 && (
            <span className="markup-control__dollar">
              {' '}· +${formatBig(dollarImpact)}
            </span>
          )}
        </span>
      </div>
      <input
        type="range"
        min={0}
        max={30}
        step={0.5}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="markup-control__slider"
      />
    </div>
  )
}


function describeDeadline(iso) {
  if (!iso) return null
  const due = new Date(iso)
  if (Number.isNaN(due.getTime())) return null
  const now = new Date()
  const ms = due - now
  const days = Math.round(ms / (1000 * 60 * 60 * 24))
  if (days > 1) return { text: `Due in ${days} days`, tone: 'info' }
  if (days === 1) return { text: 'Due tomorrow', tone: 'info' }
  if (days === 0) return { text: 'Due today', tone: 'warning' }
  return { text: `Overdue by ${Math.abs(days)} days`, tone: 'danger' }
}


function formatBig(value) {
  if (value == null || Number.isNaN(value)) return '0.00'
  return Number(value).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}


function formatNum(value) {
  if (value == null) return '—'
  return Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 })
}
