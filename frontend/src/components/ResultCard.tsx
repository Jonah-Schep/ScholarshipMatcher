import { Link } from "react-router-dom";
import type { MatchResult } from "../api/client";
import { amountLabel, money, pct } from "../lib";

const ICON = { pass: "✅", fail: "❌", unknown: "❔" } as const;

/** One match. Text arrives with the student's own essay titles already shown (the API's `for_display`). */
export function ResultCard({
  r,
  rank,
  pid,
  saved,
  onToggleSave,
}: {
  r: MatchResult;
  rank: number;
  pid: string;
  saved: boolean;
  onToggleSave: () => void;
}) {
  const j = r.judgement;
  const req = Object.entries(r.requirements ?? {}).filter(
    ([, v]) => v !== null && v !== false && !(Array.isArray(v) && v.length === 0),
  );
  return (
    <article className="card">
      <header className="card-head">
        <div>
          <h3>
            {rank}. {r.url ? <a href={r.url} target="_blank" rel="noreferrer">{r.name}</a> : r.name}
          </h3>
          <p className="muted small">
            {r.sponsor || r.source} · apply: {r.application_method.replaceAll("_", " ")}
            {r.open_date && ` · opens ${r.open_date}`}
            {r.mode === "heuristic" && " · rule-based"}
          </p>
        </div>
        <div className="row">
          <button onClick={onToggleSave}>{saved ? "★ Saved" : "☆ Save"}</button>
          <Link className="button" to={`/p/${pid}/advisor/${encodeURIComponent(r.scholarship_key)}`}>
            💬 Ask
          </Link>
        </div>
      </header>

      <dl className="metrics">
        <div><dt>Match</dt><dd>{Math.round(r.match_score)}/100</dd></div>
        <div><dt>Est. chance</dt><dd>{pct(r.win_probability)}</dd></div>
        <div><dt>Effort</dt><dd>~{r.effort_hours} h</dd></div>
        <div><dt>Award</dt><dd>{amountLabel(r.amount_display, r.amount_value)}</dd></div>
        <div><dt>Expected value</dt><dd>{money(r.expected_value)}</dd></div>
        <div><dt>Deadline</dt><dd>{r.deadline ?? "Not listed"}</dd></div>
      </dl>

      {j?.summary && <p>{j.summary}</p>}
      {(r.flags ?? []).length > 0 && (
        <ul className="flags">
          {r.flags!.map((f, i) => (
            <li key={i}>⚠️ {f}</li>
          ))}
        </ul>
      )}

      <details>
        <summary>Why this score — rubric, evidence and eligibility checks</summary>
        <table>
          <thead>
            <tr><th>Dimension</th><th>Rating</th><th>Points</th><th>Why</th></tr>
          </thead>
          <tbody>
            {(r.rubric ?? []).map((l) => (
              <tr key={l.dimension}>
                <td>{l.dimension}</td>
                <td>{l.rating}</td>
                <td className="num">{l.points} / {l.max_points}</td>
                <td>{l.why}</td>
              </tr>
            ))}
          </tbody>
        </table>

        {(r.hard_checks ?? []).length > 0 && (
          <>
            <h4>Eligibility checks (rule-based)</h4>
            <ul className="plain">
              {r.hard_checks!.map((h, i) => (
                <li key={i}>
                  {ICON[h.result]} <strong>{h.name}</strong> — {h.detail}
                </li>
              ))}
            </ul>
          </>
        )}

        {j && j.fit_evidence.length > 0 && (
          <>
            <h4>Fit evidence (each quote was verified against the listing)</h4>
            <table>
              <thead>
                <tr><th>Looking for</th><th>Listing says</th><th>From your profile</th><th>Met</th></tr>
              </thead>
              <tbody>
                {j.fit_evidence.map((e, i) => (
                  <tr key={i}>
                    <td>{e.criterion}</td>
                    <td>“{e.scholarship_quote}”</td>
                    <td>{e.student_evidence || "— (gap)"}</td>
                    <td>{e.met ? "✅" : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}

        {j && (j.strengths.length > 0 || j.gaps.length > 0) && (
          <div className="grid2">
            <div>
              <h4>Strengths</h4>
              <ul>{j.strengths.map((s, i) => <li key={i}>{s}</li>)}</ul>
            </div>
            <div>
              <h4>Gaps</h4>
              <ul>{j.gaps.map((s, i) => <li key={i}>{s}</li>)}</ul>
            </div>
          </div>
        )}

        {j && (j.essay_reuse === "full" || j.essay_reuse === "partial") && (
          <div className="notice info">
            Essay tip: your essay {j.reusable_essay_title} looks {j.essay_reuse === "full" ? "fully" : "partly"} reusable
            here.
          </div>
        )}
        {req.length > 0 && (
          <p className="muted small">
            Application needs:{" "}
            {req.map(([k, v]) => `${k.replaceAll("_", " ")}: ${Array.isArray(v) ? v.join(", ") : String(v)}`).join(", ")}
          </p>
        )}
      </details>
    </article>
  );
}
