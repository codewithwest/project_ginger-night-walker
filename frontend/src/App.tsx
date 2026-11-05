import {
  ArrowDownRight,
  ArrowUpRight,
  Check,
  CircleAlert,
  ExternalLink,
  FileSearch,
  LoaderCircle,
  Play,
  Plus,
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
};

const isTerminal = (status: string) =>
  ["planned", "blocked", "completed", "failed", "stopped", "needs_clarification"].includes(
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

function extractProducts(text: string): Product[] {
  const lines = text
    .split("\n")
    .map((line) => line.replace(/\s+/g, " ").trim())
    .filter(Boolean);
  const ignored = new Set([
    "from",
    "shop now",
    "view collection",
    "new arrivals",
    "press to skip carousel",
    "add to cart",
    "quick view",
  ]);
  const products: Product[] = [];

  lines.forEach((line, index) => {
    const match = line.match(/R\s*\d[\d,]*(?:\.\d{2})?/i);
    if (!match) return;

    let name = line.slice(0, match.index).trim().replace(/[| -]+$/, "");
    if (!name) {
      let previous = index - 1;
      while (
        previous >= 0 &&
        (ignored.has(lines[previous].toLowerCase()) ||
          lines[previous].toLowerCase().includes("price depends on the options"))
      ) {
        previous -= 1;
      }
      name = previous >= 0 ? lines[previous] : "";
    }

    if (
      !name ||
      ignored.has(name.toLowerCase()) ||
      name.toLowerCase().includes("price depends on the options")
    ) {
      return;
    }

    if (!products.some((product) => product.name === name && product.price === match[0])) {
      products.push({ name, price: match[0] });
    }
  });

  return products;
}

function ResearchReport({ result }: { result: BrowserResult }) {
  const findings = result.findings || [];
  const sources = result.sources || (result.final_url ? [{ url: result.final_url, title: result.title }] : []);
  const products = result.products || [];
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

function ProductResult({ result }: { result: BrowserResult }) {
  const products = result.products?.length
    ? result.products
    : extractProducts(result.text || "");
  const firstProduct = products[0];
  const firstAnswer = result.answer || (
    firstProduct ? `${firstProduct.name} — ${firstProduct.price}` : null
  );

  return (
    <section className="answer-panel">
      <div className="answer-panel__topline">
        <span className="eyebrow"><Check size={13} /> RESULT FOUND</span>
        <span className="result-count">
          {products.length} {products.length === 1 ? "product" : "products"} found
        </span>
      </div>

      {firstAnswer && (
        <div className="answer-highlight">
          <span className="answer-highlight__label">FIRST PRODUCT</span>
          <h2>{firstProduct?.name || firstAnswer.split(" — ")[0]}</h2>
          <strong>{firstProduct?.price || firstAnswer.split(" — ").slice(1).join(" — ")}</strong>
        </div>
      )}

      {products.length > 0 && (
        <div className="product-list">
          {products.map((product, index) => (
            <div className="product-row" key={`${product.name}-${index}`}>
              <span className="product-row__number">
                {String(index + 1).padStart(2, "0")}
              </span>
              <span className="product-row__name">{product.name}</span>
              <strong className="product-row__price">{product.price}</strong>
            </div>
          ))}
        </div>
      )}

      {result.final_url && (
        <a
          className="source-link"
          href={result.final_url}
          target="_blank"
          rel="noreferrer"
        >
          <ExternalLink size={14} />
          Source page
          <span>{new URL(result.final_url).hostname}</span>
        </a>
      )}
    </section>
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

                {selected.result && ["completed", "blocked"].includes(selected.status) && (
                  <ResearchReport result={selected.result} />
                )}

                {selected.status === "running" || selected.status === "queued" ? (
                  <section className="working-panel">
                    <div className="working-panel__icon"><LoaderCircle size={20} /></div>
                    <div>
                      <strong>Working on it</strong>
                      <p>Night Walker is carrying out the mission. The report will appear here.</p>
                    </div>
                  </section>
                ) : null}

                {selected.error && (
                  <div className="error-banner">
                    <CircleAlert size={16} />
                    <span>{selected.error}</span>
                  </div>
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

        <footer className="footer">
          <span>GINGER NIGHT WALKER</span>
          <span><ShieldCheck size={13} /> READ-ONLY BROWSER · POLICY-GOVERNED · LOCAL-FIRST</span>
        </footer>
      </main>
    </div>
  );
}
