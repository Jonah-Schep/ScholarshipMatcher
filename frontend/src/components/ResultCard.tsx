import { Link } from "react-router-dom";
import type { MatchResult } from "../api/client";
import { amountLabel, daysUntil, fitLabel, pct, shortDate } from "../lib";

const MARK = { pass: "✓", fail: "✕", unknown: "?" } as const;

/** A file name as a short label: "“Priya_robotics_essay.txt”" → "Priya robotics essay". */
const essayName = (title: string) =>
  title.replace(/[“”"]/g, "").replace(/\.(txt|pdf|docx?|md)$/i, "").replaceAll("_", " ").trim();

/** One match, compact: the four facts a student decides on, with the reasons one click away.
 * Text arrives with the student's own essay titles already shown (the API's `for_display`). */
export function ResultCard({
  r,
  pid,
  asOf,
  saved,
  onToggleSave,
}: {
  r: MatchResult;
  pid: string;
  asOf: string;
  saved: boolean;
  onToggleSave: () => void;
}) {
  const j = r.judgement;
  const flags = r.flags ?? [];
  const days = daysUntil(r.deadline, asOf);
  const essays = r.requirements?.essay_count;
  const reuse = j && (j.essay_reuse === "full" || j.essay_reuse === "partial") && j.reusable_essay_title;
  const amount = amountLabel(r.amount_display, r.amount_value);
  const req = Object.entries(r.requirements ?? {}).filter(
    ([, v]) => v !== null && v !== false && !(Array.isArray(v) && v.length === 0),
  );

  return (
    <article className="match">
      <div className="match-head">
        <div>
          <span className={`fit ${j?.fit === "strong" ? "strong" : ""}`}>
            {fitLabel(r)} · {Math.round(r.match_score)}
          </span>
          <h3>{r.url ? <a href={r.url} target="_blank" rel="noreferrer">{r.name}</a> : r.name}</h3>
          <p className="sub">
            {r.sponsor || r.source}
            {amount !== "Varies" && ` · ${amount}`}
          </p>
        </div>
        <button
          className={`icon ${saved ? "on" : ""}`}
          onClick={onToggleSave}
          aria-pressed={saved}
          aria-label={saved ? `Remove ${r.name} from saved` : `Save ${r.name}`}
          title={saved ? "Saved" : "Save"}
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill={saved ? "currentColor" : "none"} stroke="currentColor"
            strokeWidth="2" strokeLinejoin="round" aria-hidden="true">
            <path d="M6 3h12v18l-6-4-6 4z" />
          </svg>
        </button>
      </div>

      <dl className="facts">
        <div><dt>Chance</dt><dd>{pct(r.win_probability)}</dd></div>
        <div><dt>Effort</dt><dd>{r.effort_hours < 1 ? "<1" : Math.round(r.effort_hours)} h</dd></div>
        <div><dt>Essays</dt><dd>{essays ?? "—"}</dd></div>
        <div>
          <dt>Due</dt>
          <dd className={days !== null && days <= 14 ? "urgent" : ""}>{shortDate(r.deadline)}</dd>
        </div>
      </dl>

      {(flags.length > 0 || reuse) && (
        <div className="tags">
          {flags.length > 0 && <span className="tag warn">Verify · {flags.length}</span>}
          {reuse && <span className="tag" title={reuse}>Reuse “{essayName(reuse)}”</span>}
        </div>
      )}

      <details>
        <summary>Why this match</summary>
        {j?.summary && <p className="small">{j.summary}</p>}
        {flags.length > 0 && (
          <ul className="flags">
            {flags.map((f, i) => (
              <li key={i}>{f}</li>
            ))}
          </ul>
        )}

        {j && j.fit_evidence.length > 0 && (
          <>
            <h4>What they want · what you have</h4>
            <div className="evidence">
              {j.fit_evidence.map((e, i) => (
                <figure key={i}>
                  <blockquote>“{e.scholarship_quote}”</blockquote>
                  <figcaption className={e.student_evidence ? "" : "gap"}>
                    {e.student_evidence ? `You: ${e.student_evidence}` : `Gap: ${e.criterion}`}
                  </figcaption>
                </figure>
              ))}
            </div>
          </>
        )}

        {(r.hard_checks ?? []).length > 0 && (
          <>
            <h4>Eligibility checks</h4>
            <ul className="plain checklist">
              {r.hard_checks!.map((h, i) => (
                <li key={i}>
                  <span className={`mark ${h.result}`} aria-label={h.result}>{MARK[h.result]}</span>
                  <span><strong>{h.name}</strong> — {h.detail}</span>
                </li>
              ))}
            </ul>
          </>
        )}

        <h4>Score breakdown</h4>
        <div className="scroll">
          <table>
            <tbody>
              {(r.rubric ?? []).map((l) => (
                <tr key={l.dimension}>
                  <td>{l.dimension}</td>
                  <td className="num nowrap">{l.points} / {l.max_points}</td>
                  <td className="muted">{l.why}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="muted small">
          Chance is an estimate, not a statistic.
          {r.expected_value != null && ` Expected value ${amountLabel(undefined, r.expected_value)}.`}
          {req.length > 0 &&
            ` Needs: ${req.map(([k, v]) => `${k.replaceAll("_", " ")}${typeof v === "number" ? ` ${v}` : Array.isArray(v) ? ` (${v.join(", ")})` : ""}`).join(", ")}.`}
        </p>

        <div className="match-actions">
          {r.url && (
            <a className="button primary" href={r.url} target="_blank" rel="noreferrer">
              Open listing
            </a>
          )}
          <Link className="button" to={`/p/${pid}/advisor/${encodeURIComponent(r.scholarship_key)}`}>
            Ask the advisor
          </Link>
        </div>
      </details>
    </article>
  );
}
