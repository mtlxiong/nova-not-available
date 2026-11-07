/** Empty in local dev (Vite proxies /api and /ws). Set for the Vercel build so the page can reach the Python server. */
export const backendOrigin = (import.meta.env.VITE_BACKEND_ORIGIN ?? '').replace(/\/$/, '')

export function withBackend(path: string | null | undefined): string {
  if (!path) return ''
  if (!backendOrigin || /^(https?:|data:|blob:)/.test(path)) return path
  return `${backendOrigin}${path.startsWith('/') ? '' : '/'}${path}`
}

export function wsUrl(): string {
  if (!backendOrigin) {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    return `${proto}://${location.host}/ws`
  }
  const url = new URL(backendOrigin)
  const proto = url.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${url.host}/ws`
}
