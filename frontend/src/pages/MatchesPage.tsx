import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError, type Job, type MatchResult, type RunView, unwrap } from "../api/client";
import { ResultCard } from "../components/ResultCard";
import { useApp } from "../Layout";
import { daysUntil, errorText, shortDate, useLoad } from "../lib";

const jobKey = (pid: string) => `scholarmatch.job.${pid}`;

function remember(pid: string, jobId: string | null) {
  try {
    if (jobId) sessionStorage.setItem(jobKey(pid), jobId); // reconnect after a page reload
    else sessionStorage.removeItem(jobKey(pid));
  } catch {
    /* storage blocked: reconnecting is a convenience only */
  }
}

/** Follow a background run over server-sent events until it is done or fails. */
function useRunJob(pid: string, onDone: () => void) {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState("");
  const source = useRef<EventSource | null>(null);
  // Read through a ref: `onDone` changes with the sort order, and that must neither restart the stream nor make the
  // finished run reload with a sort the student has since changed.
  const doneRef = useRef(onDone);
  doneRef.current = onDone;
  const retries = useRef(0);

  // Don't spin forever when the run can't be followed (e.g. the API restarted and forgot the job).
  const lost = useCallback(() => {
    source.current?.close();
    remember(pid, null);
    setJob(null);
    setError("Lost touch with the run. Press Find again; a finished run is reused, so nothing is lost.");
  }, [pid]);

  const follow = useCallback(
    (j: Job) => {
      source.current?.close();
      setJob(j);
      if (j.state === "done" || j.state === "error") {
        remember(pid, null);
        doneRef.current();
        return;
      }
      remember(pid, j.job_id);
      const es = new EventSource(`/api/jobs/${j.job_id}/events`);
      source.current = es;
      const update = (e: MessageEvent<string>) => setJob(JSON.parse(e.data) as Job);
      const finish = (e: MessageEvent<string>) => {
        update(e);
        es.close();
        remember(pid, null);
        doneRef.current();
      };
      es.addEventListener("progress", update);
      es.addEventListener("done", finish);
      es.addEventListener("error", (e) => {
        if (e instanceof MessageEvent && e.data) return finish(e); // the run failed: the server sent the Job
        if (es.readyState !== EventSource.CLOSED) return; // a dropped connection that EventSource retries by itself
        // The browser gave up on the stream. That also happens when the page unloads, so ask the API before
        // forgetting the job (forgetting it on unload broke picking the run up again after a reload).
        api
          .GET("/api/jobs/{job_id}", { params: { path: { job_id: j.job_id } } })
          .then(({ data }) => {
            if (data && ++retries.current <= 3) return follow(data); // still known: reconnect, or finish if done
            lost(); // forgotten (404), API down behind the dev proxy (5xx), or keeps dropping
          })
          .catch(() => lost()); // the API itself is down
      });
    },
    [pid, lost],
  );

  // Pick up a run that was still going when the page was reloaded.
  useEffect(() => {
    let stored: string | null = null;
    try {
      stored = sessionStorage.getItem(jobKey(pid));
    } catch {
      /* ignore */
    }
    if (stored)
      api
        .GET("/api/jobs/{job_id}", { params: { path: { job_id: stored } } })
        .then(({ data }) => (data ? follow(data) : remember(pid, null)))
        .catch(() => undefined); // API unreachable: the layout already says so
    return () => source.current?.close();
  }, [pid, follow]);

  async function start(force: boolean) {
    setError("");
    retries.current = 0;
    try {
      follow(await unwrap(api.POST("/api/profiles/{pid}/runs", { params: { path: { pid } }, body: { force } })));
    } catch (e) {
      setError(errorText(e));
    }
  }

  const running = job?.state === "queued" || job?.state === "running";

  // Watchdog. A proxy (Vite's dev proxy does this) can keep the browser's stream open after the API behind it has
  // gone, so the stream alone can't be trusted to report a lost run. Check the job itself now and then.
  const jobId = running ? job.job_id : null;
  useEffect(() => {
    if (!jobId) return;
    const timer = window.setInterval(() => {
      api
        .GET("/api/jobs/{job_id}", { params: { path: { job_id: jobId } } })
        .then(({ data }) => {
          if (!data) lost();
          else if (data.state === "done" || data.state === "error") follow(data); // a finish the stream missed
        })
        .catch(() => lost());
    }, 15000);
    return () => window.clearInterval(timer);
  }, [jobId, follow, lost]);

  return { job, running, error, start };
}

