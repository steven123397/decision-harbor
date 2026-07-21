import { useState } from 'react'

type Status = 'idle' | 'submitting' | 'done'

interface Column {
  name: string
  type: string
}

interface Envelope {
  id?: number
  status: string
  sql?: string
  error_code?: string | null
  error_message?: string | null
  columns?: Column[] | null
  rows?: unknown[][] | null
  row_count?: number | null
  duration_ms?: number | null
}

export default function App() {
  const [sql, setSql] = useState(
    'SELECT id, customer_code, display_name FROM analytics.customers ORDER BY id LIMIT 5;',
  )
  const [status, setStatus] = useState<Status>('idle')
  const [result, setResult] = useState<Envelope | null>(null)

  async function submit() {
    setStatus('submitting')
    setResult(null)
    try {
      const res = await fetch('/api/v1/query-runs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sql }),
      })
      const data: Envelope = await res.json()
      setResult(data)
      setStatus('done')
    } catch (e) {
      setResult({
        status: 'failed',
        error_code: 'INTERNAL_ERROR',
        error_message: String(e),
      })
      setStatus('done')
    }
  }

  return (
    <main style={{ fontFamily: 'system-ui, sans-serif', padding: 24, maxWidth: 960 }}>
      <h1>DecisionHarbor 查询工作台</h1>
      <textarea
        id="sql-input"
        data-testid="sql-input"
        value={sql}
        onChange={(e) => setSql(e.target.value)}
        rows={6}
        style={{ width: '100%', fontFamily: 'monospace' }}
      />
      <div style={{ marginTop: 8 }}>
        <button
          id="submit-btn"
          data-testid="submit-btn"
          onClick={submit}
          disabled={status === 'submitting'}
        >
          {status === 'submitting' ? '执行中…' : '提交查询'}
        </button>
      </div>
      <div id="status" data-testid="status" style={{ marginTop: 12 }}>
        {status === 'submitting' && '执行中…'}
        {status === 'done' &&
          result &&
          `状态：${result.status}${result.duration_ms != null ? ` · ${result.duration_ms}ms` : ''}`}
      </div>
      <section id="result" data-testid="result" style={{ marginTop: 16 }}>
        {result && result.status === 'succeeded' && result.columns && result.rows && (
          <table border={1} cellPadding={6} style={{ borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                {result.columns.map((c) => (
                  <th key={c.name}>{c.name}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {result.rows.map((row, i) => (
                <tr key={i}>
                  {row.map((cell, j) => (
                    <td key={j}>{String(cell)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {result && (result.status === 'rejected' || result.status === 'failed') && (
          <div id="error-block" data-testid="error-block">
            <div id="error-code" data-testid="error-code">
              错误码：{result.error_code}
            </div>
            <div>{result.error_message}</div>
            {result.id != null && <div>记录 ID：{result.id}</div>}
          </div>
        )}
      </section>
    </main>
  )
}
