import { useState } from "react";
import { NavLink, Outlet, useNavigate, useOutletContext, useParams } from "react-router-dom";
import { api, type Health, type Meta, unwrap } from "./api/client";
import { useLoad } from "./lib";

export interface AppContext {
  meta: Meta;
  health: Health;
  profiles: string[];
  refreshProfiles: () => Promise<void>;
  refreshHealth: () => Promise<void>;
}

export const useApp = () => useOutletContext<AppContext>();

const PROFILE_ID = /^[a-z0-9_-]{1,40}$/;
const PAGES = [
  ["profile", "Profile"],
  ["matches", "Matches"],
  ["saved", "Saved"],
  ["advisor", "Ask the advisor"],
  ["data", "Data & audit"],
] as const;

export function Layout() {
  const { pid } = useParams();
  const navigate = useNavigate();
  const meta = useLoad(() => unwrap(api.GET("/api/meta")), []);
  const health = useLoad(() => unwrap(api.GET("/api/health")), []);
  const profiles = useLoad(() => unwrap(api.GET("/api/profiles")), []);
  const [newId, setNewId] = useState("");

  const ready = meta.data && health.data && profiles.data;
  const failed = meta.error ?? health.error ?? profiles.error;
  const known = profiles.data ?? [];

  function createProfile(e: React.FormEvent) {
    e.preventDefault();
    const id = newId.trim().toLowerCase();
    if (!PROFILE_ID.test(id)) return;
    setNewId("");
    navigate(`/p/${id}/profile`);
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <h1 className="brand">🎓 ScholarMatch</h1>
        <p className="muted small">Scholarships you can actually win — VA · NC · SC</p>

        <label className="field">
          <span>Profile</span>
          <select
            value={pid && known.includes(pid) ? pid : ""}
            onChange={(e) => e.target.value && navigate(`/p/${e.target.value}/matches`)}
          >
            <option value="">{pid && !known.includes(pid) ? `${pid} (not saved yet)` : "Choose…"}</option>
            {known.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </label>
        <form onSubmit={createProfile} className="field">
          <span>New profile</span>
          <div className="row">
            <input
              value={newId}
              onChange={(e) => setNewId(e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, ""))}
              placeholder="e.g. jonah"
              maxLength={40}
              aria-label="New profile name"
            />
            <button type="submit" disabled={!PROFILE_ID.test(newId)}>
              Create
            </button>
          </div>
        </form>

        {pid && (
          <nav className="nav">
            {PAGES.map(([path, label]) => (
              <NavLink key={path} to={`/p/${pid}/${path}`}>
                {label}
              </NavLink>
            ))}
          </nav>
        )}

        {health.data && (
          <div className="status">
            {health.data.llm_available ? (
              <p className="ok">AI agents on · {health.data.llm_name}</p>
            ) : (
              <p className="warn">
                Rule-based mode: no LLM provider configured. Matching works; AI fit review and chat are off.
              </p>
            )}
            <p className="muted small">
              Catalog: {health.data.catalog_total} scholarships
              {health.data.catalog_updated && ` · updated ${health.data.catalog_updated.slice(0, 10)}`}
            </p>
          </div>
        )}
      </aside>

      <main className="content">
        {failed ? (
          <div className="notice error">
            <strong>Can't reach the ScholarMatch API.</strong> Start it with <code>python -m scholarmatch.api</code>{" "}
            from the project folder, then reload. ({failed})
          </div>
        ) : !ready ? (
          <p className="muted">Loading…</p>
        ) : (
          <Outlet
            // A new profile in the URL mounts fresh pages: no matches, form edits or chat from the previous profile.
            key={pid ?? ""}
            context={
              {
                meta: meta.data!,
                health: health.data!,
                profiles: known,
                refreshProfiles: profiles.reload,
                refreshHealth: health.reload,
              } satisfies AppContext
            }
          />
        )}
      </main>
    </div>
  );
}
