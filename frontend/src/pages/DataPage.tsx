import { useParams } from "react-router-dom";
import { api, ApiError, unwrap } from "../api/client";
import { useLoad } from "../lib";

type Counts = Record<string, number>;

function CountTable({ title, counts }: { title: string; counts: Counts }) {
  return (
    <div>
      <h4>{title}</h4>
      <table>
        <tbody>
          {Object.entries(counts)
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([k, n]) => (
              <tr key={k}>
                <td>{k}</td>
                <td className="num">{n}</td>
              </tr>
            ))}
        </tbody>
      </table>
    </div>
  );
}

export function DataPage() {
  const { pid = "" } = useParams();
  const stats = useLoad(() => unwrap(api.GET("/api/catalog/stats")), []);
  const audit = useLoad(async () => {
    try {
      const run = await unwrap(api.GET("/api/profiles/{pid}/runs/latest", { params: { path: { pid }, query: { limit: 1 } } }));
      const log = await unwrap(api.GET("/api/runs/{run_id}/audit", { params: { path: { run_id: run.run_id } } }));
      return { run, log };
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return null;
      throw e;
    }
  }, [pid]);

  const s = stats.data as { by_source?: Counts; by_status?: Counts; scam?: Counts; llm_extracted?: number } | undefined;
  return (
    <section>
      <h2>Data &amp; audit</h2>
      {stats.error && <div className="notice error">{stats.error}</div>}
      {s && (
        <>
          <div className="grid3">
            <CountTable title="By source" counts={s.by_source ?? {}} />
            <CountTable title="By status (ended awards are kept, never shown)" counts={s.by_status ?? {}} />
            <CountTable title="Scam screen" counts={s.scam ?? {}} />
          </div>
          <p className="muted small">AI-extracted criteria cached for {s.llm_extracted ?? 0} scholarships.</p>
        </>
      )}

      {audit.error && <div className="notice error">{audit.error}</div>}
      {audit.data && (
        <>
          <h3>Latest run {audit.data.run.run_id}</h3>
          <details>
            <summary>Run statistics</summary>
            <pre>{JSON.stringify(audit.data.run.stats, null, 2)}</pre>
          </details>
          <h4>Supervisor log — every accept / reject decision</h4>
          <div className="scroll">
            <table>
              <thead>
                <tr><th>Time</th><th>Agent</th><th>Event</th><th>Detail</th></tr>
              </thead>
              <tbody>
                {audit.data.log.map((e, i) => (
                  <tr key={i}>
                    <td className="nowrap">{String(e.ts ?? "").slice(11, 19)}</td>
                    <td>{String(e.agent)}</td>
                    <td>{String(e.event)}</td>
                    <td className="detail">{typeof e.detail === "string" ? e.detail : JSON.stringify(e.detail)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {audit.data === null && <p className="muted">No match run yet for this profile.</p>}
    </section>
  );
}
