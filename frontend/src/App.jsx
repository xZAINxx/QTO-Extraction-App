import { useEffect, useRef, useState } from 'react'
import {
  ChevronRight,
  Command,
  Sparkles,
  Search,
  LayoutGrid,
  GitCompareArrows,
  Gauge,
  Eye,
  FileText,
  FolderOpen,
  Upload,
  PaintBucket,
  Settings as SettingsIcon,
  CircleDot,
} from 'lucide-react'
import SignInGate from './auth/SignInGate.jsx'
import DataTable from './components/DataTable.jsx'
import { apiFetch, useApi } from './hooks/useApi.js'
import { useExtractionStream } from './hooks/useExtractionStream.js'
import ProjectSwitcher from './panels/ProjectSwitcher.jsx'
import UploadDropzone from './panels/UploadDropzone.jsx'
import useProjectStore from './stores/projectStore.js'
import './App.css'

/**
 * Workspace tabs shown in both the side nav and (later) the main tab strip.
 * The order mirrors the PyQt6 desktop app's tab order so the two surfaces
 * stay aligned in muscle memory.
 */
const WORKSPACES = [
  { id: 'takeoff',  label: 'Takeoff',      icon: LayoutGrid },
  { id: 'diff',     label: 'What Changed', icon: GitCompareArrows },
  { id: 'cockpit',  label: 'Cockpit',      icon: Gauge },
  { id: 'coverage', label: 'Coverage',     icon: Eye },
]

const PROJECT_NAV = [
  { id: 'dashboard',  label: 'Dashboard',  icon: FolderOpen },
  { id: 'extraction', label: 'Extraction', icon: Upload },
  { id: 'assemblies', label: 'Assemblies', icon: PaintBucket },
  { id: 'settings',   label: 'Settings',   icon: SettingsIcon },
]

export default function App() {
  const [info, setInfo] = useState(null)
  const [activeWorkspace, setActiveWorkspace] = useState('takeoff')
  const [error, setError] = useState(null)
  const { apiFetch } = useApi()

  const extractionId = useProjectStore((s) => s.extraction.extractionId)
  const applyExtractionEvent = useProjectStore((s) => s.applyExtractionEvent)
  // Tail the live SSE stream whenever an extraction is in flight.
  // The hook closes its EventSource on terminator events automatically.
  useExtractionStream(extractionId, applyExtractionEvent)

  useEffect(() => {
    let cancelled = false
    apiFetch('/api/info')
      .then((data) => {
        if (!cancelled) setInfo(data)
      })
      .catch((err) => {
        if (!cancelled) setError(err.message)
      })
    return () => {
      cancelled = true
    }
  }, [apiFetch])

  // Listen for the ModeBadge's "I just changed the mode" event so the
  // displayed badge text + the extraction-mode in the takeoff subtitle
  // both refresh without a full page reload.
  useEffect(() => {
    function onInfoUpdated(e) {
      if (e?.detail) setInfo(e.detail)
    }
    window.addEventListener('qto:info-updated', onInfoUpdated)
    return () => window.removeEventListener('qto:info-updated', onInfoUpdated)
  }, [])

  return (
    <SignInGate>
      <div className="app-shell">
        <Topbar info={info} />
        <div className="app-body">
          <SideNav
            activeWorkspace={activeWorkspace}
            onSelect={setActiveWorkspace}
          />
          <main className="workspace">
            <WorkspaceContent
              workspace={activeWorkspace}
              info={info}
              error={error}
            />
          </main>
        </div>
        <StatusStrip info={info} error={error} />
      </div>
    </SignInGate>
  )
}

// ────────────────────────────────────────────────────────────────────
// Topbar
// ────────────────────────────────────────────────────────────────────

function Topbar({ info }) {
  const mode = info?.extraction_mode ?? '—'
  return (
    <header className="topbar">
      <div className="topbar__brand">
        <div className="topbar__logo" aria-hidden>
          Z
        </div>
        <div className="topbar__breadcrumb">
          <span className="topbar__product">Zeconic QTO</span>
          <ChevronRight className="topbar__chevron" size={14} />
          <ProjectSwitcher />
          <ChevronRight className="topbar__chevron" size={14} />
          <span className="topbar__crumb topbar__crumb--active">Takeoff</span>
        </div>
      </div>

      <div className="topbar__center">
        <button className="search-trigger" type="button">
          <Search size={14} />
          <span>Search projects, sheets, line items…</span>
          <kbd>
            <Command size={11} /> K
          </kbd>
        </button>
      </div>

      <div className="topbar__actions">
        <ModeBadge mode={mode} />
        <button className="btn btn--amber" type="button">
          <Sparkles size={14} />
          <span>Ask QTO AI</span>
        </button>
        <div className="avatar" title="Zain">
          Z
        </div>
      </div>
    </header>
  )
}

