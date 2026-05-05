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

  // ── Extraction job ───────────────────────────────────────────

  /**
   * Active extraction state. Populated by ``startExtraction`` and updated
   * by the SSE stream consumer (see ``useExtractionStream``).
   *   - extractionId: UUID of the running job (null when idle)
   *   - status: snapshot from the latest event
   *   - phase / page / total / pageType: progress fields
   *   - cost: live $ + token + per-model breakdown
   *   - rowCount: how many rows we've seen so far
   *   - error: terminal failure message (null otherwise)
   */
  extraction: {
    extractionId: null,
    status: 'idle',
    phase: null,
    page: 0,
    total: 0,
    pageType: null,
    cost: { cost_usd: 0, total_tokens: 0, api_calls: 0, by_model: {} },
    rowCount: 0,
    error: null,
  },

  async startExtraction({ pdfId, mode } = {}) {
    let targetPdfId = pdfId
    if (!targetPdfId) {
      const [latest] = get().pdfs
      if (!latest) {
        throw new Error('Upload a PDF first.')
      }
      targetPdfId = latest.id
    }
    set({
      extraction: {
        extractionId: null,
        status: 'starting',
        phase: 'starting',
        page: 0,
        total: 0,
        pageType: null,
        cost: { cost_usd: 0, total_tokens: 0, api_calls: 0, by_model: {} },
        rowCount: 0,
        error: null,
      },
    })
    try {
      const body = mode ? { pdf_id: targetPdfId, extraction_mode: mode }
                        : { pdf_id: targetPdfId }
      const ext = await apiFetch('/api/extractions', {
        method: 'POST',
        body,
      })
      set((s) => ({
        extraction: {
          ...s.extraction,
          extractionId: ext.id,
          status: ext.status,
        },
      }))
      return ext
    } catch (err) {
      set((s) => ({
        extraction: { ...s.extraction, status: 'failed', error: err.message },
      }))
      throw err
    }
  },

  applyExtractionEvent(event) {
    if (!event || typeof event !== 'object') return
    set((s) => {
      const ext = { ...s.extraction }
      switch (event.type) {
        case 'snapshot':
          ext.status = event.status ?? ext.status
          if (typeof event.cost_usd === 'number') {
            ext.cost = { ...ext.cost, cost_usd: event.cost_usd }
          }
          break
        case 'progress':
          ext.status = 'running'
          ext.phase = event.phase ?? ext.phase
          if (typeof event.page === 'number') ext.page = event.page
          if (typeof event.total === 'number') ext.total = event.total
          ext.pageType = event.page_type ?? ext.pageType
          break
        case 'row_ready':
          ext.rowCount += Array.isArray(event.rows) ? event.rows.length : 0
          break
        case 'tokens':
          ext.cost = {
            cost_usd: event.cost_usd ?? ext.cost.cost_usd,
            total_tokens:
              (event.input_tokens ?? 0) + (event.output_tokens ?? 0),
            api_calls: event.api_calls ?? ext.cost.api_calls,
            by_model: event.by_model ?? ext.cost.by_model,
          }
          break
        case 'done':
          ext.status = 'completed'
          if (typeof event.cost_usd === 'number') {
            ext.cost = { ...ext.cost, cost_usd: event.cost_usd }
          }
          break
        case 'error':
          ext.status = 'failed'
          ext.error = event.message ?? 'unknown error'
          break
        case 'canceled':
          ext.status = 'canceled'
          break
        default:
          break
      }
      return { extraction: ext }
    })
  },

  resetExtraction() {
    set({
      extraction: {
        extractionId: null,
        status: 'idle',
        phase: null,
        page: 0,
        total: 0,
        pageType: null,
        cost: { cost_usd: 0, total_tokens: 0, api_calls: 0, by_model: {} },
        rowCount: 0,
        error: null,
      },
    })
  },

  async cancelExtraction() {
    const id = get().extraction.extractionId
    if (!id) return
    try {
      await apiFetch(`/api/extractions/${id}/cancel`, { method: 'POST' })
    } catch (err) {
      set((s) => ({
        extraction: { ...s.extraction, error: err.message },
      }))
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
