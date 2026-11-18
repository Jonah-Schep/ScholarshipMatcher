import { Link } from "react-router-dom";
import { useApp } from "../Layout";

export function StartPage() {
  const { profiles } = useApp();
  return (
    <section>
      <h2>Welcome</h2>
      <p>
        ScholarMatch checks your profile against every scholarship in the catalog with fixed rules first, then has AI
        agents review the real candidates and explain each match. Your profile stays on this computer.
      </p>
      {profiles.length ? (
        <>
          <p>Pick a profile:</p>
          <ul className="plain">
            {profiles.map((p) => (
              <li key={p}>
                <Link to={`/p/${p}/matches`}>{p}</Link>
              </li>
            ))}
          </ul>
          <p className="muted">Or create a new one in the sidebar.</p>
        </>
      ) : (
        <p>Create a profile in the sidebar to get started.</p>
      )}
    </section>
  );
}
