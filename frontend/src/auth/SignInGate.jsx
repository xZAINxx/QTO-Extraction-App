/* ──────────────────────────────────────────────────────────────────
 * SignInGate — wraps the whole app shell. While we're checking the
 * cached session, render a centered placeholder. If there's no
 * session, render the Supabase Auth UI (themed to match QTO's
 * emerald accent + dark canvas). Otherwise pass children through.
 *
 * The auth-state subscription keeps the gate in sync with sign-out
 * events fired from anywhere else (e.g. a future user menu).
 * ────────────────────────────────────────────────────────────────── */

import { useEffect, useState } from 'react'
import { Auth } from '@supabase/auth-ui-react'
import { ThemeSupa } from '@supabase/auth-ui-shared'
import supabase, { isSupabaseConfigured } from './supabaseClient.js'

export default function SignInGate({ children }) {
  const [session, setSession] = useState(null)
  const [loading, setLoading] = useState(isSupabaseConfigured)

  useEffect(() => {
    if (!isSupabaseConfigured) return undefined
    let cancelled = false

    supabase.auth.getSession().then(({ data }) => {
      if (cancelled) return
      setSession(data.session)
      setLoading(false)
    })

    const { data: sub } = supabase.auth.onAuthStateChange((_event, sess) => {
      if (cancelled) return
      setSession(sess)
    })

    return () => {
      cancelled = true
      sub?.subscription?.unsubscribe?.()
    }
  }, [])

  // Dev mode: Supabase isn't configured yet. Pass children through with
  // a non-blocking banner so the rest of the app remains demoable. The
  // backend's auth middleware mirrors this posture (skips JWT check
  // when SUPABASE_JWT_SECRET is empty), so /api/me etc. won't 401.
  if (!isSupabaseConfigured) {
    return (
      <>
        <div className="auth-banner">
          <strong>Dev mode</strong> — Supabase auth is off.
          Add <code>VITE_SUPABASE_URL</code> + <code>VITE_SUPABASE_ANON_KEY</code> to{' '}
          <code>frontend/.env.local</code> to enable sign-in.
        </div>
        {children}
      </>
    )
  }

  if (loading) {
    return (
      <div className="auth-shell">
        <div className="auth-loading">Loading…</div>
      </div>
    )
  }

  if (!session) {
    return (
      <div className="auth-shell">
        <div className="auth-card">
          <div className="auth-card__brand">
            <div className="auth-card__logo" aria-hidden>
              Z
            </div>
            <div className="auth-card__product">Zeconic QTO</div>
          </div>
          <div className="auth-card__subtitle">
            Sign in to start a takeoff
          </div>
          <Auth
            supabaseClient={supabase}
            appearance={{
              theme: ThemeSupa,
              variables: {
                default: {
                  colors: {
                    brand: '#16A34A',
                    brandAccent: '#15803D',
                    defaultButtonBackground: '#16A34A',
                    defaultButtonBackgroundHover: '#15803D',
                  },
                },
              },
            }}
            providers={['google']}
            redirectTo={window.location.origin}
            theme="dark"
          />
        </div>
      </div>
    )
  }

  return <>{children}</>
}
