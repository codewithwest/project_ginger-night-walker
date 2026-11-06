import {
  ArrowDownRight,
  ArrowUpRight,
  Check,
  CircleAlert,
  ExternalLink,
  FileSearch,
  LoaderCircle,
  ListChecks,
  Play,
  Plus,
  RefreshCw,
  Send,
  ShieldCheck,
  Square,
  Terminal,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";

type Log = {
  at?: string;
  level?: string;
  message?: string;
};

type Product = {
  name: string;
  price: string;
};

type BrowserResult = {
  schema_version?: string;
  status?: string;
  final_url?: string;
  title?: string;
  text?: string;
  answer?: string | null;
  products?: Product[];
  evidence_id?: string;
  issues?: string[];
  findings?: { claim: string; type?: string; confidence?: string }[];
  sources?: { url: string; title?: string; retrieved_at?: string }[];
  evidence?: { evidence_id?: string; source_url?: string; captured_text?: string; visited_urls?: string[] }[];
  structured_data?: Record<string, unknown>;
  limitations?: string[];
  suggested_memory?: string[];
  goal_state?: { original_goal: string; criteria: string[]; completed: string[]; remaining: string[]; evidence_by_criterion: Record<string, { evidence_id: string; quote: string }> };
};

type Task = {
  task_id: string;
  objective: string;
  capability?: string;
  status: string;
  created_at?: string;
  updated_at?: string;
  site_name?: string;
  current_step?: string | null;
  target_url?: string;
  plan?: { clarification_question?: string; [key: string]: unknown };
  step_results?: unknown;
  result?: BrowserResult | null;
  error?: string;
  logs?: Log[];
  progress_events?: { at?: string; event_type?: string; status?: string; message?: string }[];
  round?: number;
  max_rounds?: number;
  scratchpad?: { round?: number; assignment?: string; status?: string; findings?: unknown[]; evidence_ids?: string[] }[];
  goal_state?: { original_goal: string; criteria: string[]; completed: string[]; remaining: string[]; evidence_by_criterion: Record<string, { evidence_id: string; quote: string }> } | null;
  checklist_markdown?: string;
};

const isTerminal = (status: string) =>
  ["planned", "blocked", "completed", "failed", "stopped"].includes(
    status,
  );

const formatTime = (value?: string) =>
  value
    ? new Date(value).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
      })
    : "—";

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

function Status({ value }: { value: string }) {
  const kind = ["completed", "planned"].includes(value)
    ? "success"
    : ["failed", "blocked"].includes(value)
      ? "danger"
      : ["needs_clarification", "stopped"].includes(value)
        ? "warning"
        : "active";

  return (
    <span className={`status status--${kind}`}>
      <span className="status__dot" />
      {value.replaceAll("_", " ")}
    </span>
  );
}

