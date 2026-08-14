import { useCallback, useEffect, useRef, useState } from 'react'
import { getQueryRun, submitQuery } from './api'
import type { QueryRun } from './types'

const STATUS_LABEL: Record<string, string> = {
  running: '执行中…',
  succeeded: '成功',
  failed: '失败',
  rejected: '已拒绝',
}

export default function App() {
  const [sql, setSql] = useState('SELECT * FROM customers LIMIT 5')
  const [run, setRun] = useState<QueryRun | null>(null)
  const [transportError, setTransportError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const timerRef = useRef<number | null>(null)

  const stopPolling = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }, [])

  useEffect(() => stopPolling, [stopPolling])

  const poll = useCallback((id: string) => {
    getQueryRun(id)
      .then((r) => {
        setRun(r)
        if (r.status === 'running') {
          timerRef.current = window.setTimeout(() => poll(id), 500)
        }
      })
      .catch((e: unknown) => {
        setTransportError(String((e as Error).message ?? e))
      })
  }, [])

  async function onSubmit() {
    if (busy) return
    setBusy(true)
    setTransportError(null)
    setRun(null)
    try {
      const r = await submitQuery(sql)
      setRun(r)
      if (r.status === 'running') {
        timerRef.current = window.setTimeout(() => poll(r.id), 500)
      }
    } catch (e) {
      setTransportError(String((e as Error).message ?? e))
    } finally {
      setBusy(false)
    }
  }

  const error = run?.error
    ? `[${run.error.code}] ${run.error.message}`
    : transportError

  return (
    <main style={{ maxWidth: 960, margin: '0 auto', padding: 24, fontFamily: 'system-ui, sans-serif' }}>
      <h1>DecisionHarbor 工作台</h1>
      <textarea
        data-testid="sql-input"
        value={sql}
        onChange={(e) => setSql(e.target.value)}
        rows={6}
        style={{ width: '100%', fontFamily: 'monospace', fontSize: 14, boxSizing: 'border-box' }}
      />
      <div style={{ marginTop: 8 }}>
        <button data-testid="submit" onClick={onSubmit} disabled={busy}>
          {busy ? '提交中…' : '运行'}
        </button>
      </div>

      {run && (
        <div data-testid="status" style={{ marginTop: 16 }}>
          状态：{STATUS_LABEL[run.status] ?? run.status}
          {run.duration_ms != null && ` · ${run.duration_ms} ms`}
          {run.row_count != null && ` · ${run.row_count} 行`}
        </div>
      )}

      {error && (
        <div data-testid="error" style={{ marginTop: 12, color: '#b00020' }}>
          {error}
        </div>
      )}

      {run?.status === 'succeeded' && run.result && (
        <table data-testid="result-table" style={{ marginTop: 16, borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              {run.result.columns.map((c) => (
                <th key={c.name} style={{ border: '1px solid #ccc', padding: '4px 8px', textAlign: 'left' }}>
                  {c.name} <span style={{ color: '#888', fontSize: 11 }}>({c.type})</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {run.result.rows.map((row, i) => (
              <tr key={i}>
                {run.result!.columns.map((c, j) => (
                  <td key={c.name} style={{ border: '1px solid #ccc', padding: '4px 8px' }}>
                    {row[j] == null ? '' : String(row[j])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  )
}
