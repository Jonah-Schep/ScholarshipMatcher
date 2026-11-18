import { Link, NavLink, Outlet, useNavigate, useOutletContext, useParams } from "react-router-dom";
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

export const PROFILE_ID = /^[a-z0-9_-]{1,40}$/;
const PAGES = [
  ["matches", "Matches"],
  ["saved", "Saved"],
  ["advisor", "Advisor"],
  ["profile", "Profile"],
  ["data", "Data & audit"],
] as const;

export function Layout() {
  const { pid } = useParams();
  const navigate = useNavigate();
  const meta = useLoad(() => unwrap(api.GET("/api/meta")), []);
  const health = useLoad(() => unwrap(api.GET("/api/health")), []);
  const profiles = useLoad(() => unwrap(api.GET("/api/profiles")), []);

  const ready = meta.data && health.data && profiles.data;
  const failed = meta.error ?? health.error ?? profiles.error;
  const known = profiles.data ?? [];

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-inner">
          <Link to="/" className="brand">
            Scholar<span>Match</span>
          </Link>

          {pid && (
            <nav className="nav" aria-label="Main">
              {PAGES.map(([path, label]) => (
                <NavLink key={path} to={`/p/${pid}/${path}`}>
                  {label}
                </NavLink>
              ))}
            </nav>
          )}

          <div className="topbar-right">
            {health.data && (
              <span
                className={`pill ${health.data.llm_available ? "on" : ""}`}
                title={
                  health.data.llm_available
                    ? `AI agents on · ${health.data.llm_name}`
                    : "No LLM provider configured: matching works, AI fit review and chat are off."
                }
              >
                <span className="dot" />
                {health.data.llm_available ? "AI review on" : "Rule-based mode"}
              </span>
            )}
            <label>
              <span className="sr-only">Profile</span>
              <select
                className="profile-select"
                value={pid && known.includes(pid) ? pid : ""}
                onChange={(e) => {
                  const v = e.target.value;
                  if (v === "__new") navigate("/");
                  else if (v) navigate(`/p/${v}/matches`);
                }}
              >
                <option value="">{pid && !known.includes(pid) ? `${pid} (not saved yet)` : "Choose profile…"}</option>
                {known.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
                <option value="__new">+ New profile…</option>
              </select>
            </label>
          </div>
        </div>
      </header>

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

      {health.data && (
        <footer className="footer">
          <div className="footer-inner">
            <span>
              Catalog: {health.data.catalog_total.toLocaleString("en-US")} scholarships
              {health.data.catalog_updated && ` · updated ${health.data.catalog_updated.slice(0, 10)}`} · VA · NC · SC
            </span>
            <span>Your profile stays on this computer.</span>
          </div>
        </footer>
      )}
    </div>
  );
}
