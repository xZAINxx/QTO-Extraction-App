/* ──────────────────────────────────────────────────────────────────
 * Project + PDF state — zustand store.
 *
 * Owns:
 *   - the user's project list
 *   - the currently-active project id
 *   - the PDFs in the active project
 *   - new-project modal toggle (lives here so any component can open it
 *     without prop-drilling)
 *
 * Side effects all go through `apiFetch` which injects the Supabase
 * Bearer token in production and is a no-op in dev mode (matches the
 * backend's auth posture).
 * ────────────────────────────────────────────────────────────────── */

import { create } from 'zustand'
import { apiFetch } from '../hooks/useApi.js'

export const useProjectStore = create((set, get) => ({
  projects: [],
  activeProjectId: null,
  pdfs: [],
  loadingProjects: false,
  loadingPdfs: false,
  uploadingPdf: false,
  error: null,
  newProjectModalOpen: false,

  // ── Project list ─────────────────────────────────────────────

  async fetchProjects() {
    set({ loadingProjects: true, error: null })
    try {
      const projects = await apiFetch('/api/projects')
      // Auto-select the most-recent project when none is active.
      const { activeProjectId } = get()
      const nextActive =
        activeProjectId && projects.find((p) => p.id === activeProjectId)
          ? activeProjectId
          : (projects[0]?.id ?? null)
      set({ projects, activeProjectId: nextActive, loadingProjects: false })
      if (nextActive && nextActive !== activeProjectId) {
        get().fetchPdfs(nextActive).catch(() => {
          /* fetchPdfs sets its own error */
        })
      }
    } catch (err) {
      set({ error: err.message, loadingProjects: false })
    }
  },

  async createProject(name) {
    set({ error: null })
    try {
      const project = await apiFetch('/api/projects', {
        method: 'POST',
        body: { name },
      })
      set((s) => ({
        projects: [project, ...s.projects],
        activeProjectId: project.id,
        pdfs: [],
        newProjectModalOpen: false,
      }))
      return project
    } catch (err) {
      set({ error: err.message })
      throw err
    }
  },

  setActiveProject(id) {
    if (get().activeProjectId === id) return
    set({ activeProjectId: id, pdfs: [] })
    get().fetchPdfs(id).catch(() => {})
  },

  // ── PDFs in the active project ───────────────────────────────

  async fetchPdfs(projectId) {
    if (!projectId) return
    set({ loadingPdfs: true })
    try {
      const pdfs = await apiFetch(`/api/projects/${projectId}/pdfs`)
      set({ pdfs, loadingPdfs: false })
    } catch (err) {
      set({ error: err.message, loadingPdfs: false })
    }
  },

  async uploadPdf(file) {
    const projectId = get().activeProjectId
    if (!projectId) {
      throw new Error('No active project — create or pick one first.')
    }
    set({ uploadingPdf: true, error: null })
    try {
      const fd = new FormData()
      fd.append('file', file)
      const pdf = await apiFetch(`/api/projects/${projectId}/pdfs`, {
        method: 'POST',
        body: fd,
      })
      set((s) => ({ pdfs: [pdf, ...s.pdfs], uploadingPdf: false }))
      return pdf
    } catch (err) {
      set({ error: err.message, uploadingPdf: false })
      throw err
    }
  },

  // ── New-project modal ────────────────────────────────────────

  openNewProjectModal() {
    set({ newProjectModalOpen: true })
  },
  closeNewProjectModal() {
    set({ newProjectModalOpen: false })
  },
}))

export default useProjectStore
