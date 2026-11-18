import { Link, useParams } from "react-router-dom";
import { api, type SavedOut, type Schemas, unwrap } from "../api/client";
import { useApp } from "../Layout";
import { amountLabel, errorText, useLoad } from "../lib";

type Status = Schemas["SavedIn"]["status"];

export function SavedPage() {
  const { pid = "" } = useParams();
  const { meta } = useApp();
  const saved = useLoad(() => unwrap(api.GET("/api/profiles/{pid}/saved", { params: { path: { pid } } })), [pid]);

  async function update(item: SavedOut, patch: { status?: string; note?: string }) {
    try {
      const body = { status: (patch.status ?? item.status) as Status, note: patch.note ?? item.note };
      await unwrap(api.PUT("/api/profiles/{pid}/saved/{key}", { params: { path: { pid, key: item.key } }, body }));
      await saved.reload();
    } catch (e) {
      window.alert(errorText(e));
    }
  }

  async function remove(key: string) {
    try {
      await unwrap(api.DELETE("/api/profiles/{pid}/saved/{key}", { params: { path: { pid, key } } }));
      await saved.reload();
    } catch (e) {
      window.alert(errorText(e));
    }
  }

  if (saved.error) return <div className="notice error">{saved.error}</div>;
  const items = saved.data ?? [];
  return (
    <section>
      <h2>Saved scholarships</h2>
      {!saved.loading && items.length === 0 && <p>Nothing saved yet. Press ☆ Save on a match to keep it here.</p>}
      {items.map((it) => {
        const snap = it.snapshot as { name?: string; url?: string; amount_display?: string; deadline?: string | null };
        return (
          <article key={it.key} className="card saved">
            <div>
              <h3>{snap.url ? <a href={snap.url} target="_blank" rel="noreferrer">{snap.name ?? it.key}</a> : (snap.name ?? it.key)}</h3>
              <p className="muted small">
                {amountLabel(snap.amount_display)} · deadline {snap.deadline || "not listed"}
                {it.ended && " · ⚠️ now closed"}
              </p>
              <input
                key={`${it.key}:${it.note}`}
                defaultValue={it.note}
                placeholder="Notes…"
                aria-label={`Notes for ${snap.name ?? it.key}`}
                onBlur={(e) => e.target.value !== it.note && update(it, { note: e.target.value })}
              />
            </div>
            <div className="stack narrow">
              <select value={it.status} onChange={(e) => update(it, { status: e.target.value })} aria-label="Status">
                {meta.saved_statuses.map((s) => (
                  <option key={s}>{s}</option>
                ))}
              </select>
              <Link className="button" to={`/p/${pid}/advisor/${encodeURIComponent(it.key)}`}>
                💬 Ask
              </Link>
              <button onClick={() => remove(it.key)}>Remove</button>
            </div>
          </article>
        );
      })}
    </section>
  );
}
