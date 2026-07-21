import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import ResultTable from '../components/ResultTable'
import ErrorDisplay from '../components/ErrorDisplay'

describe('ResultTable', () => {
  it('renders column headers from column definitions', () => {
    const columns = [
      { name: 'id', type: 'BIGINT' },
      { name: 'region', type: 'VARCHAR' },
    ]
    render(<ResultTable columns={columns} rows={[]} rowCount={0} durationMs={5} />)
    expect(screen.getByText('id')).toBeInTheDocument()
    expect(screen.getByText('region')).toBeInTheDocument()
  })

  it('renders row data correctly', () => {
    const columns = [{ name: 'name', type: 'VARCHAR' }]
    const rows = [['Alice'], ['Bob']]
    render(<ResultTable columns={columns} rows={rows} rowCount={2} durationMs={10} />)
    expect(screen.getByText('Alice')).toBeInTheDocument()
    expect(screen.getByText('Bob')).toBeInTheDocument()
  })

  it('renders NULL for null values', () => {
    const columns = [{ name: 'val', type: 'VARCHAR' }]
    const rows = [[null]]
    render(<ResultTable columns={columns} rows={rows} rowCount={1} durationMs={3} />)
    expect(screen.getByText('NULL')).toBeInTheDocument()
  })

  it('shows row count and duration', () => {
    render(<ResultTable columns={[]} rows={[]} rowCount={42} durationMs={123} />)
    expect(screen.getByText(/42/)).toBeInTheDocument()
    expect(screen.getByText(/123/)).toBeInTheDocument()
  })
})

describe('ErrorDisplay', () => {
  it('shows rejection with code and message', () => {
    render(<ErrorDisplay code="FORBIDDEN_STATEMENT" message="不允许的语句类型" status="rejected" />)
    expect(screen.getByText(/策略拒绝/)).toBeInTheDocument()
    expect(screen.getByText(/FORBIDDEN_STATEMENT/)).toBeInTheDocument()
    expect(screen.getByText('不允许的语句类型')).toBeInTheDocument()
  })

  it('shows failure with code and message', () => {
    render(<ErrorDisplay code="EXECUTION_ERROR" message="查询执行失败" status="failed" />)
    expect(screen.getByText(/EXECUTION_ERROR/)).toBeInTheDocument()
    expect(screen.getByText('查询执行失败')).toBeInTheDocument()
  })
})
