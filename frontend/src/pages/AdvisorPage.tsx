import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, ApiError, unwrap } from "../api/client";
import { errorText, useLoad } from "../lib";

type Turn = { id: number; role: string; content: string };

export function AdvisorPage() {
  const { pid = "", key: rawKey } = useParams();
  const navigate = useNavigate();

  // The scholarships worth asking about: the latest matches plus anything saved.
  const options = useLoad(async () => {
    const [run, saved] = await Promise.all([
      unwrap(api.GET("/api/profiles/{pid}/runs/latest", { params: { path: { pid }, query: { limit: 200 } } })).catch(
        (e) => {
          if (e instanceof ApiError && e.status === 404) return null;
          throw e;
        },
      ),
      unwrap(api.GET("/api/profiles/{pid}/saved", { params: { path: { pid } } })),
    ]);
    const map = new Map<string, string>();
    for (const r of run?.results ?? []) map.set(r.scholarship_key, r.name);
    for (const s of saved) map.set(s.key, (s.snapshot as { name?: string }).name ?? s.key);
    if (rawKey && !map.has(rawKey)) {
      // Opened by a link to a scholarship that is neither matched nor saved: name it from the catalog.
      const s = await api.GET("/api/scholarships/{key}", { params: { path: { key: rawKey } } });
      map.set(rawKey, s.data?.name ?? rawKey);
    }
    return map;
  }, [pid, rawKey]);

  const key = rawKey ?? [...(options.data?.keys() ?? [])][0];
  const history = useLoad(
    async (): Promise<Turn[]> =>
      key ? unwrap(api.GET("/api/profiles/{pid}/chat/{key}", { params: { path: { pid, key } } })) : [],
    [pid, key],
  );
  const [question, setQuestion] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState("");
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Braces matter: newer browsers return a Promise from scrollIntoView, and React would call it as a cleanup.
    end.current?.scrollIntoView({ block: "end" });
  }, [history.data, pending]);

  async function ask(e: React.FormEvent) {
    e.preventDefault();
    const q = question.trim();
    if (!q || !key) return;
    setPending(q);
    setQuestion("");
    setError("");
    try {
      await unwrap(api.POST("/api/profiles/{pid}/chat/{key}", { params: { path: { pid, key } }, body: { question: q } }));
      await history.reload();
    } catch (err) {
      setError(errorText(err));
      setQuestion(q);
    } finally {
      setPending(null);
    }
  }

  if (options.error) return <div className="notice error">{options.error}</div>;
  if (options.data && options.data.size === 0 && !rawKey) // a link to one scholarship still opens its chat
    return (
      <section>
        <h2>Ask the advisor</h2>
        <p>Find matches first, then pick one to ask about.</p>
      </section>
    );

  return (
    <section className="advisor">
      <h2>Ask the advisor</h2>
      <label className="field">
        <span>Scholarship</span>
        <select
          value={key ?? ""}
          onChange={(e) => navigate(`/p/${pid}/advisor/${encodeURIComponent(e.target.value)}`, { replace: true })}
        >
          {[...(options.data ?? new Map<string, string>())].map(([k, name]) => (
            <option key={k} value={k}>
              {name}
            </option>
          ))}
        </select>
      </label>
      <p className="muted small">
        Answers are grounded in the listing and your match analysis. The advisor won't guess facts that aren't in the
        listing, and it won't write your essays for you.
      </p>

      <div className="chat">
        {(history.data ?? []).map((t) => (
          <div key={t.id} className={`bubble ${t.role}`}>
            {t.content}
          </div>
        ))}
        {pending && (
          <>
            <div className="bubble user">{pending}</div>
            <div className="bubble assistant muted">Thinking…</div>
          </>
        )}
        <div ref={end} />
      </div>
      {error && <div className="notice error">{error}</div>}
      <form onSubmit={ask} className="row chat-input">
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          maxLength={2000}
          placeholder="e.g. What should I do first? Which essay could I reuse?"
          aria-label="Your question"
          disabled={!!pending}
        />
        <button type="submit" className="primary" disabled={!!pending || !question.trim()}>
          Ask
        </button>
      </form>
    </section>
  );
}
