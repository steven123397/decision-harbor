export type QueryColumn = { name: string; type: string };

export type QueryEnvelope = {
  status: string;
  id: string | null;
  data: {
    columns?: QueryColumn[];
    rows?: unknown[][];
    row_count?: number | null;
    duration_ms?: number | null;
    sql_text?: string;
    error?: { code: string; message: string } | null;
  } | null;
  error: { code: string; message: string } | null;
};

const API_BASE =
  (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, "") ||
  "http://localhost:8000";

export function getApiBase(): string {
  return API_BASE;
}

export async function submitQuery(sql: string): Promise<QueryEnvelope> {
  const res = await fetch(`${API_BASE}/api/v1/query-runs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sql }),
  });
  const body = (await res.json()) as QueryEnvelope;
  if (!res.ok && res.status !== 200) {
    throw new Error(body.error?.message || `HTTP ${res.status}`);
  }
  return body;
}
