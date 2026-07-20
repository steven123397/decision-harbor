import { useEffect, useState } from 'react'
import { checkReady, submitQuery, type QueryRunResponse } from './api'

type Phase = 'idle' | 'running'

function ResultView({ outcome }: { outcome: QueryRunResponse }) {
  const { record, result } = outcome
  if (record.status === 'succeeded' && result) {
    return (
      <section>
        <p>
          返回 {record.row_count} 行，耗时 {record.duration_ms} ms
          {result.truncated ? '；结果已截断至行数上限' : ''}
        </p>
        <table>
          <thead>
            <tr>
              {result.columns.map((col) => (
                <th key={col.name}>
                  {col.name}（{col.type}）
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {result.rows.map((row, i) => (
              <tr key={i}>
                {row.map((cell, j) => (
                  <td key={j}>{cell === null ? 'NULL' : String(cell)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    )
  }
  if (record.status === 'rejected') {
    return (
      <section role="note">
        <p>查询被拒绝</p>
        <p>
          <code>{record.error_code}</code>：{record.error_message}
        </p>
        <p>记录标识：{record.id}</p>
      </section>
    )
  }
  if (record.status === 'failed') {
    return (
      <section role="note">
        <p>查询失败</p>
        <p>
          <code>{record.error_code}</code>：{record.error_message}
        </p>
        <p>记录标识：{record.id}</p>
      </section>
    )
  }
  return null
}

export default function App() {
  const [ready, setReady] = useState<boolean | null>(null)
  const [sql, setSql] = useState('')
  const [phase, setPhase] = useState<Phase>('idle')
  const [outcome, setOutcome] = useState<QueryRunResponse | null>(null)
  const [requestError, setRequestError] = useState<string | null>(null)

  useEffect(() => {
    checkReady().then(setReady)
  }, [])

  async function onSubmit() {
    setPhase('running')
    setOutcome(null)
    setRequestError(null)
    try {
      setOutcome(await submitQuery(sql))
    } catch (err) {
      setRequestError(err instanceof Error ? err.message : String(err))
    } finally {
      setPhase('idle')
    }
  }

  return (
    <main>
      <h1>DecisionHarbor 查询工作台</h1>
      {ready === false && <p role="alert">后端未就绪，请稍候重试。</p>}
      <label htmlFor="sql-input">SQL</label>
      <textarea
        id="sql-input"
        rows={8}
        cols={80}
        value={sql}
        onChange={(event) => setSql(event.target.value)}
        disabled={phase === 'running'}
      />
      <div>
        <button onClick={onSubmit} disabled={phase === 'running' || ready === false}>
          提交
        </button>
      </div>
      {phase === 'running' && <p role="status">执行中…</p>}
      {requestError && <p role="alert">{requestError}</p>}
      {outcome && <ResultView outcome={outcome} />}
    </main>
  )
}
