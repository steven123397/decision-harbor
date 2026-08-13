export type QueryStatus = "running" | "succeeded" | "rejected" | "failed";

export type QueryRun = {
  id: string;
  status: QueryStatus;
  sql: string;
  result: {
    columns: { name: string; type: string }[];
    rows: unknown[][];
    row_count: number;
  } | null;
  error: { code: string; message: string } | null;
  duration_ms: number | null;
  created_at: string;
};
