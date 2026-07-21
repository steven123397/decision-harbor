interface Props {
  code: string
  message: string
  status: string
}

export default function ErrorDisplay({ code, message, status }: Props) {
  return (
    <div className={`error-area error-${status}`}>
      <div className="error-code">{status === 'rejected' ? '策略拒绝' : '执行失败'} [{code}]</div>
      <div className="error-message">{message}</div>
    </div>
  )
}
