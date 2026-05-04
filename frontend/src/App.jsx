import { useEffect, useState } from 'react'
import {
  ChevronRight,
  Command,
  Sparkles,
  Search,
  LayoutGrid,
  GitCompareArrows,
  Gauge,
  Eye,
  FolderOpen,
  Upload,
  Wrench,
  PaintBucket,
  Settings as SettingsIcon,
  CircleDot,
} from 'lucide-react'
import SignInGate from './auth/SignInGate.jsx'
import { useApi } from './hooks/useApi.js'
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
          <span className="topbar__crumb">Untitled Project</span>
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

function ModeBadge({ mode }) {
  const display = (mode ?? '').replace('_', ' ').toUpperCase() || '—'
  return (
    <button className="mode-badge" type="button" title="Extraction mode">
      <CircleDot size={10} className="mode-badge__dot" />
      <span>{display}</span>
    </button>
  )
}

// ────────────────────────────────────────────────────────────────────
// Side nav
// ────────────────────────────────────────────────────────────────────

function SideNav({ activeWorkspace, onSelect }) {
  return (
    <aside className="sidenav">
      <button className="btn btn--emerald btn--full" type="button">
        <Upload size={14} />
        <span>Run Extraction</span>
      </button>

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
  return (
    <div className="content">
      <div className="content__header">
        <div>
          <h1 className="content__title">Takeoff</h1>
          <p className="content__subtitle">
            Upload a drawing set to extract Quantity Takeoff line items.
            The current extraction mode is{' '}
            <code className="inline-code">{info?.extraction_mode ?? '—'}</code>.
          </p>
        </div>
        <div className="content__filter-bar">
          <Chip label="All Trades" />
          <Chip label="All Sheets" />
          <Chip label="Needs Review" />
        </div>
      </div>

      <section className="empty-card">
        <div className="empty-card__icon" aria-hidden>
          <Upload size={20} />
        </div>
        <h2 className="empty-card__title">No takeoff yet</h2>
        <p className="empty-card__body">
          Drop a PDF anywhere on this window, or click below to start a new
          extraction. Your drawings stay on this machine — no cloud uploads.
        </p>
        <div className="empty-card__actions">
          <button className="btn btn--emerald" type="button">
            <Upload size={14} />
            <span>Upload PDF</span>
          </button>
          <button className="btn btn--ghost" type="button">
            <Wrench size={14} />
            <span>Open recent</span>
          </button>
        </div>
      </section>

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
  return (
    <footer className="status-strip">
      <span className={'status-dot' + (online ? ' status-dot--ok' : ' status-dot--off')} />
      <span className="status-strip__label">
        {online ? 'Online' : error ? 'Offline' : 'Connecting…'}
      </span>
      <Bullet />
      <span>0 line items</span>
      <Bullet />
      <span>0 sheets loaded</span>
      <Bullet />
      <span className="status-strip__muted">
        cost & token meters live in the desktop app for now
      </span>
    </footer>
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
