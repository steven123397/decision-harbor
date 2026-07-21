interface ColumnDef {
  name: string
  type: string
}

interface Props {
  columns: ColumnDef[]
  rows: unknown[][]
  rowCount: number
  durationMs: number
}

export default function ResultTable({ columns, rows, rowCount, durationMs }: Props) {
  return (
    <div className="result-area">
      <div className="result-meta">
        返回 {rowCount} 行，耗时 {durationMs} ms
      </div>
      <div className="table-wrapper">
        <table>
          <thead>
            <tr>
              {columns.map((col) => (
                <th key={col.name}>{col.name}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i}>
                {row.map((cell, j) => (
                  <td key={j}>{cell === null ? 'NULL' : String(cell)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
