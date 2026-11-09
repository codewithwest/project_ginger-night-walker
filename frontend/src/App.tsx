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
  Sun,
  Moon,
  MonitorPlay,
  Maximize2,
  Minimize2,
  Square,
  Terminal,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

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
  workflow_type?: string;
  status?: string;
  final_url?: string;
  title?: string;
  text?: string;
  answer?: string | null;
  products?: Product[];
  evidence_id?: string;
  issues?: string[];
  findings?: {
    claim: string;
    type?: string;
    confidence?: string;
    priority?: string;
    impact_score?: number;
    impact_rationale?: string;
    remediation?: string;
    effort?: string;
    affected_urls?: string[];
    evidence_ids?: string[];
  }[];
  sources?: { url: string; title?: string; retrieved_at?: string }[];
  evidence?: { evidence_id?: string; source_url?: string; captured_text?: string; visited_urls?: string[] }[];
  structured_data?: Record<string, unknown>;
  limitations?: string[];
  suggested_memory?: string[];
  goal_state?: { original_goal: string; criteria: string[]; completed: string[]; remaining: string[]; evidence_by_criterion: Record<string, { evidence_id: string; quote: string }> };
};

type BrowserSession = {
  backend: string;
  status: string;
  active: boolean;
  debug_url?: string | null;
  current_url?: string;
  page_title?: string;
  activity?: { at?: string; message: string; url?: string | null }[];
  started_at?: string;
  finished_at?: string;
  last_error?: string;
};

type Task = {
  task_id: string;
  objective: string;
  capability?: string;
  status: string;
  created_at?: string;
  updated_at?: string;
  site_name?: string;
  current_step?: string | { objective?: string; url?: string; plan_step_id?: string } | null;
  plan_progress?: { status: string; completed: number; total: number } | null;
  target_url?: string;
  plan?: { clarification_question?: string; [key: string]: unknown };
  step_results?: unknown;
  result?: BrowserResult | null;
  error?: string;
  logs?: Log[];
  progress_events?: { at?: string; event_type?: string; status?: string; message?: string }[];
  execution_trace?: { event_id?: string; at?: string; phase?: string; details?: Record<string, unknown> }[];
  inference_events?: { at?: string; operation?: string; capability?: string; requested_model?: string; response_model?: string; status?: string; duration_ms?: number; load_duration_ms?: number | null; prompt_eval_duration_ms?: number | null; eval_duration_ms?: number | null; prompt_tokens?: number | null; completion_tokens?: number | null }[];
  timing?: { started_at?: string; finished_at?: string | null; elapsed_ms?: number; active_elapsed_ms?: number; active_phase?: string | null; phases_ms?: Record<string, number>; current_phase_ms?: number; phases?: Record<string, { duration_ms: number; share_percent: number }> };
  round?: number;
  max_rounds?: number;
  scratchpad?: { round?: number; assignment?: string; status?: string; findings?: unknown[]; evidence_ids?: string[] }[];
  goal_state?: { original_goal: string; criteria: string[]; completed: string[]; remaining: string[]; evidence_by_criterion: Record<string, { evidence_id: string; quote: string }> } | null;
  checklist_markdown?: string;
  execution_plan?: {
    version: number;
    status: string;
    steps: { step_id: string; index: number; objective: string; capability: string; status: string; depends_on: string[]; evidence_refs: string[]; result?: string | null }[];
    history: { at: string; step_id: string; status: string; message: string }[];
  } | null;
  browser?: BrowserSession | null;
};

const isTerminal = (status: string) =>
  ["planned", "blocked", "completed", "failed", "stopped"].includes(
    status,
  );