function ResearchReport({ result }: { result: BrowserResult }) {
  const findings = result.findings || [];
  const sources = result.sources || (result.final_url ? [{ url: result.final_url, title: result.title }] : []);
  const structuredData = result.structured_data || {};
  const limitations = result.limitations || result.issues || [];
  return (
    <section className="answer-panel research-report">
      <div className="answer-panel__topline">
        <span className="eyebrow"><Check size={13} /> RESEARCH REPORT</span>
        <span className="result-count">{findings.length} findings · {sources.length} sources{result.schema_version ? " · schema " + result.schema_version : ""}</span>
      </div>
      <div className="research-report__answer">
        <span className="answer-highlight__label">ANSWER</span>
        <h2>{result.answer || "The page was captured. Review the findings and source evidence below."}</h2>
      </div>
      {result.goal_state && <div className="research-section">
        <h3>Goal coverage · {result.goal_state.completed.length}/{result.goal_state.criteria.length} verified</h3>
        <ul className="finding-list">{result.goal_state.criteria.map((criterion) => {
          const proof = result.goal_state?.evidence_by_criterion[criterion];
          return <li key={criterion}>
            <span>{criterion}</span>
            <small>{proof ? "Verified · " + proof.evidence_id : "Evidence still required"}</small>
            {proof && <blockquote>{proof.quote}</blockquote>}
          </li>;
        })}</ul>
      </div>}
      {findings.length > 0 && <div className="research-section">
        <h3>Findings</h3>
        <ul className="finding-list">{findings.map((finding, index) => <li key={index}>
          <span>{finding.claim}</span><small>{finding.type?.replaceAll("_", " ") || "observation"} · {finding.confidence || "observed"}</small>
        </li>)}</ul>
      </div>}
      {Object.entries(structuredData).map(([kind, value]) => (
        <div className="research-section" key={kind}>
          <h3>Structured data · {kind.replaceAll("_", " ")}</h3>
          {kind === "products" && Array.isArray(value) ? (
            <div className="product-list">{(value as Product[]).map((product, index) => (
              <div className="product-row" key={product.name + "-" + index}>
                <span className="product-row__number">{String(index + 1).padStart(2, "0")}</span>
                <span className="product-row__name">{product.name}</span>
                <strong className="product-row__price">{product.price}</strong>
              </div>
            ))}</div>
          ) : (
            <pre className="structured-data">{JSON.stringify(value, null, 2)}</pre>
          )}
        </div>
      ))}
      {sources.length > 0 && <div className="research-section">
        <h3>Sources</h3>
        {sources.map((source, index) => <a className="source-link" href={source.url} target="_blank" rel="noreferrer" key={source.url + index}>
          <ExternalLink size={14} />{source.title || new URL(source.url).hostname}<span>{source.retrieved_at ? new Date(source.retrieved_at).toLocaleString() : new URL(source.url).hostname}</span>
        </a>)}
      </div>}
      {result.evidence && result.evidence.length > 0 && <div className="research-section">
        <h3>Captured evidence</h3>
        {result.evidence.map((item, index) => <details className="raw-data" key={item.evidence_id || index}>
          <summary>{item.evidence_id || "Browser capture"} · {item.source_url || "Source"}</summary>
          {item.visited_urls && <p>{item.visited_urls.join(" → ")}</p>}
          <pre>{item.captured_text || "No text capture available."}</pre>
        </details>)}
      </div>}
      {limitations.length > 0 && <div className="research-section research-section--limitations">
        <h3>Limitations</h3><ul>{limitations.map((item, index) => <li key={index}>{item}</li>)}</ul>
      </div>}
      {result.suggested_memory && result.suggested_memory.length > 0 && <div className="research-section">
        <h3>Potential knowledge to retain</h3><ul>{result.suggested_memory.map((item, index) => <li key={index}>{item}</li>)}</ul>
        <small>Suggestions only — Ginger's memory policy decides what is retained.</small>
      </div>}
    </section>
  );
}