type Filter = "all" | "strong" | "soon" | "quick";
const ALL_RESULTS = 500; // the cards are filtered here, so load the whole run

const FILTERS: [Filter, string][] = [
  ["all", "All"],
  ["strong", "Strong fit"],
  ["soon", "Due in 30 days"],
  ["quick", "2 hours or less"],
];

export function MatchesPage() {
  const { pid = "" } = useParams();
  const { meta, profiles } = useApp();
  const [sort, setSort] = useState(meta.sorts[0] ?? "Best overall match");
  const [hideUnverified, setHideUnverified] = useState(false);
  const [filter, setFilter] = useState<Filter>("all");
  const [force, setForce] = useState(false);
  const known = profiles.includes(pid);

  const run = useLoad(async (): Promise<RunView | null> => {
    try {
      return await unwrap(
        api.GET("/api/profiles/{pid}/runs/latest", {
          params: { path: { pid }, query: { sort, limit: ALL_RESULTS, hide_unverified: hideUnverified } },
        }),
      );
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return null;
      throw e;
    }
  }, [pid, sort, hideUnverified]);
  const profile = useLoad(
    async () => (known ? await unwrap(api.GET("/api/profiles/{pid}", { params: { path: { pid } } })) : null),
    [pid, known],
  );
  const saved = useLoad(() => unwrap(api.GET("/api/profiles/{pid}/saved", { params: { path: { pid } } })), [pid]);
  const savedKeys = new Set((saved.data ?? []).map((s) => s.key));
  const reloadRun = run.reload;
  const { job, running, error, start } = useRunJob(pid, reloadRun);

  if (!known)
    return (
      <section className="hero">
        <div className="hero-title">
          <h1>Your matches</h1>
        </div>
        <p className="muted">
          Create and save a <Link to={`/p/${pid}/profile`}>profile</Link> first.
        </p>
      </section>
    );

  async function toggleSave(key: string) {
    const params = { params: { path: { pid, key } } };
    try {
      if (savedKeys.has(key)) await unwrap(api.DELETE("/api/profiles/{pid}/saved/{key}", params));
      else await unwrap(api.PUT("/api/profiles/{pid}/saved/{key}", { ...params, body: { status: "interested", note: "" } }));
      await saved.reload();
    } catch (e) {
      window.alert(errorText(e));
    }
  }

  const v = run.data;
  const results = v?.results ?? [];
  const asOf = v?.as_of ?? new Date().toISOString().slice(0, 10);
  const days = (r: MatchResult) => daysUntil(r.deadline, asOf);
  const tests: Record<Filter, (r: MatchResult) => boolean> = {
    all: () => true,
    strong: (r) => r.judgement?.fit === "strong",
    soon: (r) => (days(r) ?? Infinity) <= 30,
    quick: (r) => r.effort_hours <= 2,
  };
  const count = (f: Filter) => results.filter(tests[f]).length;
  const shown = results.filter(tests[filter]);
  const reuse = results.filter((r) => r.judgement?.essay_reuse === "full" || r.judgement?.essay_reuse === "partial").length;
  const upcoming = results
    .filter((r) => r.deadline)
    .sort((a, b) => a.deadline!.localeCompare(b.deadline!))
    .slice(0, 4);
  const firstName = profile.data?.profile.display_name?.split(/\s+/)[0];
  const excluded = v ? Object.values(v.exclusion_counts).reduce((a, b) => a + b, 0) : 0;

  return (
    <section>
      <div className="hero">
        <div className="hero-top">
          <div className="hero-title">
            <p>{firstName ? `Hi ${firstName}` : "Your matches"}</p>
            <h1>
              {v
                ? `${v.total_results} scholarship${v.total_results === 1 ? "" : "s"} you qualify for`
                : "Find scholarships you can win"}
            </h1>
          </div>
          {v && v.total_results > 0 && (
            <div className="hero-stats">
              <div className="hero-stat"><b>{count("strong")}</b><span>strong fits</span></div>
              <div className="hero-stat"><b>{count("soon")}</b><span>due in 30 days</span></div>
              <div className="hero-stat"><b>{reuse}</b><span>reuse an essay</span></div>
            </div>
          )}
        </div>
        <div className="hero-actions">
          <button className="primary" disabled={running} onClick={() => start(force)}>
            {v ? "Refresh matches" : "Find my scholarships"}
          </button>
          <label className="check small">
            <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />
            Re-run from scratch
          </label>
          {v && (
            <span className="hero-meta">
              {v.review_mode} · {shortDate(v.as_of)}
              {v.stale && <span className="stale"> · profile or catalog changed, refresh</span>}
            </span>
          )}
        </div>
        {running && job && (
          <div className="progress" role="status">
            <div className="bar" style={{ width: `${Math.round(job.fraction * 100)}%` }} />
            <span>{job.message}</span>
          </div>
        )}
      </div>

      {job?.state === "error" && <div className="notice error">The run failed: {job.error}</div>}
      {error && <div className="notice error">{error}</div>}
      {run.error && <div className="notice error">{run.error}</div>}

      {v === null && !running && (
        <div className="notice info">Rules check every listing first; only real candidates go to the AI reviewers.</div>
      )}

      {v && (
        <div className="matches-layout">
          <div className="matches-main">
            {v.total_results === 0 ? (
              <div className="notice warn">
                <p>
                  <strong>No matches right now.</strong> Nothing was hidden silently; the reasons are below.
                </p>
                <p>
                  Try: add colleges you're applying to · fill in GPA, county and major · turn off no-essay only · lower
                  the minimum award.
                </p>
              </div>
            ) : (
              <>
                <div className="toolbar">
                  {FILTERS.map(([f, label]) => (
                    <button key={f} className="chip-btn" aria-pressed={filter === f} onClick={() => setFilter(f)}>
                      {label} {count(f)}
                    </button>
                  ))}
                  <button
                    className="chip-btn"
                    aria-pressed={hideUnverified}
                    onClick={() => setHideUnverified(!hideUnverified)}
                  >
                    Hide “verify”
                  </button>
                  <label className="sort">
                    Sort
                    <select value={sort} onChange={(e) => setSort(e.target.value)}>
                      {meta.sorts.map((s) => (
                        <option key={s}>{s}</option>
                      ))}
                    </select>
                  </label>
                </div>
                {shown.length === 0 ? (
                  <p className="muted">No matches in this view.</p>
                ) : (
                  <div className="cards">
                    {shown.map((r) => (
                      <ResultCard
                        key={r.scholarship_key}
                        r={r}
                        pid={pid}
                        asOf={asOf}
                        saved={savedKeys.has(r.scholarship_key)}
                        onToggleSave={() => toggleSave(r.scholarship_key)}
                      />
                    ))}
                  </div>
                )}
              </>
            )}

            <details className="panel" style={{ marginTop: "1.5rem" }}>
              <summary>Why not the others? ({excluded} excluded)</summary>
              <table>
                <tbody>
                  {Object.entries(v.exclusion_counts).map(([reason, n]) => (
                    <tr key={reason}>
                      <td>{reason}</td>
                      <td className="num">{n}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {v.exclusions.length > 0 && (
                <>
                  <h4>Removed individually</h4>
                  <div className="scroll">
                    <table>
                      <thead>
                        <tr><th>Scholarship</th><th>Stage</th><th>Reason</th></tr>
                      </thead>
                      <tbody>
                        {v.exclusions.map((e) => (
                          <tr key={e.scholarship_key}>
                            <td>{e.name}</td>
                            <td className="nowrap">{e.stage.replaceAll("_", " ")}</td>
                            <td>{e.reason}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </details>
          </div>

          <aside className="matches-side">
            {upcoming.length > 0 && (
              <section className="panel" aria-label="Due soon">
                <h3 style={{ margin: 0 }}>Due soon</h3>
                <ol className="plain due-list">
                  {upcoming.map((r) => {
                    const d = new Date(`${r.deadline!.slice(0, 10)}T00:00:00`);
                    const n = days(r);
                    return (
                      <li key={r.scholarship_key}>
                        <span className={`due-date ${n !== null && n <= 14 ? "urgent" : ""}`}>
                          <small>{d.toLocaleDateString("en-US", { month: "short" }).toUpperCase()}</small>
                          <b>{d.getDate()}</b>
                        </span>
                        <span>{r.name}</span>
                      </li>
                    );
                  })}
                </ol>
              </section>
            )}
            <section className="panel stack" aria-label="Advisor">
              <h3 style={{ margin: 0 }}>Ask the advisor</h3>
              <p className="muted small" style={{ margin: 0 }}>
                Questions about any match, answered from the listing.
              </p>
              <Link className="button primary" to={`/p/${pid}/advisor`}>
                Open advisor
              </Link>
            </section>
          </aside>
        </div>
      )}
    </section>
  );
}
