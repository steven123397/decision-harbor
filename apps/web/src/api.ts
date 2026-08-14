export type RunState = "received" | "executing" | "succeeded" | "rejected" | "failed";

export interface QueryRunResponse {
  id: string;
  raw_sql: string | null;
  state: RunState;
  outcome: Exclude<RunState, "received" | "executing"> | null;
  created_at: string;
  policy: { decision: string; code: string | null };
  row_count: number | null;
  duration_ms: number | null;
  result: {
    columns: Array<{ name: string; type: string }>;
    rows: unknown[][];
    row_count: number;
    duration_ms: number;
  } | null;
  error: { code: string; message: string } | null;
}

export class ApiError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const apiRoot = import.meta.env.VITE_API_BASE_URL ?? "";

async function parseResponse(response: Response): Promise<Record<string, unknown>> {
  const payload = (await response.json().catch(() => ({}))) as Record<string, unknown>;
  if (!response.ok) {
    const error = (payload.error ?? {}) as { code?: string; message?: string };
    throw new ApiError(error.code ?? "http_error", error.message ?? "Request failed", response.status);
  }
  return payload;
}

export async function submitQuery(sql: string): Promise<QueryRunResponse> {
  const response = await fetch(`${apiRoot}/api/v1/query-runs`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ sql }),
  });
  return (await parseResponse(response)) as unknown as QueryRunResponse;
}

export async function checkReadiness(): Promise<boolean> {
  const response = await fetch(`${apiRoot}/ready`);
  return response.ok;
}
