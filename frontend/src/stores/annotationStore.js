/* ──────────────────────────────────────────────────────────────────
 * Annotation state — zustand store.
 *
 * Tracks the user's draft markup before it persists, the active tool,
 * the selection set, and the cached annotation list per PDF/sheet so
 * the canvas re-render cost stays low when switching pages.
 *
 * Tool implementations (Highlight, Cloud, Callout, Dimension, Text
 * Box, Legend) hook into this store: setActiveTool('highlight') →
 * mouse-drag in the AnnotationLayer creates a draft → the layer's
 * commit handler calls createAnnotation(draft).
 *
 * The 6 tool variants land in their own commits (16–19); this store
 * lands in PR #3's foundation so the layer + Edit menu can already
 * read selection state on day one.
 * ────────────────────────────────────────────────────────────────── */

import { create } from 'zustand'
import { apiFetch } from '../hooks/useApi.js'


export const ANNOTATION_TYPES = [
  { key: 'highlight', label: 'Highlight', shortcut: 'shift+h' },
  { key: 'cloud',     label: 'Cloud',     shortcut: 'shift+c' },
  { key: 'callout',   label: 'Callout',   shortcut: 'shift+k' },
  { key: 'dimension', label: 'Dimension', shortcut: 'shift+d' },
  { key: 'text_box',  label: 'Text Box',  shortcut: 'shift+t' },
  { key: 'legend',    label: 'Legend',    shortcut: 'shift+l' },
]


export const useAnnotationStore = create((set, get) => ({
  // ── Tool state ──────────────────────────────────────────────
  activeTool: null,          // 'highlight' | 'cloud' | …  | null = pan / select
  draft: null,               // in-progress geometry while the user drags
  selection: new Set(),      // ids of currently-selected annotations
  // Per-PDF cache: { [pdfId]: { [sheet_number]: Annotation[] } }
  byPdf: {},
  loading: false,
  error: null,

  setActiveTool(tool) {
    set({ activeTool: tool, draft: null })
  },

  // ── Selection model ─────────────────────────────────────────

  selectOnly(id) {
    set({ selection: new Set([id]) })
  },
  toggleSelected(id) {
    set((s) => {
      const next = new Set(s.selection)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return { selection: next }
    })
  },
  clearSelection() {
    set({ selection: new Set() })
  },
  selectAll() {
    const all = Object.values(get().byPdf)
      .flatMap((sheetMap) => Object.values(sheetMap).flat())
    set({ selection: new Set(all.map((a) => a.id)) })
  },

  // ── Server sync ─────────────────────────────────────────────

  async fetchForPdf(pdfId) {
    if (!pdfId) return
    set({ loading: true, error: null })
    try {
      const list = await apiFetch(`/api/pdfs/${pdfId}/annotations`)
      const bySheet = {}
      for (const a of list) {
        ;(bySheet[a.sheet_number] ??= []).push(a)
      }
      set((s) => ({
        byPdf: { ...s.byPdf, [pdfId]: bySheet },
        loading: false,
      }))
    } catch (err) {
      set({ loading: false, error: err.message })
    }
  },

  async createAnnotation(pdfId, payload) {
    const a = await apiFetch(`/api/pdfs/${pdfId}/annotations`, {
      method: 'POST',
      body: payload,
    })
    set((s) => {
      const sheetMap = { ...(s.byPdf[pdfId] ?? {}) }
      const list = [...(sheetMap[a.sheet_number] ?? []), a]
      sheetMap[a.sheet_number] = list
      return { byPdf: { ...s.byPdf, [pdfId]: sheetMap }, draft: null }
    })
    return a
  },

  async updateAnnotation(annotationId, patch) {
    const a = await apiFetch(`/api/annotations/${annotationId}`, {
      method: 'PATCH',
      body: patch,
    })
    set((s) => {
      const next = { ...s.byPdf }
      const sheetMap = { ...(next[a.pdf_id] ?? {}) }
      const list = (sheetMap[a.sheet_number] ?? []).map((x) =>
        x.id === a.id ? a : x,
      )
      sheetMap[a.sheet_number] = list
      next[a.pdf_id] = sheetMap
      return { byPdf: next }
    })
    return a
  },

  async deleteAnnotation(annotationId) {
    // Find pdf+sheet for the local cache pop.
    let target = null
    for (const [pdfId, sheets] of Object.entries(get().byPdf)) {
      for (const [sheet, list] of Object.entries(sheets)) {
        const found = list.find((x) => x.id === annotationId)
        if (found) {
          target = { pdfId, sheet }
          break
        }
      }
      if (target) break
    }
    await apiFetch(`/api/annotations/${annotationId}`, { method: 'DELETE' })
    if (target) {
      set((s) => {
        const next = { ...s.byPdf }
        const sheetMap = { ...(next[target.pdfId] ?? {}) }
        sheetMap[target.sheet] = (sheetMap[target.sheet] ?? []).filter(
          (x) => x.id !== annotationId,
        )
        next[target.pdfId] = sheetMap
        return {
          byPdf: next,
          selection: new Set(
            [...s.selection].filter((id) => id !== annotationId),
          ),
        }
      })
    }
  },

  // ── Edit menu (Phase 4 / commit 19) ─────────────────────────
  // These are wired now so the menu can pre-render disabled / enabled
  // states based on selection size; the actual ops land per-tool.

  async deleteSelection() {
    const ids = [...get().selection]
    for (const id of ids) {
      try {
        await get().deleteAnnotation(id)
      } catch {
        /* keep going on partial failure */
      }
    }
    set({ selection: new Set() })
  },

  // ── Draft (in-progress markup) ──────────────────────────────

  setDraft(draft) {
    set({ draft })
  },
  clearDraft() {
    set({ draft: null })
  },
}))


export default useAnnotationStore
