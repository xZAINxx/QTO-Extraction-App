/* ──────────────────────────────────────────────────────────────────
 * useExtractionStream — subscribe to /api/extractions/{id}/events
 * via a native EventSource. Hands every parsed event to the
 * `onEvent` callback the caller supplies. Closes the channel on
 * unmount, on the [DONE] sentinel, or on a terminator event type
 * (done | error | canceled).
 *
 * EventSource doesn't support custom headers (no Authorization), so
 * the dev-mode flow works as-is. Production with Supabase auth will
 * need a same-origin cookie or a per-stream signed token; both are
 * doable but out of v1 scope — we'll add a token query param when
 * the auth flow is enabled end-to-end.
 * ────────────────────────────────────────────────────────────────── */

import { useEffect } from 'react'

export function useExtractionStream(extractionId, onEvent) {
  useEffect(() => {
    if (!extractionId) return undefined

    const url = `/api/extractions/${extractionId}/events`
    const es = new EventSource(url, { withCredentials: false })

    const handleMessage = (e) => {
      if (e.data === '[DONE]') {
        es.close()
        return
      }
      try {
        const payload = JSON.parse(e.data)
        onEvent(payload)
        if (
          payload.type === 'done' ||
          payload.type === 'error' ||
          payload.type === 'canceled'
        ) {
          es.close()
        }
      } catch (err) {
        // Malformed event — log but don't blow up the consumer.
        // eslint-disable-next-line no-console
        console.error('extraction-stream parse error', err, e.data)
      }
    }

    const handleError = (err) => {
      // eslint-disable-next-line no-console
      console.warn('extraction-stream error', err)
      // EventSource auto-retries; we don't close on transient errors.
    }

    es.addEventListener('message', handleMessage)
    es.addEventListener('error', handleError)

    return () => {
      es.removeEventListener('message', handleMessage)
      es.removeEventListener('error', handleError)
      es.close()
    }
  }, [extractionId, onEvent])
}

export default useExtractionStream