export default function App() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [objective, setObjective] = useState("");
  const [answer, setAnswer] = useState("");
  const [followUp, setFollowUp] = useState("");
  const [sendingFollowUp, setSendingFollowUp] = useState(false);
  const [busy, setBusy] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [online, setOnline] = useState(false);
  const [checklistPinned, setChecklistPinned] = useState(false);
  const [checklistDismissed, setChecklistDismissed] = useState(false);

  const selected = useMemo(
    () => tasks.find((task) => task.task_id === selectedId) || null,
    [tasks, selectedId],
  );

  // The workspace list is loaded on entry and refreshed only on explicit user
  // actions. Background polling is scoped to the one task currently opened.
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

  const refreshSelected = useCallback(async (taskId: string) => {
    try {
      const task = await api<Task>(`/tasks/${encodeURIComponent(taskId)}`);
      setTasks((current) => current.map((item) => item.task_id === taskId ? task : item));
      setOnline(true);
    } catch {
      setOnline(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!selectedId) return;
    let active = true;
    const poll = async () => {
      try {
        const task = await api<Task>(`/tasks/${encodeURIComponent(selectedId)}`);
        if (active) {
          setTasks((current) => current.map((item) => item.task_id === selectedId ? task : item));
          setOnline(true);
        }
      } catch {
        if (active) setOnline(false);
      }
    };
    void poll();
    const intervalId = window.setInterval(() => void poll(), 2500);
    return () => {
      active = false;
      window.clearInterval(intervalId);
    };
  }, [selectedId]);

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
      setError(cause instanceof Error ? cause.message : "Task submission failed");
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
      setError(cause instanceof Error ? cause.message : "Clarification failed");
    } finally {
      setSending(false);
    }
  }

  async function sendFollowUp(event: FormEvent) {
    event.preventDefault();
    if (!selected || !followUp.trim() || sendingFollowUp) return;
    setSendingFollowUp(true);
    try {
      await api("/tasks/" + encodeURIComponent(selected.task_id) + "/follow-up", { method: "POST", body: JSON.stringify({ message: followUp.trim() }) });
      setFollowUp("");
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Follow-up failed");
    } finally { setSendingFollowUp(false); }
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
  const completed = tasks.filter((task) =>
    ["planned", "completed"].includes(task.status),
  ).length;
  const attention = tasks.filter((task) =>
    ["failed", "blocked", "needs_clarification"].includes(task.status),
  ).length;

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="wordmark" href="/" aria-label="Ginger Night Walker home">
          <span className="wordmark__mark">g.</span>
          <span>
            <strong>GINGER</strong>
            <small>NIGHT WALKER</small>
          </span>
        </a>

        <div className="topbar__right">
          <span className={`connection ${online ? "is-online" : ""}`}>
            <span />
            {online ? "SYSTEM ONLINE" : "CONNECTING"}
          </span>
          <span className="topbar__divider" />
          <span className="topbar__local">LOCAL INSTANCE</span>
        </div>
      </header>

      <main className="workspace">
        <section className="welcome">
          <div>
            <div className="eyebrow">PERSONAL AUTOMATION, WITH GUARDRAILS</div>
            <h1>Make it happen.</h1>
            <p>
              Set an objective. Night Walker plans the steps, works within its
              permissions, and brings back the result.
            </p>
          </div>
          <div className="welcome__aside">
            <ShieldCheck size={18} />
            <span>PRIVATE BY DESIGN</span>
          </div>
        </section>

        <section className="overview" aria-label="Task overview">
          <div className="overview__item">
            <span>ALL MISSIONS</span>
            <strong>{tasks.length.toString().padStart(2, "0")}</strong>
          </div>
          <div className="overview__item">
            <span>IN PROGRESS</span>
            <strong>{running.toString().padStart(2, "0")}</strong>
          </div>
          <div className="overview__item">
            <span>COMPLETED</span>
            <strong>{completed.toString().padStart(2, "0")}</strong>
          </div>
          <div className="overview__item">
            <span>NEEDS ATTENTION</span>
            <strong>{attention.toString().padStart(2, "0")}</strong>
          </div>
        </section>

        <div className="main-grid">
          <aside className="left-column">
            <section className="compose-card">
              <div className="section-kicker"><Plus size={14} /> NEW MISSION</div>
              <h2>What would you like done?</h2>
              <p className="card-intro">
                Describe the outcome. You don’t need to write the steps.
              </p>
              <form onSubmit={launch}>
                <label className="sr-only" htmlFor="objective">
                  Mission objective
                </label>
                <textarea
                  id="objective"
                  value={objective}
                  onChange={(event) => setObjective(event.target.value)}
                  maxLength={2000}
                  required
                  placeholder="e.g. Find the first product and its price on weylandts.co.za"
                />
                <div className="compose-card__footer">
                  <span>{objective.length}/2000</span>
                  <button
                    className="launch-button"
                    disabled={busy || !objective.trim()}
                  >
                    {busy ? <LoaderCircle size={16} /> : <Play size={15} />}
                    {busy ? "Starting…" : "Run mission"}
                    <ArrowUpRight size={15} />
                  </button>
                </div>
              </form>
            </section>

            <section className="mission-list">
              <div className="mission-list__header">
                <div>
                  <div className="section-kicker">WORKSPACE</div>
                  <h2>Your missions</h2>
                </div>
                <button
                  className="icon-button"
                  onClick={() => void refresh()}
                  aria-label="Refresh missions"
                  title="Refresh"
                >
                  <RefreshCw size={15} />
                </button>
              </div>

              {tasks.length === 0 ? (
                <div className="empty-list">
                  <FileSearch size={22} />
                  <p>Your missions will appear here.</p>
                </div>
              ) : (
                <div className="mission-items">
                  {tasks.map((task) => (
                    <button
                      key={task.task_id}
                      className={
                        "mission-item " +
                        (selectedId === task.task_id ? "is-selected" : "")
                      }
                      onClick={() => setSelectedId(task.task_id)}
                    >
                      <span className="mission-item__top">
                        <Status value={task.status} />
                        <time>{formatTime(task.created_at)}</time>
                      </span>
                      <strong>{task.objective}</strong>
                      <span className="mission-item__id">
                        {task.task_id.slice(0, 18)}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </section>
          </aside>

          <section className="result-column">
            {selected ? (
              <>
                <div className="result-header">
                  <div>
                    <div className="section-kicker">MISSION REPORT</div>
                    <h2>{selected.objective}</h2>
                    <div className="result-header__meta">
                      <span>{selected.site_name || "Website task"}</span>
                      <span>·</span>
                      <span>{formatTime(selected.updated_at)}</span>
                    </div>
                  </div>
                  <div className="result-header__actions">
                    <Status value={selected.status} />
                    {!isTerminal(selected.status) && (
                      <button className="stop-button" onClick={() => void stop()}>
                        <Square size={12} /> Stop
                      </button>
                    )}
                  </div>
                </div>

                {selected.status === "needs_clarification" && (
                  <form className="clarification" onSubmit={clarify}>
                    <div className="section-kicker">
                      <CircleAlert size={14} /> A QUICK QUESTION
                    </div>
                    <p>
                      {selected.plan?.clarification_question ||
                        "Please clarify the website or detail needed to continue."}
                    </p>
                    <label htmlFor="clarification-answer">Your response</label>
                    <textarea
                      id="clarification-answer"
                      value={answer}
                      onChange={(event) => setAnswer(event.target.value)}
                      maxLength={2000}
                      required
                      placeholder="Type your answer…"
                    />
                    <button className="launch-button" disabled={sending || !answer.trim()}>
                      {sending ? <LoaderCircle size={15} /> : <Send size={15} />}
                      Send answer
                    </button>
                  </form>
                )}

                {selected.result && ["completed", "blocked", "stopped", "failed"].includes(selected.status) && (
                  <ResearchReport result={selected.result} />
                )}

                {selected.goal_state && (selected.status === "running" || selected.status === "queued") && (
                  <section className="working-panel">
                    <div><strong>Goal coverage · {selected.goal_state.completed.length}/{selected.goal_state.criteria.length}</strong>
                      <p>{selected.goal_state.remaining.length ? "Still to verify: " + selected.goal_state.remaining.join(" · ") : "All success criteria have evidence."}</p>
                    </div>
                  </section>
                )}
                {selected.status === "running" || selected.status === "queued" ? (
                  <section className="working-panel">
                    <div className="working-panel__icon"><LoaderCircle size={20} /></div>
                    <div>
                      <strong>Working on it</strong>
                      <p>Round {selected.round || 0} · configured ceiling {selected.max_rounds || 30}. Progress is saved as work resolves.</p>
                      {(selected.progress_events || []).slice(-4).map((event, index) => (
                        <p key={(event.at || "") + index}>{event.message}</p>
                      ))}
                    </div>
                    <form onSubmit={sendFollowUp} className="clarification">
                      <label htmlFor="follow-up-message">Add an instruction for the next research rounds</label>
                      <textarea id="follow-up-message" value={followUp} onChange={(event) => setFollowUp(event.target.value)} maxLength={2000} placeholder="Refine the research…" />
                      <button className="launch-button" disabled={sendingFollowUp || !followUp.trim()}>
                        {sendingFollowUp ? <LoaderCircle size={15} /> : <Send size={15} />}
                        Add instruction
                      </button>
                    </form>
                  </section>
                ) : null}

                {selected.error && (
                  <div className="error-banner">
                    <CircleAlert size={16} />
                    <span>{selected.error}</span>
                  </div>
                )}

                {selected.checklist_markdown && (
                  <details className="report-details" open>
                    <summary>
                      <span><Check size={15} /> Goal &amp; progress checklist</span>
                      <ArrowDownRight size={15} />
                    </summary>
                    <div className="report-details__body">
                      <pre className="structured-data">{selected.checklist_markdown}</pre>
                    </div>
                  </details>
                )}

                <details className="report-details">
                  <summary>
                    <span><Terminal size={15} /> Execution details &amp; evidence</span>
                    <ArrowDownRight size={15} />
                  </summary>
                  <div className="report-details__body">
                    <div className="detail-meta">
                      <span>Target</span>
                      {selected.target_url ? (
                        <a href={selected.target_url} target="_blank" rel="noreferrer">
                          {selected.target_url} <ExternalLink size={12} />
                        </a>
                      ) : <span>Not specified</span>}
                    </div>
                    {selected.result?.evidence_id && (
                      <div className="detail-meta">
                        <span>Evidence ID</span>
                        <code>{selected.result.evidence_id}</code>
                      </div>
                    )}
                    {selected.round !== undefined && (
                      <div className="detail-meta">
                        <span>Execution phase / budget</span>
                        <span>{selected.round} / {selected.max_rounds || 30}</span>
                      </div>
                    )}
                    {selected.scratchpad?.length ? (
                      <div className="log-list">
                        {selected.scratchpad.map((note, index) => (
                          <div className="log-entry" key={"scratch-" + index}>
                            <time>R{note.round ?? "—"}</time>
                            <span className="log-entry__level">scratchpad</span>
                            <p>{note.assignment} · {note.status}</p>
                          </div>
                        ))}
                      </div>
                    ) : null}
                    {selected.logs?.length ? (
                      <div className="log-list">
                        {selected.logs.map((log, index) => (
                          <div className="log-entry" key={(log.at || "") + index}>
                            <time>{formatTime(log.at)}</time>
                            <span className={`log-entry__level log-entry__level--${log.level || "info"}`}>
                              {log.level || "info"}
                            </span>
                            <p>{log.message}</p>
                          </div>
                        ))}
                      </div>
                    ) : null}
                    <details className="raw-data">
                      <summary>View raw browser data</summary>
                      <pre>{JSON.stringify(selected.result || selected.step_results || selected.plan, null, 2)}</pre>
                    </details>
                  </div>
                </details>
              </>
            ) : (
              <div className="report-empty">
                <div className="report-empty__icon"><FileSearch size={26} /></div>
                <div className="section-kicker">YOUR RESULTS, IN ONE PLACE</div>
                <h2>Ready when you are.</h2>
                <p>Launch a mission and its answer, sources, and execution details will show up here.</p>
              </div>
            )}

            {error && <div className="error-banner"><CircleAlert size={15} />{error}</div>}
          </section>
        </div>

        <div className={`floating-checklist ${selected && ["queued", "running"].includes(selected.status) ? "is-running" : ""} ${checklistPinned ? "is-pinned" : ""} ${checklistDismissed ? "is-dismissed" : ""}`}>
          <button
            type="button"
            className="floating-checklist__trigger"
            aria-label={selected ? `Open checklist for ${selected.objective}` : "Open task checklist"}
            aria-expanded={checklistPinned}
            onClick={() => {
              setChecklistDismissed(false);
              setChecklistPinned((current) => !current);
            }}
          >
            <span className="floating-checklist__trigger-icon"><ListChecks size={21} /></span>
            {selected?.goal_state && (
              <span className="floating-checklist__trigger-count">
                {selected.goal_state.completed.length}/{selected.goal_state.criteria.length}
              </span>
            )}
            <span className="floating-checklist__trigger-label">Task checklist</span>
            {selected && ["queued", "running"].includes(selected.status) && (
              <span className="floating-checklist__live-dot" aria-label="Model running" />
            )}
          </button>

          <section className="floating-checklist__panel" aria-label="Current task checklist">
            <div className="floating-checklist__header">
              <div>
                <span className="section-kicker"><ListChecks size={13} /> CURRENT TASK</span>
                <strong>{selected ? selected.objective : "No task selected"}</strong>
              </div>
              <button
                type="button"
                className="floating-checklist__close"
                aria-label="Close checklist"
                onClick={() => {
                  setChecklistPinned(false);
                  setChecklistDismissed(true);
                }}
              >
                <X size={16} />
              </button>
            </div>
            {selected ? (
              <>
                <div className="floating-checklist__meta">
                  <Status value={selected.status} />
                  <span>{selected.round || 0}/{selected.max_rounds || 30} rounds</span>
                </div>
                {selected.goal_state ? (
                  <>
                    <div className="floating-checklist__progress">
                      <span style={{ width: `${selected.goal_state.criteria.length ? (selected.goal_state.completed.length / selected.goal_state.criteria.length) * 100 : 0}%` }} />
                    </div>
                    <div className="floating-checklist__summary">
                      {selected.goal_state.completed.length} of {selected.goal_state.criteria.length} criteria verified
                    </div>
                    <ul className="floating-checklist__items">
                      {selected.goal_state.criteria.map((criterion, index) => {
                        const proof = selected.goal_state?.evidence_by_criterion?.[criterion];
                        const done = selected.goal_state?.completed.includes(criterion) ?? false;
                        return (
                          <li className={done ? "is-done" : ""} key={criterion + index}>
                            <span className="floating-checklist__check">
                              {done ? <Check size={13} /> : null}
                            </span>
                            <span className="floating-checklist__criterion">
                              {criterion}
                              {proof?.evidence_id && <small>Evidence · {proof.evidence_id}</small>}
                            </span>
                          </li>
                        );
                      })}
                    </ul>
                  </>
                ) : (
                  <p className="floating-checklist__empty">Success criteria are being prepared for this task.</p>
                )}
                <div className="floating-checklist__footer">
                  <span>{selected.status === "running" || selected.status === "queued" ? "Updating as work progresses" : "Showing this task's latest checklist"}</span>
                  <button type="button" onClick={() => {
                    document.querySelector(".report-details")?.scrollIntoView({ behavior: "smooth", block: "center" });
                    document.querySelectorAll(".report-details").forEach((node) => node.setAttribute("open", "true"));
                  }}>View full checklist</button>
                </div>
              </>
            ) : (
              <p className="floating-checklist__empty">Select a mission to see its goal, verified criteria and remaining work.</p>
            )}
          </section>
        </div>

        <footer className="footer">
          <span>GINGER NIGHT WALKER</span>
          <span><ShieldCheck size={13} /> READ-ONLY BROWSER · POLICY-GOVERNED · LOCAL-FIRST</span>
        </footer>
      </main>
    </div>
  );
}
