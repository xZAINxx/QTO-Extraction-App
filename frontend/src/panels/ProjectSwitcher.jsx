/* ──────────────────────────────────────────────────────────────────
 * ProjectSwitcher — replaces the static "Untitled Project ▾" button
 * in the topbar. Click → dropdown of projects + "+ New project".
 *
 * The new-project modal lives in the zustand store so other surfaces
 * (TakeoffWorkspace's empty-state CTA) can open it without a prop
 * pipe through three layers.
 * ────────────────────────────────────────────────────────────────── */

import { useEffect, useRef, useState } from 'react'
import { Check, ChevronDown, FolderOpen, FolderPlus, X } from 'lucide-react'
import useProjectStore from '../stores/projectStore.js'

export default function ProjectSwitcher() {
  const projects = useProjectStore((s) => s.projects)
  const activeProjectId = useProjectStore((s) => s.activeProjectId)
  const fetchProjects = useProjectStore((s) => s.fetchProjects)
  const setActiveProject = useProjectStore((s) => s.setActiveProject)
  const openNewProjectModal = useProjectStore((s) => s.openNewProjectModal)

  const [open, setOpen] = useState(false)
  const buttonRef = useRef(null)
  const menuRef = useRef(null)

  // Initial load.
  useEffect(() => {
    fetchProjects().catch(() => {})
  }, [fetchProjects])

  // Click-outside / Esc to close.
  useEffect(() => {
    if (!open) return
    function onClick(e) {
      if (
        buttonRef.current?.contains(e.target) ||
        menuRef.current?.contains(e.target)
      ) {
        return
      }
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

  const active = projects.find((p) => p.id === activeProjectId)
  const label = active?.name ?? 'No project'

  return (
    <div className="project-switcher">
      <button
        ref={buttonRef}
        type="button"
        className="project-switcher__button"
        onClick={() => setOpen((prev) => !prev)}
      >
        <FolderOpen size={14} className="project-switcher__icon" />
        <span className="project-switcher__label">{label}</span>
        <ChevronDown size={12} className="project-switcher__chevron" />
      </button>

      {open && (
        <div ref={menuRef} className="project-switcher__menu" role="menu">
          {projects.length === 0 && (
            <div className="project-switcher__empty">
              No projects yet. Create your first one.
            </div>
          )}
          {projects.map((p) => (
            <button
              key={p.id}
              type="button"
              role="menuitem"
              className={
                'project-switcher__item' +
                (p.id === activeProjectId
                  ? ' project-switcher__item--active'
                  : '')
              }
              onClick={() => {
                setActiveProject(p.id)
                setOpen(false)
              }}
            >
              <span className="project-switcher__item-name">{p.name}</span>
              {p.id === activeProjectId && (
                <Check
                  size={13}
                  className="project-switcher__item-check"
                />
              )}
            </button>
          ))}
          <div className="project-switcher__divider" />
          <button
            type="button"
            role="menuitem"
            className="project-switcher__new"
            onClick={() => {
              openNewProjectModal()
              setOpen(false)
            }}
          >
            <FolderPlus size={13} />
            <span>New project…</span>
          </button>
        </div>
      )}

      <NewProjectModal />
    </div>
  )
}

// ──────────────────────────────────────────────────────────────────
// New-project modal — owned by the switcher because it shares state.
// ──────────────────────────────────────────────────────────────────

function NewProjectModal() {
  const open = useProjectStore((s) => s.newProjectModalOpen)
  const closeModal = useProjectStore((s) => s.closeNewProjectModal)
  const createProject = useProjectStore((s) => s.createProject)

  const [name, setName] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)

  // Reset on open.
  useEffect(() => {
    if (open) {
      setName('')
      setError(null)
      setSubmitting(false)
    }
  }, [open])

  // Esc closes.
  useEffect(() => {
    if (!open) return
    function onKey(e) {
      if (e.key === 'Escape') closeModal()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, closeModal])

  if (!open) return null

  async function onSubmit(event) {
    event.preventDefault()
    if (!name.trim()) return
    setSubmitting(true)
    setError(null)
    try {
      await createProject(name.trim())
      // store closes the modal on success
    } catch (err) {
      setError(err.message)
      setSubmitting(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={closeModal}>
      <form
        className="modal-card"
        onClick={(e) => e.stopPropagation()}
        onSubmit={onSubmit}
      >
        <div className="modal-card__header">
          <h2 className="modal-card__title">New project</h2>
          <button
            type="button"
            className="modal-card__close"
            onClick={closeModal}
            aria-label="Close"
          >
            <X size={16} />
          </button>
        </div>
        <label className="modal-card__field">
          <span className="modal-card__label">Project name</span>
          <input
            autoFocus
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Cooper Basket Building"
            maxLength={200}
          />
        </label>
        {error && <div className="modal-card__error">{error}</div>}
        <div className="modal-card__actions">
          <button
            type="button"
            className="btn btn--ghost"
            onClick={closeModal}
            disabled={submitting}
          >
            Cancel
          </button>
          <button
            type="submit"
            className="btn btn--emerald"
            disabled={submitting || !name.trim()}
          >
            {submitting ? 'Creating…' : 'Create project'}
          </button>
        </div>
      </form>
    </div>
  )
}
