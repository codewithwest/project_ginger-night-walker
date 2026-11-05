import {
  Activity,
  AlertCircle,
  ArrowUpRight,
  Bot,
  CheckCircle2,
  Clock3,
  Database,
  LoaderCircle,
  Play,
  RefreshCw,
  Send,
  ShieldCheck,
  Square,
  Terminal,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";

type Log = {
  at?: string;
  level?: string;
  message?: string;
};

type Task = {
  task_id: string;
  objective: string;
  capability?: string;
  status: string;
  created_at?: string;
  updated_at?: string;
  site_name?: string;
  current_step?: string;
  target_url?: string;
  plan?: { clarification_question?: string; [key: string]: unknown };
  step_results?: unknown;
  result?: unknown;
  error?: string;
  logs?: Log[];
};

const terminal = (status: string) =>
  ["planned", "blocked", "completed", "failed", "stopped", "needs_clarification"].includes(
    status,
  );

const stamp = (value?: string) =>
  value ? new Date(value).toLocaleTimeString() : "—";

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
  const data = await response.json().catch(() => ({}));

  if (!response.ok) {
    throw new Error(data.detail || response.statusText);
  }

  return data as T;
}

function Pill({ status }: { status: string }) {
  const tone = ["planned", "completed"].includes(status)
    ? "good"
    : ["failed", "blocked"].includes(status)
      ? "bad"
      : ["needs_clarification", "stopped"].includes(status)
        ? "warn"
        : "";

  return (
    <span className={`pill ${tone}`}>
      {status.replaceAll("_", " ")}
    </span>
  );
}

