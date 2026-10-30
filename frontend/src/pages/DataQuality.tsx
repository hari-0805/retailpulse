import { Fragment, useCallback, useEffect, useState } from "react";
import Modal from "../components/Modal";
import { useAuth } from "../context/AuthContext";
import {
  getDQSummary, startReconciliation, getReconciliationRun, listReconciliationRuns,
  listDQIssues, getDQIssueFilterOptions, getDQIssue, updateDQIssueStatus,
} from "../api/dataQuality";
import type {
  DQSummary, DQRun, DQIssue, DQIssueDetail, DQIssueFilters, IssueStatus, IssueSeverity, RunStatus,
} from "../types";

const PAGE_SIZE = 15;
const POLL_MS = 2000;

const SEVERITY_STYLES: Record<IssueSeverity, string> = {
  ERROR: "bg-red-100 text-red-700",
  WARNING: "bg-amber-100 text-amber-700",
};
const STATUS_STYLES: Record<IssueStatus, string> = {
  OPEN: "bg-red-50 text-red-600",
  INVESTIGATING: "bg-blue-100 text-blue-700",
  RESOLVED: "bg-emerald-100 text-emerald-700",
  IGNORED: "bg-slate-100 text-slate-500",
};
const RUN_STYLES: Record<RunStatus, string> = {
  RUNNING: "bg-blue-100 text-blue-700",
  COMPLETED: "bg-emerald-100 text-emerald-700",
  COMPLETED_WITH_ISSUES: "bg-amber-100 text-amber-700",
  FAILED: "bg-red-100 text-red-700",
};

const fmt = (iso?: string | null) => (iso ? new Date(iso + (iso.endsWith("Z") ? "" : "Z")).toLocaleString() : "—");
const label = (s: string) => s.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase());

function Kpi({ title, value, accent, hint }: { title: string; value: string | number; accent?: string; hint?: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <p className="text-xs text-slate-500">{title}</p>
      <p className={`mt-1 text-2xl font-bold ${accent ?? "text-navy"}`}>{value}</p>
      {hint && <p className="mt-0.5 text-[11px] text-slate-400">{hint}</p>}
    </div>
  );
}

