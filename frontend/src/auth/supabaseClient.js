/* ──────────────────────────────────────────────────────────────────
 * Supabase client — single shared instance for the whole frontend.
 *
 * Reads VITE_SUPABASE_URL + VITE_SUPABASE_ANON_KEY from the Vite env.
 * Both values are public-safe (the anon key is rate-limited by
 * Supabase row-level security, not by secrecy).
 *
 * Graceful degradation: if either env var is missing we DON'T throw.
 * Instead ``isSupabaseConfigured`` is false and ``supabase`` is null,
 * which ``SignInGate`` reads to render an onboarding card explaining
 * how to fill in the keys. This keeps the rest of the app demoable in
 * a fresh checkout without forcing a Supabase project setup first.
 * Backend mirrors the same posture — when ``SUPABASE_JWT_SECRET`` is
 * empty, the auth middleware lets all requests through (dev mode).
 * ────────────────────────────────────────────────────────────────── */

import { createClient } from '@supabase/supabase-js'

const url = import.meta.env.VITE_SUPABASE_URL
const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY

export const isSupabaseConfigured = Boolean(url && anonKey)

export const supabase = isSupabaseConfigured
  ? createClient(url, anonKey, {
      auth: {
        persistSession: true,
        autoRefreshToken: true,
      },
    })
  : null

export default supabase
