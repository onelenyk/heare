// The one place the dashboard attaches its credential.
//
// The daemon serves this page and inlines the API token into the document
// as `window.__HEARE_TOKEN__` (see `_inject_token_html` in src/api.py), so
// there is nothing for the user to paste. Every route but the shell itself
// answers 401 without an `Authorization: Bearer <token>` header.
//
// Forty-odd `fetch(API + '/…')` calls are spread across the components, and
// threading a helper through all of them would leave the next one written
// unauthenticated by default. Wrapping `fetch` once, here, makes the
// authenticated call the one you get for free.

export const apiToken =
  (typeof window !== 'undefined' && window.__HEARE_TOKEN__) || ''

const LOCAL_API = /^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(:\d+)?(\/|$)/i

// Only our own daemon gets the token: a component that one day fetches
// some third-party URL must not hand it that.
function isLocalApi(url) {
  if (!url) return true // relative to this page, i.e. the daemon
  if (url.startsWith('/')) return true
  if (LOCAL_API.test(url)) return true
  return typeof window !== 'undefined' && url.startsWith(window.location.origin)
}

export function authHeaders(extra) {
  const headers = new Headers(extra || undefined)
  if (apiToken && !headers.has('Authorization')) {
    headers.set('Authorization', 'Bearer ' + apiToken)
  }
  return headers
}

// For EventSource and anything else that cannot set a header. The daemon
// accepts `?token=` on GET only, for exactly this reason.
export function withToken(url) {
  if (!apiToken) return url
  return url + (url.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(apiToken)
}

export function installAuthFetch() {
  if (typeof window === 'undefined' || !window.fetch) return
  if (window.fetch.__heareAuth) return
  const nativeFetch = window.fetch.bind(window)
  const wrapped = (input, init) => {
    const url =
      typeof input === 'string'
        ? input
        : (input && typeof input.url === 'string' && input.url) || ''
    if (!apiToken || !isLocalApi(url)) return nativeFetch(input, init)
    const base = (init && init.headers) || (input && input.headers) || undefined
    return nativeFetch(input, { ...(init || {}), headers: authHeaders(base) })
  }
  wrapped.__heareAuth = true
  window.fetch = wrapped
}

installAuthFetch()
