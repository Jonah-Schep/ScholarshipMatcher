import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError, type Job, type RunView, unwrap } from "../api/client";
import { ResultCard } from "../components/ResultCard";
import { useApp } from "../Layout";
import { errorText, useLoad } from "../lib";

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

export function MatchesPage() {
  const { pid = "" } = useParams();
  const { meta, profiles } = useApp();
  const [sort, setSort] = useState(meta.sorts[0] ?? "Best overall match");
  const [limit, setLimit] = useState(meta.max_results);
  const [hideUnverified, setHideUnverified] = useState(false);
  const [force, setForce] = useState(false);

  const run = useLoad(async (): Promise<RunView | null> => {
    try {
      return await unwrap(
        api.GET("/api/profiles/{pid}/runs/latest", {
          params: { path: { pid }, query: { sort, limit, hide_unverified: hideUnverified } },
        }),
      );
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return null;
      throw e;
    }
  }, [pid, sort, limit, hideUnverified]);
  const saved = useLoad(() => unwrap(api.GET("/api/profiles/{pid}/saved", { params: { path: { pid } } })), [pid]);
  const savedKeys = new Set((saved.data ?? []).map((s) => s.key));
  const reloadRun = run.reload;
  const { job, running, error, start } = useRunJob(pid, reloadRun);

  if (!profiles.includes(pid))
    return (
      <section>
        <h2>Your matches</h2>
        <p>
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
  const stats = (v?.stats ?? {}) as Record<string, number>;
  return (
    <section>
      <h2>Your matches</h2>
      <div className="row">
        <button className="primary" disabled={running} onClick={() => start(force)}>
          🔎 Find my scholarships
        </button>
        <label className="check">
          <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />
          Re-run from scratch (ignore the stored result for today)
        </label>
      </div>
      {running && job && (
        <div className="progress" role="status">
          <div className="bar" style={{ width: `${Math.round(job.fraction * 100)}%` }} />
          <span>{job.message}</span>
        </div>
      )}
      {job?.state === "error" && <div className="notice error">The run failed: {job.error}</div>}
      {error && <div className="notice error">{error}</div>}
      {run.error && <div className="notice error">{run.error}</div>}

      {v === null && !running && (
        <div className="notice info">
          Press <strong>Find my scholarships</strong>. Hard rules run first; only real candidates are reviewed by the AI
          agents.
        </div>
      )}

      {v && (
        <>
          <p className="muted small">
            Run {v.run_id} · evaluated as of {v.as_of} · {v.review_mode}
            {v.stale && " · ⚠️ your profile or the catalog changed since this run — press Find to refresh"}
          </p>
          <dl className="metrics wide">
            <div><dt>In catalog</dt><dd>{stats.catalog}</dd></div>
            <div><dt>Pass hard rules</dt><dd>{stats.eligible}</dd></div>
            <div><dt>Reviewed in depth</dt><dd>{stats.reviewed}</dd></div>
            <div><dt>Matches for you</dt><dd>{stats.matched}</dd></div>
          </dl>

          {v.total_results === 0 ? (
            <div className="notice warn">
              <p>
                <strong>No scholarships match your profile right now.</strong> Nothing was hidden silently — the reasons
                are below.
              </p>
              <p>
                Things to try: add colleges you're applying to · fill in GPA, county and major · turn off no-essay only ·
                lower the minimum award.
              </p>
            </div>
          ) : (
            <>
              <div className="row controls">
                <label className="field">
                  <span>Order by</span>
                  <select value={sort} onChange={(e) => setSort(e.target.value)}>
                    {meta.sorts.map((s) => (
                      <option key={s}>{s}</option>
                    ))}
                  </select>
                </label>
                <label className="field narrow">
                  <span>Show top</span>
                  <input
                    type="number"
                    min={1}
                    max={Math.max(1, v.total_results)}
                    value={limit}
                    onChange={(e) => setLimit(Math.max(1, Number(e.target.value) || 1))}
                  />
                </label>
                <label className="check">
                  <input type="checkbox" checked={hideUnverified} onChange={(e) => setHideUnverified(e.target.checked)} />
                  Hide items needing verification
                </label>
              </div>
              <p className="muted small">
                <strong>Est. chance</strong> is a rough estimate from who can apply, how many awards exist and your fit —
                not a statistic. <strong>Expected value</strong> = chance × award.
              </p>
              {v.results.map((r, i) => (
                <ResultCard
                  key={r.scholarship_key}
                  r={r}
                  rank={i + 1}
                  pid={pid}
                  saved={savedKeys.has(r.scholarship_key)}
                  onToggleSave={() => toggleSave(r.scholarship_key)}
                />
              ))}
            </>
          )}

          <details>
            <summary>
              Why not the others? ({Object.values(v.exclusion_counts).reduce((a, b) => a + b, 0)} excluded — full audit
              trail)
            </summary>
            <table>
              <thead>
                <tr><th>Reason</th><th>Count</th></tr>
              </thead>
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
                <table>
                  <thead>
                    <tr><th>Scholarship</th><th>Stage</th><th>Reason</th></tr>
                  </thead>
                  <tbody>
                    {v.exclusions.map((e) => (
                      <tr key={e.scholarship_key}>
                        <td>{e.name}</td>
                        <td>{e.stage.replaceAll("_", " ")}</td>
                        <td>{e.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
          </details>
        </>
      )}
    </section>
  );
}
