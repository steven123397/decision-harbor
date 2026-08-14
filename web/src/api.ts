import type { QueryRun } from './types'

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'

export async function submitQuery(sql: string): Promise<QueryRun> {
  const res = await fetch(`${API_BASE}/api/v1/query-runs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sql }),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    if (body && body.id && body.status === 'rejected') {
      return body as QueryRun
    }
    throw new Error((body?.error?.message as string) ?? `HTTP ${res.status}`)
  }
  return (await res.json()) as QueryRun
}

export async function getQueryRun(id: string): Promise<QueryRun> {
  const res = await fetch(`${API_BASE}/api/v1/query-runs/${id}`)
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error((body?.error?.message as string) ?? `HTTP ${res.status}`)
  }
  return (await res.json()) as QueryRun
}
