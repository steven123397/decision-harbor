/// <reference types="vite/client" />

export interface QueryRunRecord {
  id: string
  status: 'running' | 'succeeded' | 'rejected' | 'failed'
  sql: string
  error_code: string | null
  error_message: string | null
  row_count: number | null
  duration_ms: number | null
  created_at: string
}

export interface QueryResult {
  columns: { name: string; type: string }[]
  rows: unknown[][]
  truncated: boolean
}

export interface QueryRunResponse {
  record: QueryRunRecord
  result: QueryResult | null
}

async function readError(res: Response): Promise<Error> {
  try {
    const body = await res.json()
    if (body?.error?.message) return new Error(body.error.message)
  } catch {
    /* fall through */
  }
  return new Error(`请求失败（HTTP ${res.status}）`)
}

export async function submitQuery(sql: string): Promise<QueryRunResponse> {
  const res = await fetch('/api/v1/query-runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sql }),
  })
  if (!res.ok) throw await readError(res)
  return res.json()
}

export async function checkReady(): Promise<boolean> {
  try {
    const res = await fetch('/ready')
    return res.ok
  } catch {
    return false
  }
}
