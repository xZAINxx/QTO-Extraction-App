/* ──────────────────────────────────────────────────────────────────
 * CostPopover — gear-icon-triggered popover. Shows live cost +
 * per-model breakdown for the active extraction, plus a cost-saver
 * toggle that PATCHes /api/me/cost-saver.
 *
 * The PyQt6 cost meter (ui/cost_meter.py) showed dollars / tokens /
 * hit-rate / per-model chunks in the bottom strip. We agreed that
 * was too noisy for the new UI — these numbers belong behind a
 * settings popover, not in the always-visible status bar.
 * ────────────────────────────────────────────────────────────────── */

import { useEffect, useRef, useState } from 'react'
import { Settings, X } from 'lucide-react'
import { apiFetch } from '../hooks/useApi.js'
import useProjectStore from '../stores/projectStore.js'

export default function CostPopover() {
  const extractionId = useProjectStore((s) => s.extraction.extractionId)
  const liveCost = useProjectStore((s) => s.extraction.cost)

  const [open, setOpen] = useState(false)
  const [costSnapshot, setCostSnapshot] = useState(null)
  const [costSaver, setCostSaver] = useState(null)
  const ref = useRef(null)

  // Click-outside / Esc.
  useEffect(() => {
    if (!open) return undefined
    function onClick(e) {
      if (ref.current?.contains(e.target)) return
      setOpen(false)
    }
    function onKey(e) {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  // Pull DB snapshot when popover opens — covers the case where the
  // user disconnected during the SSE run; live deltas fold on top.
  useEffect(() => {
    if (!open || !extractionId) return
    apiFetch(`/api/extractions/${extractionId}/cost`)
      .then(setCostSnapshot)
      .catch(() => {})
  }, [open, extractionId])

  // Pull initial cost-saver value on first open.
  useEffect(() => {
    if (!open) return
    apiFetch('/api/me')
      .then((u) => setCostSaver(u.cost_saver_mode))
      .catch(() => {})
  }, [open])

  const onToggleSaver = async () => {
    const next = !costSaver
    setCostSaver(next)
    try {
      await apiFetch('/api/me/cost-saver', {
        method: 'POST',
        body: { cost_saver_mode: next },
      })
    } catch {
      setCostSaver((v) => !v)  // revert on failure
    }
  }

  // Merge: snapshot (DB) is the floor; live deltas (SSE) are the truth.
  const total = liveCost.cost_usd || costSnapshot?.total_cost_usd || 0
  const tokens = liveCost.total_tokens || costSnapshot?.total_tokens || 0
  const calls = liveCost.api_calls || costSnapshot?.total_api_calls || 0
  const byModel =
    Object.keys(liveCost.by_model || {}).length > 0
      ? liveCost.by_model
      : costSnapshot?.by_model || {}

  return (
    <div className="cost-popover-wrap" ref={ref}>
      <button
        type="button"
        className="topbar-icon-btn"
        title="Cost & settings"
        onClick={() => setOpen((p) => !p)}
      >
        <Settings size={14} />
      </button>
      {open && (
        <div className="cost-popover">
          <div className="cost-popover__header">
            <h3 className="cost-popover__title">Cost & spend</h3>
            <button
              className="cost-popover__close"
              onClick={() => setOpen(false)}
              aria-label="Close"
            >
              <X size={14} />
            </button>
          </div>

          {!extractionId ? (
            <div className="cost-popover__empty">
              No extraction running. Spend resets per-extraction; aggregate
              billing lands in a future workstream.
            </div>
          ) : (
            <>
              <div className="cost-popover__metrics">
                <div className="cost-popover__metric">
                  <div className="cost-popover__metric-value">
                    ${total.toFixed(4)}
                  </div>
                  <div className="cost-popover__metric-label">spend</div>
                </div>
                <div className="cost-popover__metric">
                  <div className="cost-popover__metric-value">
                    {(tokens / 1000).toFixed(1)}k
                  </div>
                  <div className="cost-popover__metric-label">tokens</div>
                </div>
                <div className="cost-popover__metric">
                  <div className="cost-popover__metric-value">{calls}</div>
                  <div className="cost-popover__metric-label">calls</div>
                </div>
              </div>

              {Object.keys(byModel).length > 0 && (
                <div className="cost-popover__by-model">
                  <div className="cost-popover__section-label">Per model</div>
                  {Object.entries(byModel).map(([model, m]) => (
                    <div key={model} className="cost-popover__row">
                      <span className="cost-popover__model">{shortenModel(model)}</span>
                      <span className="cost-popover__row-meta">
                        {m.api_calls} calls · ${(m.cost_usd ?? 0).toFixed(4)}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}

          <div className="cost-popover__divider" />
          <div className="cost-popover__toggle-row">
            <div>
              <div className="cost-popover__toggle-label">Cost-saver mode</div>
              <div className="cost-popover__toggle-help">
                Routes 24-hour-tolerant compose calls through Anthropic's
                batch API for ~50% off. Slower turnaround.
              </div>
            </div>
            <Toggle checked={costSaver === true} onChange={onToggleSaver} />
          </div>
        </div>
      )}
    </div>
  )
}


function Toggle({ checked, onChange }) {
  return (
    <button
      role="switch"
      aria-checked={checked ? 'true' : 'false'}
      type="button"
      onClick={onChange}
      className={'toggle' + (checked ? ' toggle--on' : '')}
    >
      <span className="toggle__thumb" />
    </button>
  )
}


function shortenModel(model) {
  if (model.includes('haiku')) return 'Haiku'
  if (model.includes('sonnet')) return 'Sonnet'
  if (model.includes('opus')) return 'Opus'
  if (model.includes('nemotron-mini')) return 'Nemotron Mini'
  if (model.includes('mistral-nemotron')) return 'Mistral Nemotron'
  if (model.includes('maverick')) return 'Llama Maverick'
  if (model.includes('nv-embed')) return 'NV Embed'
  if (model.includes('rerank')) return 'NV Rerank'
  return model.slice(0, 24)
}