export default function App() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [objective, setObjective] = useState("");
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [online, setOnline] = useState(false);

  const selected = useMemo(
    () => tasks.find((task) => task.task_id === selectedId) || null,
    [tasks, selectedId],
  );

  const refresh = useCallback(async () => {
    try {
      await api("/health");
      const data = await api<{ tasks: Task[] }>("/tasks");
      setTasks(data.tasks || []);
      setOnline(true);
      setSelectedId((current) =>
        current && data.tasks.some((task) => task.task_id === current)
          ? current
          : (data.tasks[0]?.task_id ?? null),
      );
    } catch {
      setOnline(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const intervalId = window.setInterval(() => void refresh(), 2500);
    return () => window.clearInterval(intervalId);
  }, [refresh]);

  useEffect(() => {
    setAnswer("");
    setError("");
  }, [selectedId]);

  async function launch(event: FormEvent) {
    event.preventDefault();
    if (!objective.trim() || busy) return;

    setBusy(true);
    setError("");

    try {
      const task = await api<Task>("/tasks", {
        method: "POST",
        body: JSON.stringify({ objective: objective.trim() }),
      });
      setObjective("");
      setSelectedId(task.task_id);
      await refresh();
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Task submission failed",
      );
    } finally {
      setBusy(false);
    }
  }

  async function clarify(event: FormEvent) {
    event.preventDefault();
    if (!selected || !answer.trim() || sending) return;

    setSending(true);
    setError("");

    try {
      await api(`/tasks/${encodeURIComponent(selected.task_id)}/clarify`, {
        method: "POST",
        body: JSON.stringify({ answer: answer.trim() }),
      });
      setAnswer("");
      await refresh();
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : "Clarification failed",
      );
    } finally {
      setSending(false);
    }
  }

  async function stop() {
    if (!selected) return;

    try {
      await api(`/tasks/${encodeURIComponent(selected.task_id)}`, {
        method: "DELETE",
      });
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Stop failed");
    }
  }

  const running = tasks.filter((task) =>
    ["queued", "running"].includes(task.status),
  ).length;
  const done = tasks.filter((task) =>
    ["planned", "completed"].includes(task.status),
  ).length;
  const attention = tasks.filter((task) =>
    ["failed", "blocked", "needs_clarification"].includes(task.status),
  ).length;

  return (
    <>
      <header className="topbar">
        <div>
          <div className="brand">
            GINGER <b>NIGHT WALKER</b>
          </div>
          <div className="sub">OPERATOR CONTROL CENTRE</div>
        </div>
        <div className="conn">
          {online ? "● API CONNECTED" : "● API DISCONNECTED"} · LOCAL INSTANCE
        </div>
      </header>

      <main className="shell">
        <section className="intro">
          <div>
            <div className="eyebrow">
              <ShieldCheck size={13} style={{ verticalAlign: "middle" }} />{" "}
              LEAST-PRIVILEGE AGENT RUNTIME
            </div>
            <h1>
              Mission control<span>.</span>
            </h1>
            <p>
              Give Night Walker an objective. It plans the work, requests
              clarification when needed, and reports evidence.
            </p>
          </div>
          <Bot size={48} color="#a5e6a1" />
        </section>

        <section className="stats">
          <div className="stat">
            <small>TOTAL TASKS</small>
            <strong>{tasks.length}</strong>
          </div>
          <div className="stat">
            <small>RUNNING NOW</small>
            <strong>{running}</strong>
          </div>
          <div className="stat">
            <small>PLANNED / DONE</small>
            <strong>{done}</strong>
          </div>
          <div className="stat">
            <small>NEEDS ATTENTION</small>
            <strong>{attention}</strong>
          </div>
        </section>

        <div className="layout">
          <aside className="panel">
            <div className="heading">
              <span className="idx">01 / INPUT</span>
              <h2>New mission</h2>
            </div>

            <form className="pad" onSubmit={launch}>
              <label htmlFor="objective">MISSION OBJECTIVE</label>
              <textarea
                id="objective"
                value={objective}
                onChange={(event) => setObjective(event.target.value)}
                maxLength={2000}
                required
                placeholder="Find the first product and its price on weylandts.co.za…"
              />
              <div className="meta">
                {objective.length}/2000 CHARACTERS
              </div>
              <button
                className="primary"
                disabled={busy || !objective.trim()}
              >
                {busy ? <LoaderCircle size={15} /> : <Play size={15} />}
                {busy ? "DECIDER WORKING" : "LAUNCH MISSION"}
                <ArrowUpRight size={14} />
              </button>
            </form>

            <div className="note">
              <ShieldCheck size={15} style={{ verticalAlign: "middle" }} />{" "}
              <b>READ-ONLY BROWSER</b>
              <br />
              Isolated Playwright session. Navigation and visible-content
              extraction only; no purchases or form submissions.
            </div>
          </aside>

          <section className="panel">
            <div className="heading monitor-head">
              <div>
                <span className="idx">02 / OPERATIONS</span>
                <h2>Task monitor</h2>
              </div>
              <button className="stop" onClick={() => void refresh()}>
                <RefreshCw size={13} /> REFRESH
              </button>
            </div>

            <div className="monitor-body">
              <nav className="tasks">
                {tasks.length ? (
                  tasks.map((task) => (
                    <button
                      key={task.task_id}
                      className={
                        "task " +
                        (selectedId === task.task_id ? "active" : "")
                      }
                      onClick={() => setSelectedId(task.task_id)}
                    >
                      <div className="meta">
                        {(task.capability || "AUTO") +
                          " · " +
                          stamp(task.created_at)}
                      </div>
                      <Pill status={task.status} />
                      <p>{task.objective}</p>
                      <div className="muted">{task.task_id}</div>
                    </button>
                  ))
                ) : (
                  <div className="detail">No missions yet.</div>
                )}
              </nav>

              <article className="detail">
                {selected ? (
                  <>
                    <button
                      className="stop"
                      disabled={terminal(selected.status)}
                      onClick={() => void stop()}
                    >
                      <Square size={11} /> STOP
                    </button>
                    <span className="idx">MISSION DETAIL</span>
                    <h3>{selected.objective}</h3>
                    <div className="muted">
                      {selected.task_id} · Updated {stamp(selected.updated_at)}
                    </div>
                    <Pill status={selected.status} />

                    {selected.status === "needs_clarification" && (
                      <form className="clarify" onSubmit={clarify}>
                        <strong>
                          <AlertCircle
                            size={15}
                            style={{ verticalAlign: "middle" }}
                          />{" "}
                          OPERATOR INPUT REQUIRED
                        </strong>
                        <p>
                          {selected.plan?.clarification_question ||
                            "Please clarify the website or detail needed to continue."}
                        </p>
                        <label htmlFor="clarification-answer">
                          YOUR RESPONSE
                        </label>
                        <textarea
                          id="clarification-answer"
                          value={answer}
                          onChange={(event) => setAnswer(event.target.value)}
                          maxLength={2000}
                          required
                          placeholder="Type your answer here. Polling will not interrupt your typing."
                        />
                        <div className="meta">{answer.length}/2000</div>
                        <button
                          className="send"
                          disabled={sending || !answer.trim()}
                        >
                          {sending ? (
                            <LoaderCircle size={14} />
                          ) : (
                            <Send size={14} />
                          )}
                          SEND &amp; RESUME
                        </button>
                      </form>
                    )}

                    <div className="meta" style={{ marginTop: 16 }}>
                      <Terminal
                        size={13}
                        style={{ verticalAlign: "middle" }}
                      />{" "}
                      EXECUTION LOG · LIVE POLLING
                    </div>
                    <div className="logs">
                      {(selected.logs || []).map((log, index) => (
                        <div
                          className="log"
                          key={(log.at || "") + index}
                        >
                          <time>{stamp(log.at)}</time>
                          <b>{log.level || "info"}</b> · {log.message}
                        </div>
                      ))}
                    </div>

                    <details className="evidence">
                      <summary>
                        <Database
                          size={13}
                          style={{ verticalAlign: "middle" }}
                        />{" "}
                        PLAN / BROWSER OUTPUT / EVIDENCE
                      </summary>
                      <pre>
                        {JSON.stringify(
                          {
                            current_step: selected.current_step,
                            site_name: selected.site_name,
                            target_url: selected.target_url,
                            plan: selected.plan,
                            step_results: selected.step_results,
                            result: selected.result,
                            error: selected.error,
                          },
                          null,
                          2,
                        )}
                      </pre>
                    </details>
                  </>
                ) : (
                  <div className="detail">
                    Select a mission to inspect its plan, logs and evidence.
                  </div>
                )}

                {error && (
                  <div className="error">
                    <AlertCircle size={13} /> {error}
                  </div>
                )}
              </article>
            </div>
          </section>
        </div>

        <footer className="footer">
          GINGER NIGHT WALKER · CONTROL SYSTEM
          <span>LOCAL-FIRST / POLICY-GOVERNED / EVIDENCE-LED</span>
        </footer>
      </main>
    </>
  );
}
