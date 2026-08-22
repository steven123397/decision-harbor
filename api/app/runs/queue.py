"""运行队列网关：入队、认领、终态发布与运维排水开关。

query_runs 表即队列（ADR-0015）。所有状态转移都是带条件的原子 UPDATE
（CAS）：认领用 FOR UPDATE SKIP LOCKED 避免多执行者争抢同一行，终态
发布与快照写入同事务、携带 generation 做 fencing——失去所有权的旧
执行者因代过期无法发布结果。

全局并发与自动重试（ADR-0019）：认领事务以事务级 advisory lock 串行
「数有效租约 + 认领」，容量以数据库为唯一事实源跨实例生效；attempt
硬上界 3 在认领侧统一计数（generation ≥ 1 即递增）；仅基础设施类
错误码自动回队重跑。
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.execute.executor import (
    QY_ANALYTICS_UNAVAILABLE,
    QY_TIMEOUT,
    SNAPSHOT_MAX_BYTES,
    column_dicts,
    snapshot_json,
)
from app.runs.models import TERMINAL_STATES

WORKER_PAUSED_FLAG = "worker.paused"

# 失败处置结果：回队 / 终态 / 失去所有权（CAS 未命中，含被接管、被取消）。
RetryOutcome = Literal["requeued", "failed", "fenced"]

# 取消处置结果：确定生效（排队取消）/ 已受理（运行中取消，best effort
# 中止进行中）/ 幂等重复 / 不可取消（已落入其他终态）。
CancelOutcome = Literal["cancelled", "cancelling", "already_cancelled", "not_cancellable"]

# 单次运行总执行次数硬上界（规格 #6：attempt 1→3，接管重跑同样计数）。
MAX_ATTEMPTS = 3

# 基础设施类失败码：自动重跑资格的唯一判据（ADR-0019）。连接丢失、
# 语句超时与执行者崩溃接管属于「环境暂态」；数据库确定性错误（列不
# 存在、语法错等）重跑必然复现，直接终态。命名避开 retry——那是
# 用户动作（CONTEXT.md「重试关系」）。
REQUEUE_ELIGIBLE_ERROR_CODES = frozenset({QY_TIMEOUT, QY_ANALYTICS_UNAVAILABLE})

ATTEMPTS_EXHAUSTED_CODE = "QY_ATTEMPTS_EXHAUSTED"
ATTEMPTS_EXHAUSTED_MESSAGE = f"自动重试次数已耗尽（{MAX_ATTEMPTS} 次执行均未成功）"

# 认领闸门的 advisory lock key：任意固定整数，仅用于串行化「数容量 +
# 认领」这一临界区（事务提交即释放），不承载队列所有权——所有权仍由
# 租约 + generation 表达（ADR-0017 否决的是后者用 advisory lock）。
CLAIM_GATE_LOCK_KEY = 7011


def build_snapshot(result) -> dict:
    """执行结果 → 快照字段（列定义、行、字节数）；worker 与测试共用，
    序列化口径只有这一处（executor.snapshot_json，取数期的字节累积
    同源，ADR-0011 扩展）。"""
    columns = column_dicts(result.columns)
    rows = result.rows
    size_bytes = len(
        snapshot_json({"columns": columns, "rows": rows}).encode("utf-8")
    )
    return {"columns": columns, "rows": rows, "size_bytes": size_bytes}


@dataclass(frozen=True)
class Claim:
    run_id: int
    sql: str
    attempt: int
    generation: int


def mark_queued(session: Session, run_id: int) -> bool:
    """received → queued（加入调用方事务，不自行提交）。

    返回 False 表示运行已不在 received（被并发转移，如取消）。
    """
    return (
        session.execute(
            text(
                "UPDATE query_runs SET state = 'queued', queued_at = now() "
                "WHERE id = :id AND state = 'received'"
            ),
            {"id": run_id},
        ).rowcount
        == 1
    )


def finalize_rejected(session: Session, run_id: int, *, code: str, message: str) -> bool:
    """received → rejected（终态）：策略拒绝同步落定，不入队。加入调用方事务。"""
    return (
        session.execute(
            text(
                "UPDATE query_runs SET state = 'rejected', rejection_code = :code, "
                "rejection_message = :message, finished_at = now() "
                "WHERE id = :id AND state = 'received'"
            ),
            {"id": run_id, "code": code, "message": message},
        ).rowcount
        == 1
    )


def claim_next(
    session_factory: sessionmaker[Session],
    *,
    worker_id: str,
    lease_seconds: int,
    run_id: int | None = None,
    capacity: int | None = None,
    exclude_run_ids: Collection[int] = frozenset(),
) -> Claim | None:
    """认领最早的可执行运行：写租约、递增 generation、转入 running。

    可执行 = queued，或 running 且租约已过期（接管：原执行者失去
    所有权，接管者重新执行）。FOR UPDATE SKIP LOCKED 使多个执行者
    并发认领时不争抢同一行；run_id 过滤仅供测试定位特定运行。

    attempt 硬上界（ADR-0019）：attempt 只在认领侧递增，且统一规则
    为「generation ≥ 1 即 +1」——首次认领（queued、generation=0）
    保持 attempt，此后无论接管还是失败回队重跑都递增；达到
    MAX_ATTEMPTS 的行不再是可执行候选，由 fail_expired_exhausted
    收尾。未认领到则返回 None。

    全局并发闸门：capacity 给出时，事务先取 advisory lock 串行化
    「数有效租约 + 认领」临界区，有效租约数（running 且未过期）已达
    capacity 即本轮不认领——第 N+1 个运行排队等待，而不是失败。闸门
    只看数据库事实，跨实例天然生效。
    """
    with session_factory() as session:
        # run_id / exclude 过滤在客户端拼接：参数化 NULL 判断与
        # NOT IN 空集会触发 AmbiguousParameter（PostgreSQL 无法推断
        # :rid 类型）。
        filter_clause = "AND id = :rid" if run_id is not None else ""
        if exclude_run_ids:
            ids = ",".join(str(int(r)) for r in exclude_run_ids)
            filter_clause += f" AND id NOT IN ({ids})"
        if capacity is not None:
            session.execute(
                text("SELECT pg_advisory_xact_lock(:key)"), {"key": CLAIM_GATE_LOCK_KEY}
            )
            active = session.execute(
                text(
                    "SELECT count(*) FROM query_runs "
                    "WHERE state = 'running' AND lease_expires_at > now()"
                )
            ).scalar_one()
            if active >= capacity:
                session.rollback()
                return None
        row = session.execute(
            text(
                "SELECT id, sql, attempt, generation, state FROM query_runs "
                "WHERE (state = 'queued' OR (state = 'running' "
                "AND lease_expires_at <= now())) "
                f"AND attempt < {MAX_ATTEMPTS} {filter_clause} "
                "ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED"
            ),
            {"rid": run_id} if run_id is not None else {},
        ).fetchone()
        if row is None:
            session.rollback()
            return None
        # 认领即递增 attempt，除非是全新排队行的首次认领（generation=0）。
        # 接管（原 state=running）与失败回队重跑（requeue_or_fail 落回
        # queued 但 generation ≥ 1）都算重新执行，统一递增（CONTEXT.md
        # 「尝试」）。行已被锁定，值不会并发漂移。
        bump = 1 if row.generation >= 1 else 0
        session.execute(
            text(
                "UPDATE query_runs SET state = 'running', worker_id = :worker, "
                "lease_expires_at = now() + make_interval(secs => :lease), "
                "generation = generation + 1, attempt = attempt + :bump, "
                "started_at = now() WHERE id = :id"
            ),
            {
                "worker": worker_id,
                "lease": lease_seconds,
                "bump": bump,
                "id": row.id,
            },
        )
        session.commit()
        return Claim(
            run_id=row.id,
            sql=row.sql,
            attempt=row.attempt + bump,
            generation=row.generation + 1,
        )


def fail_expired_exhausted(
    session_factory: sessionmaker[Session], *, grace_seconds: int = 30
) -> int:
    """清扫：租约过期且超过宽限、attempt 耗尽的运行转 failed 终态。

    attempt 达到硬上界后不再有认领路径（claim_next 的候选过滤），
    但行仍留在 running——终态只能由这里补上（通常是持有最后租约的
    执行者崩溃且无人再接）。宽限给「执行者活着、续期抖动」的运行留
    复活窗口：租约刚过期不扫，过期超过 grace_seconds（一个租约周期
    量级）仍未成功发布才宣告耗尽——成功发布即离开 running，清扫
    自然不命中；误杀面缩到「续期失败整个宽限期 + 恰在窗口内结束」
    的执行，其成功结果仍会被 fencing 拒绝，不产生重复发布。幂等，
    返回清扫行数。
    """
    with session_factory() as session:
        swept = session.execute(
            text(
                "UPDATE query_runs SET state = 'failed', "
                "error_code = :code, error_message = :msg, finished_at = now() "
                "WHERE state = 'running' "
                "AND lease_expires_at <= now() - make_interval(secs => :grace) "
                f"AND attempt >= {MAX_ATTEMPTS}"
            ),
            {
                "code": ATTEMPTS_EXHAUSTED_CODE,
                "msg": ATTEMPTS_EXHAUSTED_MESSAGE,
                "grace": grace_seconds,
            },
        ).rowcount
        session.commit()
        return swept


def requeue_or_fail(
    session_factory: sessionmaker[Session],
    claim: Claim,
    *,
    error_code: str,
    error_message: str,
) -> RetryOutcome:
    """执行失败后的处置：自动回队重跑或落 failed 终态。

    仅基础设施类失败码（REQUEUE_ELIGIBLE_ERROR_CODES）且 attempt 未
    耗尽时 CAS 回 queued——清空租约、保留 attempt（回队不预递增，
    重跑在认领时才计数，用户看到的 attempt 始终对齐真实执行次数）。
    其余情况经 publish_failure 直接终态，保留最后一次真实错误码与
    摘要（耗尽时错误码仍是失败原因本身，attempt 上限反映在 attempt
    字段，不互相改写）。

    返回 requeued / failed / fenced：fenced 泛指 CAS 未命中——执行者
    已失去所有权（被接管、被取消等），不得再转移该运行的状态。
    """
    if error_code in REQUEUE_ELIGIBLE_ERROR_CODES and claim.attempt < MAX_ATTEMPTS:
        with session_factory() as session:
            updated = session.execute(
                text(
                    "UPDATE query_runs SET state = 'queued', worker_id = NULL, "
                    "lease_expires_at = NULL, queued_at = now() "
                    "WHERE id = :id AND generation = :gen AND state = 'running'"
                ),
                {"id": claim.run_id, "gen": claim.generation},
            ).rowcount
            session.commit()
            return "requeued" if updated == 1 else "fenced"
    ok = publish_failure(
        session_factory, claim, error_code=error_code, error_message=error_message
    )
    return "failed" if ok else "fenced"


def renew_lease(
    session_factory: sessionmaker[Session],
    claim: Claim,
    *,
    worker_id: str,
    lease_seconds: int,
) -> bool:
    """租约心跳续期：仍持有所有权（worker + generation + running）才生效。

    返回 False 表示执行者已失去所有权（租约过期被接管），调用方应
    停止续期；其后的终态发布同样会被 generation fencing 拒绝。
    """
    with session_factory() as session:
        updated = session.execute(
            text(
                "UPDATE query_runs SET "
                "lease_expires_at = now() + make_interval(secs => :lease) "
                "WHERE id = :id AND state = 'running' AND worker_id = :worker "
                "AND generation = :gen"
            ),
            {
                "id": claim.run_id,
                "gen": claim.generation,
                "worker": worker_id,
                "lease": lease_seconds,
            },
        ).rowcount
        session.commit()
        return updated == 1


def publish_success(
    session_factory: sessionmaker[Session],
    claim: Claim,
    *,
    columns: list[dict],
    rows: list[list],
    row_count: int,
    truncated: bool,
    duration_ms: int,
    size_bytes: int,
    retention_hours: int,
) -> bool:
    """running → succeeded + 快照，一个事务内的不可分割发布。

    generation 不匹配（执行者已失去所有权）时整体不生效并返回 False。
    """
    with session_factory() as session:
        try:
            updated = session.execute(
                text(
                    "UPDATE query_runs SET state = 'succeeded', row_count = :rc, "
                    "truncated = :tr, duration_ms = :dur, finished_at = now() "
                    "WHERE id = :id AND generation = :gen AND state = 'running'"
                ),
                {
                    "rc": row_count,
                    "tr": truncated,
                    "dur": duration_ms,
                    "id": claim.run_id,
                    "gen": claim.generation,
                },
            ).rowcount
            if updated != 1:
                session.rollback()
                return False
            session.execute(
                text(
                    "INSERT INTO query_run_snapshots "
                    "(run_id, columns, rows, row_count, truncated, size_bytes, expires_at) "
                    "VALUES (:rid, :cols, :rows, :rc, :tr, :size, "
                    "now() + make_interval(hours => :retention))"
                ),
                {
                    "rid": claim.run_id,
                    "cols": snapshot_json(columns),
                    "rows": snapshot_json(rows),
                    "rc": row_count,
                    "tr": truncated,
                    "size": size_bytes,
                    "retention": retention_hours,
                },
            )
            session.commit()
            return True
        except Exception:
            session.rollback()
            raise


def publish_failure(
    session_factory: sessionmaker[Session],
    claim: Claim,
    *,
    error_code: str,
    error_message: str,
) -> bool:
    """running → failed（终态），同样受 generation fencing 保护。"""
    with session_factory() as session:
        updated = session.execute(
            text(
                "UPDATE query_runs SET state = 'failed', error_code = :code, "
                "error_message = :message, finished_at = now() "
                "WHERE id = :id AND generation = :gen AND state = 'running'"
            ),
            {
                "id": claim.run_id,
                "gen": claim.generation,
                "code": error_code,
                "message": error_message,
            },
        ).rowcount
        session.commit()
        return updated == 1


def request_cancel(session_factory: sessionmaker[Session], run_id: int) -> CancelOutcome:
    """取消请求的状态机裁决（#12，ADR-0018）。

    queued → cancelled（终态）：取消确定生效，行不再是可执行候选；
    running → cancelling：取消事实已登记，终态发布与回队的 CAS 都要求
    state='running'，此后任何执行结果都发布不出去——「取消事实在终态
    发布前获胜时不得再发布查询结果」由 fencing 保证；执行者检测到
    cancelling 后 best effort 中止底层查询并经 finalize_cancelled 收尾。
    cancelled / cancelling 重复取消幂等；其余终态（succeeded/failed/
    rejected）不可取消。

    两条 CAS 都未命中时可能只是状态正在迁移（认领 queued→running、
    回队 running→queued）而非真的不可取消——重试裁决而不是拿瞬时
    状态定论，「排队中的取消确定生效」不受竞态影响。
    """
    with session_factory() as session:
        for _ in range(3):
            swept = session.execute(
                text(
                    "UPDATE query_runs SET state = 'cancelled', finished_at = now() "
                    "WHERE id = :id AND state = 'queued'"
                ),
                {"id": run_id},
            ).rowcount
            if swept == 1:
                session.commit()
                return "cancelled"
            swept = session.execute(
                text(
                    "UPDATE query_runs SET state = 'cancelling' "
                    "WHERE id = :id AND state IN ('running', 'cancelling')"
                ),
                {"id": run_id},
            ).rowcount
            if swept == 1:
                session.commit()
                return "cancelling"
            state = session.execute(
                text("SELECT state FROM query_runs WHERE id = :id"), {"id": run_id}
            ).scalar_one_or_none()
            if state is None or state in TERMINAL_STATES:
                session.rollback()
                if state == "cancelled":
                    return "already_cancelled"
                return "not_cancellable"
            # 非终态且两条 CAS 都未命中：状态正在迁移，回滚快照重试
            session.rollback()
        # 迁移竞态持续三轮仍未命中：按当前事实返回不可取消的稳定结论
        session.rollback()
        return "not_cancellable"


def finalize_cancelled(session_factory: sessionmaker[Session], claim: Claim) -> bool:
    """cancelling → cancelled（终态），携带 generation 做 fencing。

    执行者的查询中止/结束后调用：只有仍持有所有权（generation 未变）
    且运行仍在 cancelling 的执行者能落终态；被清扫或被接管返回 False。
    """
    with session_factory() as session:
        updated = session.execute(
            text(
                "UPDATE query_runs SET state = 'cancelled', finished_at = now() "
                "WHERE id = :id AND generation = :gen AND state = 'cancelling'"
            ),
            {"id": claim.run_id, "gen": claim.generation},
        ).rowcount
        session.commit()
        return updated == 1


def finalize_expired_cancelling(
    session_factory: sessionmaker[Session], *, grace_seconds: int = 30
) -> int:
    """清扫：cancelling 且租约过期超过宽限的运行落 cancelled 终态。

    执行者登记 cancelling 后停止续期（续期要求 running），正常路径由
    执行者自己 finalize_cancelled 收尾；执行者在收尾前崩溃则行永远停在
    cancelling——这里补上终态。宽限与耗尽清扫同语义：给执行者留收尾
    窗口，避免与还在跑的 finalize 竞争（CAS 使竞争本身无害）。幂等，
    返回清扫行数。
    """
    with session_factory() as session:
        swept = session.execute(
            text(
                "UPDATE query_runs SET state = 'cancelled', finished_at = now() "
                "WHERE state = 'cancelling' "
                "AND lease_expires_at <= now() - make_interval(secs => :grace)"
            ),
            {"grace": grace_seconds},
        ).rowcount
        session.commit()
        return swept


def purge_expired_snapshots(session_factory: sessionmaker[Session]) -> int:
    """过期清理：删除保留期已过的快照行（expires_at <= now()）。

    只删快照、不动 query_runs——审计行永久保留（CONTEXT.md「保留期」）。
    快照独立成表使清理是整行删除（ADR-0015）。幂等：重复执行对已清空
    的行集是空操作；保留期内（expires_at 在未来）的快照永不命中。
    返回本次删除行数。
    """
    with session_factory() as session:
        purged = session.execute(
            text("DELETE FROM query_run_snapshots WHERE expires_at <= now()")
        ).rowcount
        session.commit()
        return purged


def set_worker_paused(session_factory: sessionmaker[Session], *, paused: bool) -> None:
    """运维排水开关：暂停时执行者不再认领新运行（已在执行的跑完为止）。

    迁移或测试前用来安全排空队列；标记只是认领闸门，不影响已入队运行。
    """
    with session_factory() as session:
        if paused:
            session.execute(
                text(
                    "INSERT INTO system_flags (flag) VALUES (:flag) "
                    "ON CONFLICT (flag) DO NOTHING"
                ),
                {"flag": WORKER_PAUSED_FLAG},
            )
        else:
            session.execute(
                text("DELETE FROM system_flags WHERE flag = :flag"),
                {"flag": WORKER_PAUSED_FLAG},
            )
        session.commit()


def worker_paused(session_factory: sessionmaker[Session]) -> bool:
    with session_factory() as session:
        row = session.execute(
            text("SELECT 1 FROM system_flags WHERE flag = :flag LIMIT 1"),
            {"flag": WORKER_PAUSED_FLAG},
        ).fetchone()
        return row is not None
