import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import type { QueryRunResponse } from './api'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const succeeded: QueryRunResponse = {
  record: {
    id: 'run-1',
    status: 'succeeded',
    sql: 'SELECT 1',
    error_code: null,
    error_message: null,
    row_count: 2,
    duration_ms: 12,
    created_at: '2026-07-20T00:00:00Z',
  },
  result: {
    columns: [
      { name: 'region', type: 'varchar' },
      { name: 'n', type: 'int8' },
    ],
    rows: [
      ['East', 20],
      ['West', 20],
    ],
    truncated: false,
  },
}

const rejected: QueryRunResponse = {
  record: {
    id: 'run-2',
    status: 'rejected',
    sql: 'DROP TABLE customers',
    error_code: 'POLICY_NON_QUERY_STATEMENT',
    error_message: '只允许只读查询表达式。',
    row_count: null,
    duration_ms: null,
    created_at: '2026-07-20T00:00:00Z',
  },
  result: null,
}

const failed: QueryRunResponse = {
  record: {
    id: 'run-3',
    status: 'failed',
    sql: 'SELECT 1/0',
    error_code: 'EXECUTION_ERROR',
    error_message: '数据库执行错误：division by zero',
    row_count: null,
    duration_ms: null,
    created_at: '2026-07-20T00:00:00Z',
  },
  result: null,
}

function mockFetch(postImpl: () => Promise<Response>) {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/ready')) return Promise.resolve(jsonResponse({ status: 'ready' }))
      return postImpl()
    }),
  )
}

async function submitSql(sql = 'SELECT 1') {
  const user = userEvent.setup()
  render(<App />)
  await user.type(await screen.findByLabelText('SQL'), sql)
  await user.click(screen.getByRole('button', { name: '提交' }))
  return user
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('查询工作台', () => {
  it('提交成功：展示结果表格、行数与耗时', async () => {
    mockFetch(() => Promise.resolve(jsonResponse(succeeded)))
    await submitSql()
    const table = await screen.findByRole('table')
    expect(table).toHaveTextContent('region')
    expect(table).toHaveTextContent('East')
    expect(screen.getByText(/返回 2 行/)).toBeInTheDocument()
    expect(screen.getByText(/耗时 12 ms/)).toBeInTheDocument()
  })

  it('提交成功且截断：展示截断提示', async () => {
    const truncated = {
      ...succeeded,
      result: { ...succeeded.result!, truncated: true },
    }
    mockFetch(() => Promise.resolve(jsonResponse(truncated)))
    await submitSql()
    expect(await screen.findByText(/结果已截断/)).toBeInTheDocument()
  })

  it('策略拒绝：展示错误码、说明与记录标识', async () => {
    mockFetch(() => Promise.resolve(jsonResponse(rejected)))
    await submitSql('DROP TABLE customers')
    expect(await screen.findByText(/查询被拒绝/)).toBeInTheDocument()
    expect(screen.getByText('POLICY_NON_QUERY_STATEMENT')).toBeInTheDocument()
    expect(screen.getByText(/run-2/)).toBeInTheDocument()
  })

  it('执行失败：展示错误码与摘要', async () => {
    mockFetch(() => Promise.resolve(jsonResponse(failed)))
    await submitSql()
    expect(await screen.findByText(/查询失败/)).toBeInTheDocument()
    expect(screen.getByText('EXECUTION_ERROR')).toBeInTheDocument()
  })

  it('提交等待期间展示执行中状态', async () => {
    let resolvePost: (res: Response) => void = () => {}
    mockFetch(
      () =>
        new Promise<Response>((resolve) => {
          resolvePost = resolve
        }),
    )
    await submitSql()
    expect(await screen.findByText(/执行中/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '提交' })).toBeDisabled()
    resolvePost(jsonResponse(succeeded))
    await waitFor(() => expect(screen.queryByText(/执行中/)).not.toBeInTheDocument())
  })

  it('请求层失败：展示可读错误', async () => {
    mockFetch(() =>
      Promise.resolve(
        jsonResponse({ error: { code: 'INVALID_REQUEST', message: 'sql 必须为非空字符串。' } }, 400),
      ),
    )
    await submitSql('   ')
    expect(await screen.findByRole('alert')).toHaveTextContent('sql 必须为非空字符串。')
  })
})
