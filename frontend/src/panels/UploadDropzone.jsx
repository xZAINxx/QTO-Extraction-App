/* ──────────────────────────────────────────────────────────────────
 * UploadDropzone — drag-drop + click-to-browse for PDF uploads.
 *
 * Wires through `useProjectStore.uploadPdf` so the post-upload state
 * (pdf list update, error tracking) lives in zustand. The dropzone
 * itself only owns transient UI state (drag-active visual, last
 * upload status message).
 *
 * Hard-caps file size at 50MB on the client to match the backend
 * limit and surface the error before round-tripping.
 * ────────────────────────────────────────────────────────────────── */

import { useCallback, useState } from 'react'
import { useDropzone } from 'react-dropzone'
import { CheckCircle2, FileText, Loader2, Upload } from 'lucide-react'
import useProjectStore from '../stores/projectStore.js'

const MAX_BYTES = 50 * 1024 * 1024

export default function UploadDropzone() {
  const activeProjectId = useProjectStore((s) => s.activeProjectId)
  const uploadPdf = useProjectStore((s) => s.uploadPdf)
  const uploadingPdf = useProjectStore((s) => s.uploadingPdf)
  const openNewProjectModal = useProjectStore((s) => s.openNewProjectModal)

  const [status, setStatus] = useState({ kind: 'idle', message: '' })

  const onDrop = useCallback(
    async (acceptedFiles, fileRejections) => {
      if (fileRejections.length > 0) {
        const reason = fileRejections[0].errors[0]?.message ?? 'Rejected'
        setStatus({ kind: 'error', message: reason })
        return
      }
      const [file] = acceptedFiles
      if (!file) return

      if (!activeProjectId) {
        setStatus({
          kind: 'error',
          message: 'Create a project first — pick or add one above.',
        })
        return
      }
      if (file.size > MAX_BYTES) {
        setStatus({
          kind: 'error',
          message: `File too large (${(file.size / 1024 / 1024).toFixed(1)}MB; max 50MB).`,
        })
        return
      }

      setStatus({ kind: 'uploading', message: `Uploading ${file.name}…` })
      try {
        const pdf = await uploadPdf(file)
        setStatus({
          kind: 'success',
          message: `Uploaded ${pdf.filename}${
            pdf.page_count ? ` · ${pdf.page_count} pages` : ''
          }`,
        })
        // Auto-clear success after 4 seconds.
        setTimeout(() => {
          setStatus((cur) =>
            cur.kind === 'success' ? { kind: 'idle', message: '' } : cur,
          )
        }, 4000)
      } catch (err) {
        setStatus({ kind: 'error', message: err.message })
      }
    },
    [activeProjectId, uploadPdf],
  )

  const { getRootProps, getInputProps, isDragActive, open } = useDropzone({
    onDrop,
    accept: { 'application/pdf': ['.pdf'] },
    multiple: false,
    maxSize: MAX_BYTES,
    disabled: uploadingPdf,
    noClick: true,    // we wire the click ourselves on the inner button
    noKeyboard: true,
  })

  const dropzoneClass =
    'upload-dropzone' +
    (isDragActive ? ' upload-dropzone--drag' : '') +
    (status.kind === 'error' ? ' upload-dropzone--error' : '') +
    (status.kind === 'success' ? ' upload-dropzone--success' : '') +
    (uploadingPdf ? ' upload-dropzone--uploading' : '')

  return (
    <div {...getRootProps({ className: dropzoneClass })}>
      <input {...getInputProps()} />

      <div className="upload-dropzone__icon" aria-hidden>
        {status.kind === 'uploading' ? (
          <Loader2 size={22} className="upload-dropzone__spin" />
        ) : status.kind === 'success' ? (
          <CheckCircle2 size={22} />
        ) : (
          <Upload size={22} />
        )}
      </div>

      <h2 className="upload-dropzone__title">
        {status.kind === 'uploading'
          ? status.message
          : 'Drop a PDF here'}
      </h2>

      <p className="upload-dropzone__body">
        {status.kind === 'uploading'
          ? 'This usually takes a few seconds for typical drawing sets.'
          : status.kind === 'success'
          ? status.message
          : 'PDF only · 50MB max · your drawings stay on this machine until extraction runs.'}
      </p>

      {status.kind === 'error' && (
        <div className="upload-dropzone__error">{status.message}</div>
      )}

      {status.kind !== 'uploading' && (
        <div className="upload-dropzone__actions">
          {activeProjectId ? (
            <button
              type="button"
              className="btn btn--emerald"
              onClick={open}
              disabled={uploadingPdf}
            >
              <FileText size={14} />
              <span>Choose a PDF</span>
            </button>
          ) : (
            <button
              type="button"
              className="btn btn--emerald"
              onClick={openNewProjectModal}
            >
              <span>Create a project to start</span>
            </button>
          )}
        </div>
      )}
    </div>
  )
}
