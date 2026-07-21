import { useState } from 'react'
import ResultTable from './ResultTable'
import ErrorDisplay from './ErrorDisplay'

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

interface ColumnDef {
  name: string
  type: string
}

interface QueryResult {
  status: string
  data?: {
    id: number
    columns?: ColumnDef[]
    rows?: unknown[][]
    row_count?: number
    duration_ms?: number
    created_at: string
  }
  error?: {
    code: string
    message: string
  }
}

export default function QueryWorkbench() {
  const [sql, setSql] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<QueryResult | null>(null)

  const handleSubmit = async () => {
    if (!sql.trim()) return
    setLoading(true)
    setResult(null)
    try {
      const res = await fetch(`${API_BASE}/api/v1/query-runs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sql }),
      })
      const data: QueryResult = await res.json()
      setResult(data)
    } catch {
      setResult({
        status: 'failed',
        error: { code: 'NETWORK_ERROR', message: '无法连接到 API 服务' },
      })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="workbench">
      <div className="sql-input-area">
        <textarea
          value={sql}
          onChange={(e) => setSql(e.target.value)}
          placeholder="输入 SQL 查询..."
          rows={6}
          disabled={loading}
          aria-label="SQL 输入"
        />
        <button onClick={handleSubmit} disabled={loading || !sql.trim()}>
          {loading ? '执行中...' : '执行查询'}
        </button>
      </div>

      {loading && <div className="status-indicator">执行中...</div>}

      {result && result.status === 'succeeded' && result.data && (
        <ResultTable
          columns={result.data.columns || []}
          rows={result.data.rows || []}
          rowCount={result.data.row_count || 0}
          durationMs={result.data.duration_ms || 0}
        />
      )}

      {result && (result.status === 'rejected' || result.status === 'failed') && result.error && (
        <ErrorDisplay code={result.error.code} message={result.error.message} status={result.status} />
      )}
    </div>
  )
}
