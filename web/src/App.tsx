import { FormEvent, useState } from "react";
import type { QueryRun } from "./types";

type ViewState =
  | { kind: "idle" }
  | { kind: "running" }
  | { kind: "done"; run: QueryRun }
  | { kind: "transport"; message: string };

export function App() {
  const [sql, setSql] = useState("SELECT id, region FROM customers ORDER BY id LIMIT 5");
  const [view, setView] = useState<ViewState>({ kind: "idle" });

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setView({ kind: "running" });
    try {
      const response = await fetch("/api/v1/query-runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sql }),
      });
      const body = (await response.json()) as QueryRun | { error?: { message?: string } };
      if (!response.ok || !("status" in body)) {
        const message =
          "error" in body && body.error?.message ? body.error.message : "Request failed.";
        setView({ kind: "transport", message });
        return;
      }
      setView({ kind: "done", run: body });
    } catch (error) {
      setView({
        kind: "transport",
        message: error instanceof Error ? error.message : "Request failed.",
      });
    }
  }

  const running = view.kind === "running";

  return (
    <main>
      <h1>查询工作台</h1>
      <form onSubmit={onSubmit}>
        <label htmlFor="sql">SQL</label>
        <textarea
          id="sql"
          name="sql"
          value={sql}
          onChange={(event) => setSql(event.target.value)}
          disabled={running}
        />
        <button type="submit" disabled={running}>
          提交
        </button>
      </form>
      {running ? <p role="status">执行中</p> : null}
      {view.kind === "done" && view.run.status === "succeeded" && view.run.result ? (
        <section>
          <p role="status">已完成</p>
          <table>
            <thead>
              <tr>
                {view.run.result.columns.map((column) => (
                  <th key={column.name}>{column.name}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {view.run.result.rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {row.map((cell, cellIndex) => (
                    <td key={cellIndex}>{cell == null ? "" : String(cell)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}
      {view.kind === "done" && view.run.error ? (
        <p className="error" role="alert">
          {view.run.error.code}: {view.run.error.message}
        </p>
      ) : null}
      {view.kind === "transport" ? (
        <p className="error" role="alert">
          {view.message}
        </p>
      ) : null}
    </main>
  );
}
