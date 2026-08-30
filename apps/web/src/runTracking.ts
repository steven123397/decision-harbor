import { useCallback, useEffect, useState } from 'react'

import type { ApiClient, QueryResult, QueryRun } from './api'

/** How long the workbench waits between two reads of the same query run. */
export const POLL_INTERVAL_MS = 1000

export const RUN_ID_PARAM = 'run'

const RUN_ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

const TERMINAL_STATUSES = new Set<QueryRun['status']>(['rejected', 'succeeded', 'failed', 'cancelled'])

/** Why a succeeded run has no readable snapshot. */
export type ResultProblem = 'result_expired' | 'result_unavailable'

export type TrackedRun =
  | { kind: 'idle' }
  | { kind: 'restoring'; runId: string; reconnecting: boolean }
  | { kind: 'pending'; run: QueryRun; reconnecting: boolean }
  | { kind: 'success'; run: QueryRun; result: QueryResult | null; problem: ResultProblem | null }
  | { kind: 'terminal'; run: QueryRun }
  | { kind: 'missing'; runId: string }

export function isTerminal(run: QueryRun): boolean {
  return TERMINAL_STATUSES.has(run.status)
}

/** The run a tracked state is showing facts for, when it has read one. */
export function trackedRun(state: TrackedRun): QueryRun | null {
  return state.kind === 'pending' || state.kind === 'success' || state.kind === 'terminal' ? state.run : null
}

/** The run identifier the page address remembers, if it carries a usable one. */
export function readTrackedRunId(): string | null {
  const value = new URLSearchParams(window.location.search).get(RUN_ID_PARAM)
  return value !== null && RUN_ID_PATTERN.test(value) ? value : null
}

/** Remember the run identifier so a reload resumes the same query run. */
export function rememberTrackedRunId(runId: string): void {
  const url = new URL(window.location.href)
  url.searchParams.set(RUN_ID_PARAM, runId)
  window.history.replaceState(null, '', url)
}

function startingPoint(tracked: { runId: string; run: QueryRun | null }): TrackedRun {
  if (tracked.run === null) return { kind: 'restoring', runId: tracked.runId, reconnecting: false }
  if (isTerminal(tracked.run)) return { kind: 'terminal', run: tracked.run }
  return { kind: 'pending', run: tracked.run, reconnecting: false }
}

/**
 * Follow one query run until it stops moving.
 *
 * The run is polled, never streamed. Polling starts as soon as a run is
 * tracked, and stops at a terminal state, when the component unmounts, and on
 * a failure no later read can undo. A successful run is followed by a single
 * read of its result snapshot.
 */
export function useTrackedRun(api: ApiClient) {
  const [tracked, setTracked] = useState<{ runId: string; run: QueryRun | null } | null>(null)
  const [progress, setProgress] = useState<TrackedRun>({ kind: 'idle' })

  useEffect(() => {
    if (tracked === null) return
    const { runId, run: knownRun } = tracked
    let stopped = false
    let timer: ReturnType<typeof setTimeout> | null = null
    let inFlight = false

    const readResult = async (run: QueryRun): Promise<boolean> => {
      const response = await api.getResult(run.id)
      if (stopped) return false
      const result = response.data?.result ?? null
      const code = response.error?.code
      // A run that no longer exists will never have a result either.
      if (result === null && code === 'query_run_not_found') {
        setProgress({ kind: 'missing', runId: run.id })
        return false
      }
      // Only a succeeded run ever carried a snapshot, so these two codes say
      // what happened to it: waiting cannot bring either one back.
      if (result === null && (code === 'result_expired' || code === 'result_unavailable')) {
        setProgress({ kind: 'success', run, result: null, problem: code })
        return false
      }
      setProgress({ kind: 'success', run, result, problem: null })
      // A result that is not readable yet is still worth waiting for.
      return result === null
    }

    const settle = async (run: QueryRun): Promise<boolean> => {
      if (run.status === 'succeeded') return await readResult(run)
      setProgress({ kind: 'terminal', run })
      return false
    }

    const poll = async (): Promise<boolean> => {
      const response = await api.getRun(runId)
      if (stopped) return false
      const run = response.data?.query_run ?? null
      if (run === null) {
        if (response.error?.code === 'query_run_not_found') {
          setProgress({ kind: 'missing', runId })
          return false
        }
        // Keep the last known run on screen: a read that failed is not a fact
        // about the run, it is only a gap in the polling.
        setProgress((previous) =>
          previous.kind === 'pending' || previous.kind === 'restoring'
            ? { ...previous, reconnecting: true }
            : previous,
        )
        return true
      }
      if (!isTerminal(run)) {
        setProgress({ kind: 'pending', run, reconnecting: false })
        return true
      }
      return await settle(run)
    }

    const tick = async (): Promise<void> => {
      // One request at a time: the next poll is only scheduled once the
      // previous one has settled, so a slow response cannot pile up requests.
      if (inFlight) return
      inFlight = true
      try {
        const again = await poll()
        if (!stopped && again) timer = setTimeout(tick, POLL_INTERVAL_MS)
      } finally {
        inFlight = false
      }
    }

    if (knownRun !== null && isTerminal(knownRun)) void settle(knownRun)
    else void tick()

    return () => {
      stopped = true
      if (timer !== null) clearTimeout(timer)
    }
  }, [api, tracked])

  const track = useCallback((runId: string, run: QueryRun | null = null) => {
    const next = { runId, run }
    setProgress(startingPoint(next))
    setTracked(next)
  }, [])

  return { state: progress, track }
}
