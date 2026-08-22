import { canCancel, canRetry, HistoryPage, stateLabel } from "./api";

interface RunHistoryProps {
  page: HistoryPage | null;
  error: string | null;
  onView: (runId: number) => void;
  onCancel: (runId: number) => void;
  onRetry: (runId: number) => void;
  onMore: () => void;
}

const actionButton: React.CSSProperties = {
  marginRight: 4,
  padding: "2px 10px",
};

/** 历史运行列表：按创建时间降序的游标分页，展示终态与重试关系
 * （retry_of 指向原运行，CONTEXT.md「重试关系」）。行内操作与当前
 * 面板共用同一组处理器。 */
export default function RunHistory({
  page,
  error,
  onView,
  onCancel,
  onRetry,
  onMore,
}: RunHistoryProps) {
  return (
    <section data-testid="history" style={{ marginTop: 40 }}>
      <h2 style={{ fontSize: 18 }}>历史运行</h2>
      {error && <p data-testid="history-error">{error}</p>}
      {page && (
        <>
          <div style={{ overflowX: "auto", maxWidth: "100%" }}>
            <table
              data-testid="history-table"
              border={1}
              cellPadding={6}
              style={{ borderCollapse: "collapse", minWidth: 560 }}
            >
              <thead>
                <tr>
                  <th>运行</th>
                  <th>状态</th>
                  <th>创建时间</th>
                  <th>重试关系</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {page.runs.map((run) => (
                  <tr key={run.id} data-testid={`history-row-${run.id}`}>
                    <td>#{run.id}</td>
                    <td>{stateLabel(run.state)}</td>
                    <td>{run.created_at ? new Date(run.created_at).toLocaleString() : "—"}</td>
                    <td>{run.retry_of != null ? `重试自 #${run.retry_of}` : ""}</td>
                    <td>
                      <button
                        data-testid={`view-${run.id}`}
                        style={actionButton}
                        onClick={() => onView(run.id)}
                      >
                        查看
                      </button>
                      {canCancel(run) && (
                        <button
                          data-testid={`cancel-${run.id}`}
                          style={actionButton}
                          onClick={() => onCancel(run.id)}
                        >
                          取消
                        </button>
                      )}
                      {canRetry(run) && (
                        <button
                          data-testid={`retry-${run.id}`}
                          style={actionButton}
                          onClick={() => onRetry(run.id)}
                        >
                          重试
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {page.nextCursor && (
            <button
              data-testid="history-more"
              style={{ marginTop: 8, padding: "6px 16px" }}
              onClick={onMore}
            >
              加载更多
            </button>
          )}
        </>
      )}
    </section>
  );
}
