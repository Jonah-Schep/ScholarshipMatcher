import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { PROFILE_ID, useApp } from "../Layout";

export function StartPage() {
  const { profiles } = useApp();
  const navigate = useNavigate();
  const [newId, setNewId] = useState("");

  function createProfile(e: React.FormEvent) {
    e.preventDefault();
    const id = newId.trim().toLowerCase();
    if (!PROFILE_ID.test(id)) return;
    setNewId("");
    navigate(`/p/${id}/profile`);
  }

  return (
    <section className="stack">
      <div className="hero">
        <div className="hero-title">
          <p>VA · NC · SC</p>
          <h1>Scholarships you can actually win</h1>
        </div>
        <p className="muted" style={{ maxWidth: 620, margin: "0.9rem 0 0" }}>
          Rules check every listing first, then AI reviewers explain each real match. Your profile stays on this computer.
        </p>
      </div>

      <div className="welcome">
        <div className="panel">
          <h3>Your profiles</h3>
          {profiles.length ? (
            <div className="profile-list">
              {profiles.map((p) => (
                <Link key={p} to={`/p/${p}/matches`}>
                  {p}
                </Link>
              ))}
            </div>
          ) : (
            <p className="muted">No profiles yet. Create one to get started.</p>
          )}
        </div>

        <form onSubmit={createProfile} className="panel stack">
          <h3 style={{ margin: 0 }}>New profile</h3>
          <label className="field">
            <span>Profile name (letters, numbers, - or _)</span>
            <input
              value={newId}
              onChange={(e) => setNewId(e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, ""))}
              placeholder="e.g. jonah"
              maxLength={40}
            />
          </label>
          <button type="submit" className="primary" disabled={!PROFILE_ID.test(newId)}>
            Create profile
          </button>
        </form>
      </div>
    </section>
  );
}