const formatDuration = (milliseconds?: number) => {
  const totalSeconds = Math.max(0, Math.floor((milliseconds || 0) / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return hours > 0 ? `${hours}h ${minutes}m ${seconds}s` : minutes > 0 ? `${minutes}m ${seconds}s` : `${seconds}s`;
};

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

function MarkdownContent({ children, className = "" }: { children: string; className?: string }) {
  return (
    <div className={`markdown-content ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ node: _node, ...props }) => (
            <a {...props} target="_blank" rel="noreferrer" />
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
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
  const auditSummary = structuredData.audit_report_summary as Record<string, string | number | boolean | undefined> | undefined;
  const pageSmoke = structuredData.qa_page_smoke as Record<string, any> | undefined;
  const singlePageScope = auditSummary?.crawl_scope === "single_page";
  const summaryMetrics = auditSummary ? singlePageScope ? [
    { label: "Pages inspected", value: auditSummary.pages_inspected ?? 0 },
    { label: "H1 headings", value: pageSmoke?.h1_count ?? "—" },
    { label: "Images checked", value: pageSmoke?.image_count ?? "—" },
    { label: "Broken images", value: pageSmoke?.broken_image_count ?? "—" },
    { label: "Images pending", value: pageSmoke?.pending_image_count ?? "—" },
    { label: "Console errors", value: pageSmoke?.console_error_count ?? "—" },
    { label: "Page exceptions", value: pageSmoke?.page_error_count ?? "—" },
    { label: "Failed requests", value: pageSmoke?.request_failed_count ?? "—" },
  ] : [
    { label: "Pages inspected", value: auditSummary.pages_inspected ?? 0 },
    { label: "Known URL coverage", value: auditSummary.crawl_coverage_percent == null ? "—" : String(auditSummary.crawl_coverage_percent) + "%" },
    { label: "Templates inspected", value: auditSummary.unique_templates_inspected ?? 0 },
    { label: "Budget used", value: auditSummary.budget_utilisation_percent == null ? "—" : String(auditSummary.budget_utilisation_percent) + "%" },
    { label: "URLs excluded", value: auditSummary.excluded_url_count ?? 0 },
    { label: "Pages still queued", value: auditSummary.queued_candidate_count ?? 0 },
    { label: "Broken internal links", value: auditSummary.broken_internal_links ?? 0 },
    { label: "Duplicate title groups", value: auditSummary.duplicate_title_groups ?? 0 },
    { label: "Duplicate content groups", value: auditSummary.duplicate_content_groups ?? 0 },
    { label: "Accessibility findings", value: auditSummary.accessibility_findings ?? 0 },
  ] : [];
  const limitations = result.limitations || result.issues || [];
  const reportLabel = result.workflow_type
    ? result.workflow_type.replaceAll("_", " ").toUpperCase() + " REPORT"
    : "RESEARCH REPORT";
  return (
    <section className="answer-panel research-report">
      <div className="answer-panel__topline">
        <span className="eyebrow"><Check size={13} /> {reportLabel}</span>
        <span className="result-count">{findings.length} findings · {sources.length} sources{result.schema_version ? " · schema " + result.schema_version : ""}</span>
      </div>
      <div className="research-report__answer">
        <span className="answer-highlight__label">ANSWER</span>
        <MarkdownContent className="markdown-content--answer">{result.answer || "The page was captured. Review the findings and source evidence below."}</MarkdownContent>
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
      {summaryMetrics.length > 0 && <div className="research-section">
        <h3>Audit overview <span className="audit-completion">{singlePageScope ? "Single-page scope · site-wide coverage not measured" : auditSummary?.crawl_complete ? "Known discovery queue exhausted" : "Coverage incomplete"}</span></h3>
        <div className="audit-summary-grid">{summaryMetrics.map((metric) => (
          <div className="audit-summary-metric" key={metric.label}>
            <strong>{metric.value}</strong><span>{metric.label}</span>
          </div>
        ))}</div>
      </div>}
      {findings.length > 0 && <div className="research-section">
        <h3>Prioritised findings</h3>
        <ul className="finding-list">{findings.map((finding, index) => <li key={finding.claim + "-" + index}>
          <div className="finding-heading">
            <span>{finding.claim}</span>
            {finding.priority && <strong className={"finding-priority finding-priority--" + finding.priority.toLowerCase()}>{finding.priority}</strong>}
          </div>
          <small>{finding.type?.replaceAll("_", " ") || "observation"} · {finding.confidence || "observed"}{finding.impact_score != null ? " · impact " + finding.impact_score + "/100" : ""}{finding.effort ? " · " + finding.effort + " effort" : ""}</small>
          {finding.impact_rationale && <p className="finding-impact">{finding.impact_rationale}</p>}
          {finding.remediation && <p className="finding-remediation"><strong>Recommended fix:</strong> {finding.remediation}</p>}
          {finding.affected_urls && finding.affected_urls.length > 0 && <div className="finding-urls">
            {finding.affected_urls.slice(0, 5).map((url) => <a href={url} target="_blank" rel="noreferrer" key={url}>{url}</a>)}
          </div>}
          {finding.evidence_ids && finding.evidence_ids.length > 0 && <small>Evidence: {finding.evidence_ids.join(", ")}</small>}
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
  const [retrying, setRetrying] = useState(false);
  const [busy, setBusy] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [online, setOnline] = useState(false);
  const [checklistPinned, setChecklistPinned] = useState(false);
  const [checklistDismissed, setChecklistDismissed] = useState(false);
  const [browserPinned, setBrowserPinned] = useState(false);
  const [browserDismissed, setBrowserDismissed] = useState(false);
  const [checklistFullscreen, setChecklistFullscreen] = useState(false);
  const [theme, setTheme] = useState<"dark" | "light">(() => {
    try { return localStorage.getItem("night-walker-theme") === "light" ? "light" : "dark"; }
    catch { return "dark"; }
  });

  useEffect(() => {
    if (!checklistFullscreen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setChecklistFullscreen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [checklistFullscreen]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("night-walker-theme", theme); } catch { /* storage is optional */ }
  }, [theme]);

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
      setTasks((current) => [task, ...current.filter((item) => item.task_id !== task.task_id)]);
      setSelectedId(task.task_id);
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
      await refreshSelected(selected.task_id);
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
      await refreshSelected(selected.task_id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Follow-up failed");
    } finally { setSendingFollowUp(false); }
  }

  async function retryTask() {
    if (!selected || selected.status !== "failed" || retrying) return;
    setRetrying(true);
    setError("");
    try {
      const task = await api<Task>(`/tasks/${encodeURIComponent(selected.task_id)}/retry`, {
        method: "POST",
      });
      setTasks((current) => current.map((item) => item.task_id === task.task_id ? task : item));
      await refreshSelected(selected.task_id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Task retry failed");
    } finally {
      setRetrying(false);
    }
  }

  async function stop() {
    if (!selected) return;

    try {
      await api(`/tasks/${encodeURIComponent(selected.task_id)}`, {
        method: "DELETE",
      });
      await refreshSelected(selected.task_id);
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
          <button className="theme-toggle" type="button" onClick={() => setTheme((current) => current === "dark" ? "light" : "dark")} aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`} title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}>
            {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
            <span>{theme === "dark" ? "LIGHT" : "DARK"}</span>
          </button>
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
                      {task.plan_progress && task.plan_progress.total > 0 && (
                        <span className="mission-item__id">Plan · {task.plan_progress.completed}/{task.plan_progress.total} steps verified</span>
                      )}
                      {task.timing && <span className="mission-item__duration">Elapsed · {formatDuration(task.timing.elapsed_ms)}</span>}
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
                      <span>·</span>
                      <span className="job-duration">{selected.timing?.finished_at ? "Total time" : "Elapsed"} · {formatDuration(selected.timing?.elapsed_ms)}</span>
                    </div>
                  </div>
                  <div className="result-header__actions">
                    <Status value={selected.status} />
                    {selected.status === "failed" && (
                      <button className="stop-button" onClick={() => void retryTask()} disabled={retrying}>
                        {retrying ? <LoaderCircle size={13} /> : <RefreshCw size={13} />}
                        {retrying ? "Retrying…" : "Retry task"}
                      </button>
                    )}
                    {!isTerminal(selected.status) && (
                      <button className="stop-button" onClick={() => void stop()}>
                        <Square size={12} /> Stop
                      </button>
                    )}
                  </div>
                </div>

                {selected.inference_events?.length ? (
        <section className="inference-panel">
          <div className="section-kicker">MODEL INFERENCE</div>
          <div className="inference-panel__list">
            {selected.inference_events.slice().reverse().map((event, index) => (
              <div className="inference-event" key={event.at || index}>
                <div className="inference-event__top"><strong>{event.operation || "Inference"}</strong><span>{event.status} · {formatDuration(event.duration_ms)}</span></div>
                <div className="inference-event__model">Requested: {event.requested_model || "unknown"}{event.response_model && event.response_model !== event.requested_model ? ` · Returned: ${event.response_model}` : ""}</div>
                <div className="inference-event__metrics">Load {event.load_duration_ms == null ? "—" : formatDuration(event.load_duration_ms)} · Prompt {event.prompt_eval_duration_ms == null ? "—" : formatDuration(event.prompt_eval_duration_ms)} · Generation {event.eval_duration_ms == null ? "—" : formatDuration(event.eval_duration_ms)}</div>
              </div>
            ))}
          </div>
        </section>
      ) : null}
      {selected.timing && (
                  <section className="timing-panel">
                    <div className="timing-panel__top">
                      <div><div className="section-kicker">JOB TIMING</div><strong>{selected.timing.finished_at ? "Total duration" : "Elapsed so far"}</strong></div>
                      <strong className="timing-panel__total">{formatDuration(selected.timing.elapsed_ms)}</strong>
                    </div>
                    <div className="timing-panel__phases">
                      {(["queue", "planning", "browser", "analysis", "reporting"] as const).map((phase) => {
                        const duration = (selected.timing?.phases_ms?.[phase] || 0) + (selected.timing?.active_phase === phase ? (selected.timing?.current_phase_ms || 0) : 0);
                        const share = selected.timing?.active_elapsed_ms ? Math.min(100, (duration / selected.timing.active_elapsed_ms) * 100) : 0;
                        return <div className="timing-phase" key={phase}><div className="timing-phase__meta"><span>{phase}</span><span>{formatDuration(duration)}</span></div><div className="timing-phase__track"><span style={{ width: `${share}%` }} /></div></div>;
                      })}
                    </div>
                    {selected.timing.active_phase && <div className="timing-panel__active">Currently timing: {selected.timing.active_phase}</div>}
                  </section>
                )}

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
                      {selected.current_step && <div className="current-step"><span className="current-step__label">CURRENT STEP</span><MarkdownContent>{typeof selected.current_step === "string" ? selected.current_step : selected.current_step.objective || "Executing planned step"}</MarkdownContent></div>}
                      <div className="progress-timeline progress-timeline--live">
                        {(selected.progress_events || []).slice(-5).map((event, index) => (
                          <article className="progress-event" key={(event.at || "") + index}>
                            <span className="progress-event__marker" />
                            <div className="progress-event__body">
                              <div className="progress-event__meta">
                                <span>{(event.event_type || event.status || "UPDATE").replaceAll("_", " ")}</span>
                                <time>{formatTime(event.at)}</time>
                              </div>
                              <MarkdownContent>{event.message || "Progress updated."}</MarkdownContent>
                            </div>
                          </article>
                        ))}
                      </div>
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

                {selected.execution_plan?.steps?.length ? (
                  <details className="report-details" open>
                    <summary>
                      <span><Check size={15} /> Mission plan · {selected.execution_plan.steps.filter((step) => step.status === "completed").length}/{selected.execution_plan.steps.length} verified</span>
                      <ArrowDownRight size={15} />
                    </summary>
                    <div className="report-details__body">
                      <div className="plan-step-list">
                        {selected.execution_plan.steps.map((step) => (
                          <article className="plan-step" key={step.step_id}>
                            <div className="plan-step__heading">
                              <strong>{step.index}. {step.objective}</strong>
                              <Status value={step.status} />
                            </div>
                            <div className="plan-step__meta">
                              <span>{step.capability}</span>
                              {step.depends_on.length > 0 && <span>Depends on: {step.depends_on.join(", ")}</span>}
                            </div>
                            {step.result && <p>{step.result}</p>}
                            {step.evidence_refs.length > 0 && <div className="plan-step__evidence">Evidence: {step.evidence_refs.join(", ")}</div>}
                          </article>
                        ))}
                      </div>
                    </div>
                  </details>
                ) : null}

                {selected.checklist_markdown && (
                  <details className="report-details" open>
                    <summary>
                      <span><Check size={15} /> Goal &amp; progress checklist</span>
                      <ArrowDownRight size={15} />
                    </summary>
                    <div className="report-details__body">
                      <div className="checklist-actions"><span>Readable task guidance</span><button type="button" onClick={() => setChecklistFullscreen(true)}><Maximize2 size={14} /> Full screen</button></div>
                      <MarkdownContent className="checklist-markdown">{selected.checklist_markdown}</MarkdownContent>
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
                    {selected.progress_events?.length ? (
                      <section className="progress-history">
                        <div className="progress-history__heading">Progress history <span>{selected.progress_events.length} updates</span></div>
                        <div className="progress-timeline">
                          {selected.progress_events.map((event, index) => (
                            <article className="progress-event" key={(event.at || "") + index}>
                              <span className="progress-event__marker" />
                              <div className="progress-event__body">
                                <div className="progress-event__meta">
                                  <span>{(event.event_type || event.status || "UPDATE").replaceAll("_", " ")}</span>
                                  <time>{event.at ? new Date(event.at).toLocaleString() : "—"}</time>
                                </div>
                                <MarkdownContent>{event.message || "Progress updated."}</MarkdownContent>
                              </div>
                            </article>
                          ))}
                        </div>
                      </section>
                    ) : null}
                    {selected.execution_trace?.length ? (
                      <section className="progress-history">
                        <div className="progress-history__heading">Execution trace <span>{selected.execution_trace.length} events</span></div>
                        <div className="progress-timeline">
                          {selected.execution_trace.map((event, index) => (
                            <article className="progress-event" key={event.event_id || (event.at || "") + index}>
                              <span className="progress-event__marker" />
                              <div className="progress-event__body">
                                <div className="progress-event__meta">
                                  <span>EXECUTION · {(event.phase || "event").toUpperCase()}</span>
                                  <time>{event.at ? new Date(event.at).toLocaleString() : "—"}</time>
                                </div>
                                <MarkdownContent>{String(event.details?.reason || event.details?.observation || event.details?.status || event.details?.method || "Execution phase recorded.")}</MarkdownContent>
                              </div>
                            </article>
                          ))}
                        </div>
                      </section>
                    ) : null}
                    {selected.scratchpad?.length ? (
                      <div className="log-list">
                        {selected.scratchpad.map((note, index) => (
                          <div className="log-entry" key={"scratch-" + index}>
                            <time>R{note.round ?? "—"}</time>
                            <span className="log-entry__level">scratchpad</span>
                            <div className="log-entry__content"><MarkdownContent>{note.assignment || "Research note"}</MarkdownContent><span className="log-entry__status">{note.status}</span></div>
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
                            <div className="log-entry__content"><MarkdownContent>{log.message || ""}</MarkdownContent></div>
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

        <div className={`floating-browser ${selected?.browser?.active ? "is-running" : ""} ${browserPinned ? "is-pinned" : ""} ${browserDismissed ? "is-dismissed" : ""}`}>
          <button
            type="button"
            className="floating-browser__trigger"
            aria-label="Open live browser monitor"
            aria-expanded={browserPinned}
            onClick={() => {
              setBrowserDismissed(false);
              setBrowserPinned((current) => !current);
            }}
          >
            <span className="floating-browser__trigger-icon"><MonitorPlay size={19} /></span>
            <span className="floating-browser__trigger-label">Live browser</span>
            {selected?.browser?.active && <span className="floating-browser__live-dot" aria-label="Browser active" />}
          </button>

          <section className="floating-browser__panel" aria-label="Live browser monitor">
            <div className="floating-browser__header">
              <div>
                <span className="section-kicker"><MonitorPlay size={13} /> BROWSER OBSERVABILITY</span>
                <strong>{selected?.browser?.page_title || "Waiting for browser activity"}</strong>
              </div>
              <button
                type="button"
                className="floating-browser__close"
                aria-label="Close browser monitor"
                onClick={() => {
                  setBrowserPinned(false);
                  setBrowserDismissed(true);
                }}
              >
                <X size={16} />
              </button>
            </div>
            <div className="floating-browser__meta">
              <span className={selected?.browser?.active ? "is-live" : ""}>
                <i /> {selected?.browser?.status || "idle"}
              </span>
              <span>{selected?.browser?.backend || "browser"} backend</span>
            </div>
            {selected?.browser?.current_url && (
              <a className="floating-browser__url" href={selected.browser.current_url} target="_blank" rel="noreferrer">
                {selected.browser.current_url}
                <ExternalLink size={12} />
              </a>
            )}
            {selected?.browser?.debug_url && selected.browser.active ? (
              <div className="floating-browser__viewer">
                <iframe
                  title="Read-only live Steel browser session"
                  src={`${selected.browser.debug_url}${selected.browser.debug_url.includes("?") ? "&" : "?"}interactive=false`}
                  allow="autoplay; fullscreen"
                  referrerPolicy="no-referrer"
                />
              </div>
            ) : (
              <div className="floating-browser__viewer-empty">
                {selected?.browser?.active
                  ? "The browser is active. A live video URL was not provided by this Steel session; activity is still shown below."
                  : selected?.browser?.finished_at
                    ? "This browser session has finished. The activity timeline below remains available."
                    : "Start a mission to see its live browser session here."}
              </div>
            )}
            {selected?.browser?.last_error && <div className="floating-browser__error">{selected.browser.last_error}</div>}
            <div className="floating-browser__activity-heading">
              <span>SESSION ACTIVITY</span>
              <span>{selected?.browser?.activity?.length || 0} events</span>
            </div>
            <div className="floating-browser__activity">
              {(selected?.browser?.activity || []).slice().reverse().slice(0, 8).map((event, index) => (
                <article key={(event.at || "") + index}>
                  <span className="floating-browser__activity-dot" />
                  <div>
                    <p>{event.message}</p>
                    <time>{formatTime(event.at)}</time>
                  </div>
                </article>
              ))}
              {!selected?.browser?.activity?.length && <p className="floating-browser__empty">No browser events recorded for this mission yet.</p>}
            </div>
            <div className="floating-browser__resize-hint">Resize from the lower-right corner · width and height are adjustable</div>
            <div className="floating-browser__footer">
              <span>Live view is read-only</span>
              {selected?.browser?.backend === "steel" && <a href="http://localhost:3000/ui" target="_blank" rel="noreferrer">Open Steel UI <ExternalLink size={11} /></a>}
            </div>
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

        {checklistFullscreen && selected?.checklist_markdown && (
          <div className="checklist-fullscreen" role="dialog" aria-modal="true" aria-label="Full-screen task guidance">
            <header className="checklist-fullscreen__header">
              <div><span className="section-kicker"><ListChecks size={14} /> TASK GOAL &amp; PROGRESS</span><strong>{selected.objective}</strong></div>
              <button type="button" onClick={() => setChecklistFullscreen(false)} aria-label="Exit full screen"><Minimize2 size={17} /> Exit full screen</button>
            </header>
            <div className="checklist-fullscreen__content"><MarkdownContent className="checklist-markdown checklist-markdown--fullscreen">{selected.checklist_markdown}</MarkdownContent></div>
          </div>
        )}

        <footer className="footer">
          <span>GINGER NIGHT WALKER</span>
          <span><ShieldCheck size={13} /> READ-ONLY BROWSER · POLICY-GOVERNED · LOCAL-FIRST</span>
        </footer>
      </main>
    </div>
  );
}
