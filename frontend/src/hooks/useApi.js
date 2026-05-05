/* ──────────────────────────────────────────────────────────────────
 * useApi — thin wrapper around fetch() that injects the current
 * Supabase access token as a Bearer auth header. Routes that don't
 * require auth (e.g. /api/health) still work because we only set
 * the header when a session exists.
 *
 * Also exports a non-hook `apiFetch` for use outside React (e.g.
 * zustand stores, plain helpers). Both share the same implementation.
 * ────────────────────────────────────────────────────────────────── */

import supabase, { isSupabaseConfigured } from '../auth/supabaseClient.js'

function isPlainObject(value) {
  if (value == null || typeof value !== 'object') return false
  if (value instanceof FormData) return false
  if (value instanceof Blob) return false
  if (value instanceof ArrayBuffer) return false
  if (typeof value === 'string') return false
  const proto = Object.getPrototypeOf(value)
  return proto === Object.prototype || proto === null
}

export async function apiFetch(path, options = {}) {
  const { headers: rawHeaders, body: rawBody, ...rest } = options
  const headers = new Headers(rawHeaders || {})

  // Only attach a Bearer token when Supabase is wired up. In dev-mode
  // (no env vars yet) the backend's auth middleware also short-circuits,
  // so /api/me + friends respond without a token.
  if (isSupabaseConfigured) {
    const { data } = await supabase.auth.getSession()
    const token = data?.session?.access_token
    if (token && !headers.has('Authorization')) {
      headers.set('Authorization', `Bearer ${token}`)
    }
  }

  let body = rawBody
  if (isPlainObject(rawBody)) {
    body = JSON.stringify(rawBody)
    if (!headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json')
    }
  }

  const res = await fetch(path, { ...rest, headers, body })

  if (!res.ok) {
    let payload = null
    try {
      payload = await res.text()
    } catch {
      payload = null
    }
    const err = new Error(`API ${path} → ${res.status}`)
    err.status = res.status
    err.body = payload
    throw err
  }

  if (res.status === 204) return null
  const contentType = res.headers.get('Content-Type') || ''
  if (!contentType.includes('application/json')) {
    return null
  }
  return res.json()
}

export function useApi() {
  return { apiFetch }
}

export default useApi