const _EXTRACTION_MODES = [
  {
    key: 'hybrid',
    label: 'Hybrid (Claude)',
    description:
      'Claude routes everything. Most accurate; highest token spend.',
  },
  {
    key: 'multi_agent',
    label: 'Multi-Agent (NVIDIA + Claude)',
    description:
      'NVIDIA NIM agents extract; Claude reviews low-confidence rows.',
  },
  {
    key: 'claude_only',
    label: 'Claude Only (legacy)',
    description: 'Pure Claude pipeline; bisect tool only.',
  },
]


function ModeBadge({ mode }) {
  const [open, setOpen] = useState(false)
  const [pending, setPending] = useState(null)
  const setExtractionMode = useProjectStore.getState
  const ref = useRef(null)
  const display = ((pending ?? mode) ?? '').replace('_', ' ').toUpperCase() || '—'

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

  const onPick = async (newMode) => {
    setOpen(false)
    setPending(newMode)
    try {
      await apiFetch('/api/me/extraction-mode', {
        method: 'POST',
        body: { extraction_mode: newMode },
      })
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error('mode update failed', err)
    } finally {
      setPending(null)
      // Refetch /api/info so the parent App's `info.extraction_mode`
      // reflects the new value (we read from there elsewhere).
      try {
        const data = await apiFetch('/api/info')
        // best-effort propagate via a custom event the parent can hear
        window.dispatchEvent(new CustomEvent('qto:info-updated', { detail: data }))
      } catch {
        /* ignore */
      }
    }
  }

  return (
    <div className="mode-badge-wrap" ref={ref}>
      <button
        className="mode-badge mode-badge--clickable"
        type="button"
        title="Click to switch extraction mode"
        onClick={() => setOpen((p) => !p)}
      >
        <CircleDot size={10} className="mode-badge__dot" />
        <span>{display}</span>
      </button>
      {open && (
        <div className="mode-badge__menu" role="menu">
          {_EXTRACTION_MODES.map((m) => (
            <button
              key={m.key}
              type="button"
              role="menuitem"
              className={
                'mode-badge__item' +
                (m.key === mode ? ' mode-badge__item--active' : '')
              }
              onClick={() => onPick(m.key)}
            >
              <div className="mode-badge__item-label">{m.label}</div>
              <div className="mode-badge__item-desc">{m.description}</div>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ────────────────────────────────────────────────────────────────────
// Side nav
// ────────────────────────────────────────────────────────────────────

function SideNav({ activeWorkspace, onSelect }) {
  const pdfs = useProjectStore((s) => s.pdfs)
  const extraction = useProjectStore((s) => s.extraction)
  const startExtraction = useProjectStore((s) => s.startExtraction)
  const cancelExtraction = useProjectStore((s) => s.cancelExtraction)
  const isRunning =
    extraction.status === 'starting' ||
    extraction.status === 'running' ||
    extraction.status === 'pending'
  const canRun = pdfs.length > 0 && !isRunning

  return (
    <aside className="sidenav">
      {isRunning ? (
        <button
          className="btn btn--ghost btn--full"
          type="button"
          onClick={() => cancelExtraction().catch(() => {})}
        >
          <Upload size={14} />
          <span>Cancel ({extraction.page}/{extraction.total || '…'})</span>
        </button>
      ) : (
        <button
          className="btn btn--emerald btn--full"
          type="button"
          onClick={() => startExtraction().catch(() => {})}
          disabled={!canRun}
          title={
            pdfs.length === 0
              ? 'Upload a PDF first'
              : 'Run extraction on the most-recent PDF'
          }
        >
          <Upload size={14} />
          <span>Run Extraction</span>
        </button>
      )}

      <nav className="sidenav__group">
        <div className="sidenav__label">Workspace</div>
        {WORKSPACES.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            type="button"
            className={
              'sidenav__item' +
              (activeWorkspace === id ? ' sidenav__item--active' : '')
            }
            onClick={() => onSelect(id)}
          >
            <Icon size={15} />
            <span>{label}</span>
          </button>
        ))}
      </nav>

      <nav className="sidenav__group">
        <div className="sidenav__label">Project</div>
        {PROJECT_NAV.map(({ id, label, icon: Icon }) => (
          <button key={id} type="button" className="sidenav__item">
            <Icon size={15} />
            <span>{label}</span>
          </button>
        ))}
      </nav>
    </aside>
  )
}

// ────────────────────────────────────────────────────────────────────
// Workspace content
// ────────────────────────────────────────────────────────────────────

function WorkspaceContent({ workspace, info, error }) {
  if (error) return <ErrorState message={error} />
  if (workspace === 'takeoff') return <TakeoffWorkspace info={info} />
  return <ComingSoonCard workspace={workspace} />
}

function TakeoffWorkspace({ info }) {
  const ready = info != null
  const pdfs = useProjectStore((s) => s.pdfs)
  const activeProjectId = useProjectStore((s) => s.activeProjectId)
  const projects = useProjectStore((s) => s.projects)
  const activeProject = projects.find((p) => p.id === activeProjectId)

  return (
    <div className="content">
      <div className="content__header">
        <div>
          <h1 className="content__title">Takeoff</h1>
          <p className="content__subtitle">
            {activeProject
              ? `Project ${activeProject.name} · upload a drawing set to extract line items.`
              : 'Pick a project from the topbar (or create one), then drop a PDF to extract line items.'}
            {' '}The current extraction mode is{' '}
            <code className="inline-code">{info?.extraction_mode ?? '—'}</code>.
          </p>
        </div>
        <div className="content__filter-bar">
          <Chip label="All Trades" />
          <Chip label="All Sheets" />
          <Chip label="Needs Review" />
        </div>
      </div>

      <UploadDropzone />

      <ExtractionStatusBanner />

      <DataTable extractionId={useProjectStore((s) => s.extraction.extractionId)} />

      {pdfs.length > 0 && (
        <section className="pdf-list">
          <div className="pdf-list__header">
            <h3 className="pdf-list__title">Uploaded PDFs</h3>
            <span className="pdf-list__count">{pdfs.length}</span>
          </div>
          <div className="pdf-list__rows">
            {pdfs.map((p) => (
              <div key={p.id} className="pdf-list__row">
                <FileText size={14} className="pdf-list__icon" />
                <div className="pdf-list__name">{p.filename}</div>
                <div className="pdf-list__meta">
                  {p.page_count ? `${p.page_count} pp` : '—'} ·{' '}
                  {(p.byte_size / 1024 / 1024).toFixed(1)} MB
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      <section className="status-card">
        <div className="status-card__row">
          <span className="status-card__label">Backend</span>
          <Pill
            tone={ready ? 'success' : 'warning'}
            label={ready ? 'Connected · /api/info OK' : 'Connecting…'}
          />
        </div>
        <div className="status-card__row">
          <span className="status-card__label">Anthropic key</span>
          <Pill
            tone={info?.has_anthropic_key ? 'success' : 'danger'}
            label={info?.has_anthropic_key ? 'Detected in .env' : 'Missing'}
          />
        </div>
        <div className="status-card__row">
          <span className="status-card__label">NVIDIA NIM key</span>
          <Pill
            tone={info?.has_nvidia_key ? 'success' : 'danger'}
            label={info?.has_nvidia_key ? 'Detected in .env' : 'Missing'}
          />
        </div>
        <div className="status-card__row">
          <span className="status-card__label">UI version</span>
          <Pill
            tone="info"
            label={info?.ui_v2 ? 'ui_v2 = true' : 'ui_v2 = false'}
          />
        </div>
      </section>
    </div>
  )
}

function ComingSoonCard({ workspace }) {
  const meta = WORKSPACES.find((w) => w.id === workspace)
  const Icon = meta?.icon ?? LayoutGrid
  return (
    <div className="content">
      <div className="content__header">
        <div>
          <h1 className="content__title">{meta?.label ?? workspace}</h1>
          <p className="content__subtitle">
            Coming next. The PyQt6 desktop app already ships this workspace —
            the web port lands once the Takeoff surface is locked in.
          </p>
        </div>
      </div>
      <section className="empty-card empty-card--soft">
        <div className="empty-card__icon" aria-hidden>
          <Icon size={20} />
        </div>
        <h2 className="empty-card__title">{meta?.label} (web port pending)</h2>
        <p className="empty-card__body">
          For now, switch to the desktop app to use this view. The CLI
          equivalent is documented in <code className="inline-code">README.md</code>.
        </p>
      </section>
    </div>
  )
}

function ErrorState({ message }) {
  return (
    <div className="content">
      <div className="content__header">
        <div>
          <h1 className="content__title">Backend unreachable</h1>
          <p className="content__subtitle">
            The frontend can't reach the FastAPI backend at{' '}
            <code className="inline-code">/api/info</code>. Make sure
            uvicorn is running (it normally launches with{' '}
            <code className="inline-code">npm run dev</code>).
          </p>
        </div>
      </div>
      <section className="empty-card empty-card--danger">
        <h2 className="empty-card__title">Error</h2>
        <p className="empty-card__body">
          <code className="inline-code">{message}</code>
        </p>
      </section>
    </div>
  )
}

// ────────────────────────────────────────────────────────────────────
// Status strip
// ────────────────────────────────────────────────────────────────────

function StatusStrip({ info, error }) {
  const online = !!info && !error
  const extraction = useProjectStore((s) => s.extraction)
  const pdfs = useProjectStore((s) => s.pdfs)
  return (
    <footer className="status-strip">
      <span className={'status-dot' + (online ? ' status-dot--ok' : ' status-dot--off')} />
      <span className="status-strip__label">
        {online ? 'Online' : error ? 'Offline' : 'Connecting…'}
      </span>
      <Bullet />
      <span>{extraction.rowCount} line items</span>
      <Bullet />
      <span>{pdfs.length} {pdfs.length === 1 ? 'PDF' : 'PDFs'} loaded</span>
      {extraction.status === 'running' && (
        <>
          <Bullet />
          <span>
            page {extraction.page}/{extraction.total || '…'} ·{' '}
            {extraction.pageType ?? '…'}
          </span>
        </>
      )}
      {extraction.cost.cost_usd > 0 && (
        <>
          <Bullet />
          <span>${extraction.cost.cost_usd.toFixed(4)}</span>
        </>
      )}
      <span className="status-strip__muted">
        {extraction.status === 'running'
          ? 'extraction running…'
          : 'idle'}
      </span>
    </footer>
  )
}

function ExtractionStatusBanner() {
  const extraction = useProjectStore((s) => s.extraction)
  if (extraction.status === 'idle') return null

  const tone =
    extraction.status === 'failed'
      ? 'danger'
      : extraction.status === 'completed'
      ? 'success'
      : extraction.status === 'canceled'
      ? 'warning'
      : 'info'

  let title
  let body
  switch (extraction.status) {
    case 'starting':
      title = 'Starting extraction…'
      body = 'Resolving PDF, picking the model pipeline.'
      break
    case 'pending':
    case 'running':
      title = `Extracting · page ${extraction.page}/${extraction.total || '…'}`
      body = extraction.pageType
        ? `Current page type: ${extraction.pageType.replace('_', ' ')}.`
        : 'Pipeline is working through your drawing set.'
      break
    case 'completed':
      title = 'Extraction complete'
      body = `${extraction.rowCount} line items extracted ·`
        + ` $${extraction.cost.cost_usd.toFixed(4)}`
        + ` · ${extraction.cost.api_calls} API calls.`
      break
    case 'failed':
      title = 'Extraction failed'
      body = extraction.error ?? 'Unknown error.'
      break
    case 'canceled':
      title = 'Extraction canceled'
      body = `Stopped at page ${extraction.page}/${extraction.total || '?'}.`
      break
    default:
      return null
  }

  return (
    <div className={`extraction-banner extraction-banner--${tone}`}>
      <div className="extraction-banner__title">{title}</div>
      <div className="extraction-banner__body">{body}</div>
    </div>
  )
}

function Bullet() {
  return <span className="status-strip__bullet">•</span>
}

// ────────────────────────────────────────────────────────────────────
// Tiny bits
// ────────────────────────────────────────────────────────────────────

function Pill({ tone = 'info', label }) {
  return <span className={`pill pill--${tone}`}>{label}</span>
}

function Chip({ label }) {
  return (
    <button type="button" className="chip">
      {label}
    </button>
  )
}