export default function DataQuality() {
  const { user } = useAuth();
  const canManage = user?.role === "COMPANY_ADMIN" || user?.role === "SUPER_ADMIN";

  const [tab, setTab] = useState<"overview" | "issues" | "history">("overview");
  const [refreshKey, setRefreshKey] = useState(0);

  // ---- overview / run state ----
  const [summary, setSummary] = useState<DQSummary | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(true);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [activeRun, setActiveRun] = useState<DQRun | null>(null);
  const [runResult, setRunResult] = useState<DQRun | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [now, setNow] = useState(Date.now());

  const loadSummary = useCallback(() => {
    setSummaryError(null);
    getDQSummary()
      .then((s) => {
        setSummary(s);
        // Pick up a run that is already in progress (e.g. started before a page reload or after an import).
        setActiveRun((prev) => prev ?? s.running_run ?? null);
      })
      .catch(() => setSummaryError("Couldn't load the data quality overview."))
      .finally(() => setSummaryLoading(false));
  }, []);

  useEffect(() => { loadSummary(); }, [loadSummary, refreshKey]);

  // Poll the running reconciliation so the UI shows live progress instead of looking frozen.
  useEffect(() => {
    if (!activeRun || activeRun.status !== "RUNNING") return;
    const runId = activeRun.id;
    const poll = setInterval(async () => {
      try {
        const r = await getReconciliationRun(runId);
        if (r.status !== "RUNNING") {
          setActiveRun(null);
          setRunResult(r);
          setRefreshKey((k) => k + 1);
        } else {
          setActiveRun(r);
        }
      } catch {
        /* transient error: keep polling */
      }
    }, POLL_MS);
    const tick = setInterval(() => setNow(Date.now()), 1000);
    return () => { clearInterval(poll); clearInterval(tick); };
  }, [activeRun?.id, activeRun?.status]);

  const handleRun = async () => {
    setStarting(true);
    setRunError(null);
    setRunResult(null);
    try {
      setActiveRun(await startReconciliation());
    } catch (e: any) {
      if (e?.response?.status === 409) {
        setRunError("A reconciliation is already running — showing its progress.");
        loadSummary();
      } else {
        setRunError(e?.response?.data?.detail ?? "Couldn't start the reconciliation.");
      }
    } finally {
      setStarting(false);
    }
  };

  const elapsedSeconds = activeRun
    ? Math.max(0, Math.floor((now - new Date(activeRun.started_at + (activeRun.started_at.endsWith("Z") ? "" : "Z")).getTime()) / 1000))
    : 0;

  // ---- issues state ----
  const [filters, setFilters] = useState<DQIssueFilters>({ sort_dir: "desc" });
  const [page, setPage] = useState(1);
  const [issues, setIssues] = useState<DQIssue[]>([]);
  const [issuesTotal, setIssuesTotal] = useState(0);
  const [issuesLoading, setIssuesLoading] = useState(false);
  const [issuesError, setIssuesError] = useState<string | null>(null);
  const [options, setOptions] = useState<{ issue_types: string[]; modules: string[] }>({ issue_types: [], modules: [] });
  const [selectedId, setSelectedId] = useState<string | null>(null);

  useEffect(() => {
    if (tab !== "issues") return;
    getDQIssueFilterOptions().then(setOptions).catch(() => undefined);
  }, [tab, refreshKey]);

  useEffect(() => {
    if (tab !== "issues") return;
    setIssuesLoading(true);
    setIssuesError(null);
    const clean: DQIssueFilters = { page, page_size: PAGE_SIZE };
    (Object.entries(filters) as [keyof DQIssueFilters, any][]).forEach(([k, v]) => { if (v) (clean as any)[k] = v; });
    listDQIssues(clean)
      .then((r) => { setIssues(r.items); setIssuesTotal(r.total); })
      .catch(() => setIssuesError("Couldn't load issues."))
      .finally(() => setIssuesLoading(false));
  }, [tab, filters, page, refreshKey]);

  // Changing one filter keeps every other selected filter.
  const setFilter = (key: keyof DQIssueFilters, value: string) => {
    setFilters((prev) => ({ ...prev, [key]: value || undefined }));
    setPage(1);
  };
  const issuePages = Math.max(1, Math.ceil(issuesTotal / PAGE_SIZE));

  // ---- history state ----
  const [runs, setRuns] = useState<DQRun[]>([]);
  const [runsTotal, setRunsTotal] = useState(0);
  const [runsPage, setRunsPage] = useState(1);
  const [runsLoading, setRunsLoading] = useState(false);
  const [runsError, setRunsError] = useState<string | null>(null);
  const [expandedRun, setExpandedRun] = useState<string | null>(null);

  useEffect(() => {
    if (tab !== "history") return;
    setRunsLoading(true);
    setRunsError(null);
    listReconciliationRuns(runsPage, PAGE_SIZE)
      .then((r) => { setRuns(r.items); setRunsTotal(r.total); })
      .catch(() => setRunsError("Couldn't load reconciliation history."))
      .finally(() => setRunsLoading(false));
  }, [tab, runsPage, refreshKey]);
  const runPages = Math.max(1, Math.ceil(runsTotal / PAGE_SIZE));

  return (
    <div className="mx-auto w-full max-w-7xl p-4 sm:p-6">
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-navy">Data Quality</h1>
          <p className="text-sm text-slate-500">Find and resolve inconsistencies across sales, inventory, products and customers.</p>
        </div>
        {canManage && (
          <button className="btn-primary" disabled={starting || !!activeRun} onClick={handleRun}>
            {activeRun ? "Reconciliation running..." : starting ? "Starting..." : "Run Reconciliation"}
          </button>
        )}
      </div>

      {/* Live run status */}
      {activeRun && (
        <div className="mb-4 flex items-center gap-3 rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-800">
          <span className="h-4 w-4 animate-spin rounded-full border-2 border-blue-500 border-t-transparent" />
          <span>Reconciliation in progress — running data-quality checks ({elapsedSeconds}s). You can keep using the app; results appear here when it finishes.</span>
        </div>
      )}
      {runResult && !activeRun && (
        <div className={`mb-4 rounded-lg border px-4 py-3 text-sm ${
          runResult.status === "FAILED" ? "border-red-200 bg-red-50 text-red-800"
            : runResult.status === "COMPLETED" ? "border-emerald-200 bg-emerald-50 text-emerald-800"
            : "border-amber-200 bg-amber-50 text-amber-800"}`}>
          {runResult.status === "FAILED"
            ? `Reconciliation failed: ${runResult.error_message ?? "unknown error"}`
            : runResult.status === "COMPLETED"
              ? `Reconciliation complete — ${runResult.records_checked} records checked, no inconsistencies found.`
              : `Reconciliation complete — ${runResult.records_checked} records checked, ${runResult.issues_detected} issue(s) present (${runResult.new_issues} new, ${runResult.issues_resolved} auto-resolved${runResult.failed_checks ? `, ${runResult.failed_checks} check(s) failed` : ""}).`}
        </div>
      )}
      {runError && <div className="mb-4 rounded-md border border-red-200 bg-red-50 px-4 py-2.5 text-sm text-red-800">{runError}</div>}

      <div className="mb-6 flex gap-2 border-b border-slate-200">
        {([["overview", "Overview"], ["issues", "Issues"], ["history", "Reconciliation History"]] as const).map(([t, text]) => (
          <button key={t} onClick={() => setTab(t)}
            className={`border-b-2 px-4 py-2 text-sm font-semibold ${tab === t ? "border-brand-500 text-brand-600" : "border-transparent text-slate-500 hover:text-slate-700"}`}>
            {text}
          </button>
        ))}
      </div>

      {/* ============ OVERVIEW ============ */}
      {tab === "overview" && (
        <>
          {summaryError && (
            <div className="mb-4 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
              {summaryError} <button className="ml-2 font-medium underline" onClick={loadSummary}>Try again</button>
            </div>
          )}
          <div className="mb-6 grid grid-cols-2 gap-4 lg:grid-cols-6">
            {summaryLoading ? Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="h-24 animate-pulse rounded-xl bg-slate-200" />
            )) : summary && (
              <>
                <Kpi title="Records checked" value={summary.records_checked} />
                <Kpi title="Valid records" value={summary.valid_records} accent="text-emerald-600" />
                <Kpi title="With warnings" value={summary.records_with_warnings} accent="text-amber-600" />
                <Kpi title="With errors" value={summary.records_with_errors} accent="text-red-600" />
                <Kpi title="Unresolved issues" value={summary.unresolved_issues} accent={summary.unresolved_issues ? "text-red-600" : "text-emerald-600"} />
                <Kpi title="Last reconciliation" value={summary.last_reconciliation_at ? fmt(summary.last_reconciliation_at).split(",")[0] : "Never"}
                  hint={summary.last_reconciliation_at ? fmt(summary.last_reconciliation_at).split(",")[1]?.trim() : "Run one to get started"} />
              </>
            )}
          </div>

          {summary && (
            <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
              <div className="rounded-xl border border-slate-200 bg-white shadow-sm">
                <p className="border-b border-slate-100 px-4 py-3 text-sm font-semibold text-navy">Unresolved issues by module</p>
                <table className="w-full text-left text-sm">
                  <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                    <tr><th className="px-4 py-2">Module</th><th className="px-4 py-2">Errors</th><th className="px-4 py-2">Warnings</th></tr>
                  </thead>
                  <tbody>
                    {summary.by_module.length === 0 && (
                      <tr><td colSpan={3} className="px-4 py-8 text-center text-slate-400">
                        {summary.last_reconciliation_at ? "No unresolved issues. Your data looks consistent." : "Nothing checked yet — run a reconciliation."}
                      </td></tr>
                    )}
                    {summary.by_module.map((m) => (
                      <tr key={m.module} className="border-t border-slate-100">
                        <td className="px-4 py-2 text-slate-700">{m.module}</td>
                        <td className="px-4 py-2 font-medium text-red-600">{m.errors}</td>
                        <td className="px-4 py-2 font-medium text-amber-600">{m.warnings}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="rounded-xl border border-slate-200 bg-white shadow-sm">
                <p className="border-b border-slate-100 px-4 py-3 text-sm font-semibold text-navy">Issues by status</p>
                <div className="grid grid-cols-2 gap-3 p-4">
                  {(["OPEN", "INVESTIGATING", "RESOLVED", "IGNORED"] as IssueStatus[]).map((s) => (
                    <button key={s} onClick={() => { setFilters({ sort_dir: "desc", status: s }); setPage(1); setTab("issues"); }}
                      className="rounded-lg border border-slate-200 p-3 text-left hover:bg-slate-50">
                      <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${STATUS_STYLES[s]}`}>{label(s)}</span>
                      <p className="mt-2 text-xl font-bold text-navy">{summary.status_counts[s] ?? 0}</p>
                    </button>
                  ))}
                </div>
              </div>
            </div>
          )}
        </>
      )}

      {/* ============ ISSUES ============ */}
      {tab === "issues" && (
        <>
          <div className="mb-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <input className="form-input" placeholder="Search description, record, type..." value={filters.search ?? ""} onChange={(e) => setFilter("search", e.target.value)} />
              <select className="form-input" value={filters.issue_type ?? ""} onChange={(e) => setFilter("issue_type", e.target.value)}>
                <option value="">All issue types</option>
                {options.issue_types.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
              <select className="form-input" value={filters.severity ?? ""} onChange={(e) => setFilter("severity", e.target.value)}>
                <option value="">Any severity</option><option value="ERROR">Error</option><option value="WARNING">Warning</option>
              </select>
              <select className="form-input" value={filters.module ?? ""} onChange={(e) => setFilter("module", e.target.value)}>
                <option value="">All modules</option>
                {options.modules.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
              <select className="form-input" value={filters.status ?? ""} onChange={(e) => setFilter("status", e.target.value)}>
                <option value="">Any status</option>
                {(["OPEN", "INVESTIGATING", "RESOLVED", "IGNORED"] as IssueStatus[]).map((s) => <option key={s} value={s}>{label(s)}</option>)}
              </select>
              <input type="date" className="form-input" value={filters.date_from ?? ""} onChange={(e) => setFilter("date_from", e.target.value)} />
              <input type="date" className="form-input" value={filters.date_to ?? ""} onChange={(e) => setFilter("date_to", e.target.value)} />
              <select className="form-input" value={filters.sort_dir ?? "desc"} onChange={(e) => setFilter("sort_dir", e.target.value)}>
                <option value="desc">Newest first</option><option value="asc">Oldest first</option>
              </select>
            </div>
            <button className="mt-3 text-sm font-medium text-brand-500 hover:underline" onClick={() => { setFilters({ sort_dir: "desc" }); setPage(1); }}>
              Clear filters
            </button>
          </div>

          <div className="rounded-xl border border-slate-200 bg-white shadow-sm">
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                  <tr>
                    <th className="px-4 py-3">Issue</th><th className="px-4 py-3">Type</th><th className="px-4 py-3">Severity</th>
                    <th className="px-4 py-3">Module</th><th className="px-4 py-3">Affected record</th><th className="px-4 py-3">Description</th>
                    <th className="px-4 py-3">Detected</th><th className="px-4 py-3">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {issuesLoading && Array.from({ length: 6 }).map((_, i) => (
                    <tr key={i} className="border-t border-slate-100">
                      {Array.from({ length: 8 }).map((__, j) => <td key={j} className="px-4 py-3"><div className="h-3.5 w-full max-w-[110px] animate-pulse rounded bg-slate-200" /></td>)}
                    </tr>
                  ))}
                  {!issuesLoading && issuesError && (
                    <tr><td colSpan={8} className="px-4 py-10 text-center">
                      <p className="mb-2 text-sm text-red-600">{issuesError}</p>
                      <button className="text-sm font-medium text-brand-500 hover:underline" onClick={() => setRefreshKey((k) => k + 1)}>Try again</button>
                    </td></tr>
                  )}
                  {!issuesLoading && !issuesError && issues.length === 0 && (
                    <tr><td colSpan={8} className="px-4 py-10 text-center text-sm text-slate-400">No issues match these filters.</td></tr>
                  )}
                  {!issuesLoading && !issuesError && issues.map((i) => (
                    <tr key={i.id} className="cursor-pointer border-t border-slate-100 hover:bg-slate-50" onClick={() => setSelectedId(i.id)}>
                      <td className="px-4 py-3 font-mono text-xs text-slate-500">{i.id.slice(0, 8)}</td>
                      <td className="max-w-[200px] px-4 py-3 text-slate-800">{i.issue_type}</td>
                      <td className="px-4 py-3"><span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${SEVERITY_STYLES[i.severity]}`}>{i.severity}</span></td>
                      <td className="px-4 py-3 text-slate-600">{i.module}</td>
                      <td className="max-w-[180px] truncate px-4 py-3 text-slate-600">{i.record_label ?? i.record_id}</td>
                      <td className="max-w-[280px] truncate px-4 py-3 text-slate-500">{i.description}</td>
                      <td className="whitespace-nowrap px-4 py-3 text-slate-500">{fmt(i.detected_at)}</td>
                      <td className="px-4 py-3"><span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${STATUS_STYLES[i.status]}`}>{label(i.status)}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!issuesLoading && !issuesError && issuesTotal > 0 && (
              <div className="flex items-center justify-between border-t border-slate-100 px-4 py-3 text-sm text-slate-500">
                <span>Page {page} of {issuePages} — {issuesTotal} issue{issuesTotal === 1 ? "" : "s"}</span>
                <div className="flex gap-2">
                  <button className="btn-outline px-3 py-1.5" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Previous</button>
                  <button className="btn-outline px-3 py-1.5" disabled={page >= issuePages} onClick={() => setPage((p) => p + 1)}>Next</button>
                </div>
              </div>
            )}
          </div>
        </>
      )}

      {/* ============ HISTORY ============ */}
      {tab === "history" && (
        <div className="rounded-xl border border-slate-200 bg-white shadow-sm">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-4 py-3">Execution</th><th className="px-4 py-3">Started</th><th className="px-4 py-3">Completed</th>
                  <th className="px-4 py-3">Triggered by</th><th className="px-4 py-3">Records</th><th className="px-4 py-3">Issues</th>
                  <th className="px-4 py-3">Resolved</th><th className="px-4 py-3">Failed checks</th><th className="px-4 py-3">Status</th>
                </tr>
              </thead>
              <tbody>
                {runsLoading && <tr><td colSpan={9} className="px-4 py-8 text-center text-slate-400">Loading...</td></tr>}
                {!runsLoading && runsError && (
                  <tr><td colSpan={9} className="px-4 py-8 text-center">
                    <p className="mb-2 text-sm text-red-600">{runsError}</p>
                    <button className="text-sm font-medium text-brand-500 hover:underline" onClick={() => setRefreshKey((k) => k + 1)}>Try again</button>
                  </td></tr>
                )}
                {!runsLoading && !runsError && runs.length === 0 && (
                  <tr><td colSpan={9} className="px-4 py-8 text-center text-slate-400">No reconciliations have run yet.</td></tr>
                )}
                {!runsLoading && !runsError && runs.map((r) => (
                  <Fragment key={r.id}>
                    <tr className="cursor-pointer border-t border-slate-100 hover:bg-slate-50" onClick={() => setExpandedRun(expandedRun === r.id ? null : r.id)}>
                      <td className="px-4 py-3 font-mono text-xs text-slate-500">{r.id.slice(0, 8)}</td>
                      <td className="whitespace-nowrap px-4 py-3 text-slate-600">{fmt(r.started_at)}</td>
                      <td className="whitespace-nowrap px-4 py-3 text-slate-600">{fmt(r.completed_at)}</td>
                      <td className="px-4 py-3 text-slate-600">{r.triggered_by_name ?? "—"}</td>
                      <td className="px-4 py-3 text-slate-600">{r.records_checked}</td>
                      <td className="px-4 py-3 text-slate-600">{r.issues_detected} <span className="text-xs text-slate-400">({r.new_issues} new)</span></td>
                      <td className="px-4 py-3 text-emerald-600">{r.issues_resolved}</td>
                      <td className={`px-4 py-3 ${r.failed_checks ? "font-semibold text-red-600" : "text-slate-600"}`}>{r.failed_checks}</td>
                      <td className="px-4 py-3"><span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${RUN_STYLES[r.status]}`}>{label(r.status)}</span></td>
                    </tr>
                    {expandedRun === r.id && (
                      <tr className="bg-slate-50">
                        <td colSpan={9} className="px-6 py-3">
                          {r.error_message && <p className="mb-2 text-sm text-red-600">{r.error_message}</p>}
                          {r.check_results.length === 0 ? <p className="text-sm text-slate-400">No per-check results recorded.</p> : (
                            <ul className="grid grid-cols-1 gap-1 text-xs sm:grid-cols-2">
                              {r.check_results.map((c) => (
                                <li key={c.code} className="flex items-center justify-between rounded border border-slate-200 bg-white px-3 py-1.5">
                                  <span className="text-slate-700">{c.label}</span>
                                  <span className={c.status === "OK" ? "text-slate-500" : "font-semibold text-red-600"}>
                                    {c.status === "OK" ? `${c.found} found` : `failed${c.error ? `: ${c.error}` : ""}`}
                                  </span>
                                </li>
                              ))}
                            </ul>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
          {!runsLoading && !runsError && runsTotal > PAGE_SIZE && (
            <div className="flex items-center justify-between border-t border-slate-100 px-4 py-3 text-sm text-slate-500">
              <button className="btn-outline px-3 py-1.5" disabled={runsPage <= 1} onClick={() => setRunsPage((p) => p - 1)}>Previous</button>
              <span>Page {runsPage} of {runPages}</span>
              <button className="btn-outline px-3 py-1.5" disabled={runsPage >= runPages} onClick={() => setRunsPage((p) => p + 1)}>Next</button>
            </div>
          )}
        </div>
      )}

      {selectedId && (
        <IssueDetailModal
          issueId={selectedId}
          canManage={canManage}
          onClose={() => setSelectedId(null)}
          onChanged={() => setRefreshKey((k) => k + 1)}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------- detail modal

function IssueDetailModal({ issueId, canManage, onClose, onChanged }: {
  issueId: string; canManage: boolean; onClose: () => void; onChanged: () => void;
}) {
  const [issue, setIssue] = useState<DQIssueDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [newStatus, setNewStatus] = useState<IssueStatus | "">("");
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    getDQIssue(issueId)
      .then(setIssue)
      .catch(() => setError("Couldn't load this issue."))
      .finally(() => setLoading(false));
  }, [issueId]);
  useEffect(() => { load(); }, [load]);

  const needsNote = newStatus === "RESOLVED" || newStatus === "IGNORED";

  const save = async () => {
    if (!newStatus) return;
    setSaving(true);
    setSaveError(null);
    setSaved(false);
    try {
      await updateDQIssueStatus(issueId, newStatus, note.trim() || undefined);
      setNewStatus("");
      setNote("");
      setSaved(true);
      load();
      onChanged();
    } catch (e: any) {
      setSaveError(e?.response?.data?.detail ?? "Couldn't update the issue.");
    } finally {
      setSaving(false);
    }
  };

  // Show the numbers that explain the inconsistency, skipping internal ids.
  const facts = issue
    ? Object.entries(issue.context.details).filter(([k, v]) => !/(^|_)ids?$/.test(k) && k !== "products" && k !== "movement_id" && typeof v !== "object")
    : [];

  return (
    <Modal title="Issue details" onClose={onClose} wide>
      {loading && <div className="space-y-2"><div className="h-4 w-1/2 animate-pulse rounded bg-slate-200" /><div className="h-3 w-full animate-pulse rounded bg-slate-200" /></div>}
      {!loading && error && <p className="text-sm text-red-600">{error} <button className="font-medium underline" onClick={load}>Try again</button></p>}
      {!loading && issue && (
        <div className="space-y-5">
          <div>
            <div className="mb-1 flex flex-wrap items-center gap-2">
              <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${SEVERITY_STYLES[issue.severity]}`}>{issue.severity}</span>
              <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${STATUS_STYLES[issue.status]}`}>{label(issue.status)}</span>
              <span className="text-xs text-slate-400">{issue.module} · detected {fmt(issue.detected_at)} · last seen {fmt(issue.last_detected_at)}</span>
            </div>
            <h3 className="text-base font-semibold text-navy">{issue.issue_type}</h3>
            <p className="mt-1 text-sm text-slate-600">{issue.description}</p>
          </div>

          {facts.length > 0 && (
            <div>
              <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">The inconsistency</p>
              <dl className="grid grid-cols-2 gap-x-6 gap-y-2 rounded-lg bg-slate-50 p-3 text-sm sm:grid-cols-3">
                {facts.map(([k, v]) => (
                  <div key={k}>
                    <dt className="text-xs text-slate-400">{label(k)}</dt>
                    <dd className={`font-medium ${k === "difference" || k === "shortfall" ? "text-red-600" : "text-slate-800"}`}>{String(v)}</dd>
                  </div>
                ))}
              </dl>
            </div>
          )}

          {Object.keys(issue.context.record).length > 0 && (
            <div>
              <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Affected record</p>
              <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-3">
                {Object.entries(issue.context.record).map(([k, v]) => (
                  <div key={k}><dt className="text-xs text-slate-400">{k}</dt><dd className="font-medium text-slate-800">{String(v)}</dd></div>
                ))}
              </dl>
            </div>
          )}

          {issue.context.line_items.length > 0 && (
            <div>
              <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Line items</p>
              <table className="w-full text-left text-xs">
                <thead className="bg-slate-50 text-slate-500"><tr><th className="px-3 py-1.5">Product</th><th className="px-3 py-1.5">Qty</th><th className="px-3 py-1.5">Unit price</th><th className="px-3 py-1.5">Total</th></tr></thead>
                <tbody>{issue.context.line_items.map((l, i) => (
                  <tr key={i} className="border-t border-slate-100"><td className="px-3 py-1.5">{l.product}</td><td className="px-3 py-1.5">{l.quantity}</td><td className="px-3 py-1.5">{l.unit_price}</td><td className="px-3 py-1.5">{l.total}</td></tr>
                ))}</tbody>
              </table>
            </div>
          )}

          {issue.context.movements.length > 0 && (
            <div>
              <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Recent stock movements</p>
              <div className="max-h-48 overflow-y-auto rounded border border-slate-200">
                <table className="w-full text-left text-xs">
                  <thead className="sticky top-0 bg-slate-50 text-slate-500"><tr><th className="px-3 py-1.5">Date</th><th className="px-3 py-1.5">Type</th><th className="px-3 py-1.5">Before</th><th className="px-3 py-1.5">Change</th><th className="px-3 py-1.5">After</th><th className="px-3 py-1.5">Reason</th></tr></thead>
                  <tbody>{issue.context.movements.map((m) => (
                    <tr key={m.id} className="border-t border-slate-100">
                      <td className="whitespace-nowrap px-3 py-1.5">{fmt(m.created_at)}</td><td className="px-3 py-1.5">{label(m.movement_type)}</td>
                      <td className="px-3 py-1.5">{m.previous_quantity}</td>
                      <td className={`px-3 py-1.5 ${m.quantity_changed < 0 ? "text-red-600" : "text-emerald-600"}`}>{m.quantity_changed > 0 ? "+" : ""}{m.quantity_changed}</td>
                      <td className="px-3 py-1.5">{m.updated_quantity}</td><td className="max-w-[220px] truncate px-3 py-1.5 text-slate-500">{m.reason ?? "—"}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            </div>
          )}

          {issue.context.related_sales.length > 0 && (
            <div>
              <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Related sales</p>
              <table className="w-full text-left text-xs">
                <thead className="bg-slate-50 text-slate-500"><tr><th className="px-3 py-1.5">Invoice</th><th className="px-3 py-1.5">Date</th><th className="px-3 py-1.5">Qty</th><th className="px-3 py-1.5">Total</th></tr></thead>
                <tbody>{issue.context.related_sales.map((s) => (
                  <tr key={s.sale_id} className="border-t border-slate-100"><td className="px-3 py-1.5">{s.invoice_number}</td><td className="px-3 py-1.5">{fmt(s.sale_date)}</td><td className="px-3 py-1.5">{s.quantity ?? "—"}</td><td className="px-3 py-1.5">{s.total}</td></tr>
                ))}</tbody>
              </table>
            </div>
          )}

          {(issue.resolution_note || issue.resolved_at) && (
            <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm">
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Resolution</p>
              <p className="mt-1 text-slate-700">{issue.resolution_note}</p>
              <p className="mt-1 text-xs text-slate-400">{issue.resolved_by_name ?? "System"} · {fmt(issue.resolved_at)}</p>
            </div>
          )}

          <div>
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">History</p>
            <ul className="space-y-1.5 text-xs text-slate-600">
              {issue.events.map((e) => (
                <li key={e.id} className="rounded border border-slate-100 px-3 py-1.5">
                  <span className="font-medium">{e.user_name ?? "System"}</span> changed status{" "}
                  {e.previous_status ? <>from <b>{label(e.previous_status)}</b> </> : null}to <b>{label(e.new_status)}</b>
                  <span className="text-slate-400"> · {fmt(e.created_at)}</span>
                  {e.note && <p className="mt-0.5 text-slate-500">"{e.note}"</p>}
                </li>
              ))}
            </ul>
          </div>

          {canManage && (
            <div className="rounded-lg border border-slate-200 p-3">
              <p className="mb-2 text-sm font-semibold text-navy">Update status</p>
              {saved && <p className="mb-2 text-sm text-emerald-600">Status updated.</p>}
              {saveError && <p className="mb-2 text-sm text-red-600">{saveError}</p>}
              <div className="flex flex-col gap-2 sm:flex-row">
                <select className="form-input sm:w-48" value={newStatus} onChange={(e) => { setNewStatus(e.target.value as IssueStatus | ""); setSaved(false); }}>
                  <option value="">Choose new status...</option>
                  {(["OPEN", "INVESTIGATING", "RESOLVED", "IGNORED"] as IssueStatus[]).filter((s) => s !== issue.status).map((s) => (
                    <option key={s} value={s}>{label(s)}</option>
                  ))}
                </select>
                <input className="form-input flex-1" placeholder={needsNote ? "Resolution note (required)" : "Note (optional)"} value={note} onChange={(e) => setNote(e.target.value)} />
                <button className="btn-primary" disabled={!newStatus || saving || (needsNote && note.trim().length < 3)} onClick={save}>
                  {saving ? "Saving..." : "Save"}
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}
