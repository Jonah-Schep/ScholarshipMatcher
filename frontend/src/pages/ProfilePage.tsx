import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, ApiError, type ProfileIn, type ProfileOut, type Schemas, unwrap } from "../api/client";

type EssayOut = Schemas["EssayOut"];
import { useApp } from "../Layout";
import { errorText, splitLines, useLoad } from "../lib";

type Tri = boolean | null | undefined;
const ETHNICITIES = ["Black / African American", "Hispanic / Latino", "Native American", "Asian / Pacific Islander", "White", "Other"];
const GENDERS = ["", "female", "male", "nonbinary", "prefer not to say"];
const CLASS_YEARS = ["", "freshman", "sophomore", "junior", "senior"];
const STATES = ["VA", "NC", "SC"];

/** The form keeps free-text lists as text; `toProfileIn` turns them back into lists. */
interface Form {
  p: ProfileIn;
  majors: string;
  activities: string;
  interests: string;
  awards: string;
  affiliations: string;
}

/** A new profile: the same defaults as `StudentProfile` on the server. */
const EMPTY: ProfileIn = {
  display_name: "", state: "VA", county: "", city: "", high_school: "", level: "hs_senior", class_year: "", gpa: null,
  current_school: "", intended_schools: [], majors: [], pell_eligible: null, financial_need: null,
  us_citizen_or_resident: true, gender: "", ethnicities: [], disability: null, first_generation: null,
  military_affiliation: null, affiliations: [], no_special_affiliations: false, age: null, activities: [], interests: [],
  awards_won: [], no_essay_only: false, min_award: 0, max_effort_hours: null,
};

function toForm(p?: ProfileIn): Form {
  const base: ProfileIn = p ?? EMPTY;
  return {
    p: base,
    majors: (base.majors ?? []).join(", "),
    activities: (base.activities ?? []).join("\n"),
    interests: (base.interests ?? []).join("\n"),
    awards: (base.awards_won ?? []).join("\n"),
    affiliations: (base.affiliations ?? []).join("\n"),
  };
}

function toProfileIn(f: Form): ProfileIn {
  return {
    ...f.p,
    majors: f.majors.split(",").map((m) => m.trim()).filter(Boolean),
    activities: splitLines(f.activities),
    interests: splitLines(f.interests),
    awards_won: splitLines(f.awards),
    affiliations: splitLines(f.affiliations),
  };
}

/** The editable fields of a stored profile. Picked by name (the keys of EMPTY), so a field the server adds to
 * StudentProfile but not to ProfileIn can't make every save fail with a 422. */
function editable(out: ProfileOut): ProfileIn {
  const stored = out.profile as unknown as Record<string, unknown>;
  const picked = Object.fromEntries(Object.keys(EMPTY).filter((k) => k in stored).map((k) => [k, stored[k]]));
  return { ...EMPTY, ...picked } as ProfileIn;
}

function TriSelect({ label, value, onChange }: { label: string; value: Tri; onChange: (v: Tri) => void }) {
  return (
    <label className="field">
      <span>{label}</span>
      <select
        value={value == null ? "" : value ? "yes" : "no"}
        onChange={(e) => onChange(e.target.value === "" ? null : e.target.value === "yes")}
      >
        <option value="">Prefer not to say</option>
        <option value="yes">Yes</option>
        <option value="no">No</option>
      </select>
    </label>
  );
}

export function ProfilePage() {
  const { pid = "" } = useParams();
  const navigate = useNavigate();
  const { meta, refreshProfiles } = useApp();
  const loaded = useLoad(async () => {
    try {
      return await unwrap(api.GET("/api/profiles/{pid}", { params: { path: { pid } } }));
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return null; // a new profile
      throw e;
    }
  }, [pid]);
  const [form, setForm] = useState<Form>(toForm());
  // Essays live apart from the form: adding or removing one must not reset fields the student hasn't saved yet.
  const [essays, setEssays] = useState<EssayOut[]>([]);
  const [busy, setBusy] = useState("");
  const [notes, setNotes] = useState<string[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    if (loaded.data === undefined) return;
    setForm(toForm(loaded.data ? editable(loaded.data) : undefined));
    setEssays(loaded.data?.essays ?? []);
  }, [loaded.data]);

  const p = form.p;
  const set = (patch: Partial<ProfileIn>) => setForm((f) => ({ ...f, p: { ...f.p, ...patch } }));
  const setText = (key: keyof Omit<Form, "p">) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
    setForm((f) => ({ ...f, [key]: e.target.value }));
  const exists = !!loaded.data;

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy("Saving…");
    setError("");
    try {
      await unwrap(api.PUT("/api/profiles/{pid}", { params: { path: { pid } }, body: toProfileIn(form) }));
      await refreshProfiles();
      navigate(`/p/${pid}/matches`);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy("");
    }
  }

  async function upload(files: FileList | null) {
    if (!files?.length) return;
    setBusy("Reading your essays… (the AI pulls out themes; this can take a minute)");
    setError("");
    const body = new FormData();
    for (const f of files) body.append("files", f);
    try {
      const res = await fetch(`/api/profiles/${encodeURIComponent(pid)}/essays`, { method: "POST", body });
      const data = (await res.json().catch(() => ({}))) as Partial<Schemas["EssayUploadOut"]> & { detail?: unknown };
      if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Upload failed (${res.status}).`);
      setEssays(data.essays ?? []);
      setNotes(data.warnings ?? []);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy("");
    }
  }

  async function removeEssay(index: number) {
    try {
      const out = await unwrap(api.DELETE("/api/profiles/{pid}/essays/{index}", { params: { path: { pid, index } } }));
      setEssays(out.essays);
      setNotes([]);
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function deleteAll() {
    if (!window.confirm(`Delete profile "${pid}", its saved scholarships, match history and chats from this computer?`)) return;
    try {
      await unwrap(api.DELETE("/api/profiles/{pid}", { params: { path: { pid } } }));
      await refreshProfiles();
      navigate("/");
    } catch (err) {
      setError(errorText(err));
    }
  }

  if (loaded.loading && loaded.data === undefined) return <p className="muted">Loading…</p>;
  if (loaded.error) return <div className="notice error">{loaded.error}</div>;

  return (
    <section>
      <h2>Your profile {exists ? "" : <span className="muted small">(new: “{pid}”)</span>}</h2>
      <p className="muted small">
        Stored only on this computer. Name, exact location and demographics are used by local rule checks and are never
        sent to an AI model.
      </p>

      <form onSubmit={save} className="stack">
        <div className="grid3">
          <label className="field">
            <span>Name (optional)</span>
            <input value={p.display_name ?? ""} onChange={(e) => set({ display_name: e.target.value })} />
          </label>
          <label className="field">
            <span>I am a…</span>
            <select value={p.level ?? "hs_senior"} onChange={(e) => set({ level: e.target.value as ProfileIn["level"] })}>
              {Object.entries(meta.levels).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Class year (college students)</span>
            <select value={p.class_year ?? ""} onChange={(e) => set({ class_year: e.target.value })}>
              {CLASS_YEARS.map((c) => (
                <option key={c} value={c}>
                  {c || "—"}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="grid4">
          <label className="field">
            <span>State of residence</span>
            <select
              value={STATES.includes(p.state ?? "VA") ? p.state : "XX"}
              onChange={(e) => set({ state: e.target.value })}
            >
              {STATES.map((s) => (
                <option key={s}>{s}</option>
              ))}
              <option value="XX">Other</option>
            </select>
          </label>
          <label className="field">
            <span>County</span>
            <input value={p.county ?? ""} onChange={(e) => set({ county: e.target.value })} />
          </label>
          <label className="field">
            <span>City</span>
            <input value={p.city ?? ""} onChange={(e) => set({ city: e.target.value })} />
          </label>
          <label className="field">
            <span>High school</span>
            <input value={p.high_school ?? ""} onChange={(e) => set({ high_school: e.target.value })} />
          </label>
        </div>

        <div className="grid3">
          <label className="field">
            <span>Unweighted GPA (4.0 scale, blank = skip)</span>
            <input
              type="number"
              min={0}
              max={4}
              step={0.01}
              value={p.gpa ?? ""}
              onChange={(e) => set({ gpa: e.target.value === "" ? null : Number(e.target.value) })}
            />
          </label>
          <label className="field">
            <span>Current college</span>
            <select value={p.current_school ?? ""} onChange={(e) => set({ current_school: e.target.value })}>
              <option value="">None / other</option>
              {Object.entries(meta.schools).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          </label>
          <fieldset className="field">
            <span>Colleges I'm applying to / admitted to</span>
            <div className="checks">
              {Object.entries(meta.schools).map(([k, v]) => (
                <label key={k} className="check">
                  <input
                    type="checkbox"
                    checked={(p.intended_schools ?? []).includes(k)}
                    onChange={(e) =>
                      set({
                        intended_schools: e.target.checked
                          ? [...(p.intended_schools ?? []), k]
                          : (p.intended_schools ?? []).filter((s) => s !== k),
                      })
                    }
                  />
                  {v}
                </label>
              ))}
            </div>
          </fieldset>
        </div>
        <label className="field">
          <span>Major(s) or intended major(s), comma separated</span>
          <input value={form.majors} onChange={setText("majors")} />
        </label>

        <h3>Money</h3>
        <div className="grid4">
          <TriSelect label="Pell Grant eligible?" value={p.pell_eligible} onChange={(v) => set({ pell_eligible: v })} />
          <TriSelect label="Financial need (per FAFSA)?" value={p.financial_need} onChange={(v) => set({ financial_need: v })} />
          <label className="field">
            <span>Smallest award worth my time ($)</span>
            <input
              type="number"
              min={0}
              step={100}
              value={p.min_award ?? 0}
              onChange={(e) => set({ min_award: Number(e.target.value) || 0 })}
            />
          </label>
          <label className="check">
            <input type="checkbox" checked={!!p.no_essay_only} onChange={(e) => set({ no_essay_only: e.target.checked })} />
            Only show no-essay scholarships
          </label>
        </div>

        <h3>About you (helps the fit review)</h3>
        <div className="grid2">
          <label className="field">
            <span>Clubs, sports, jobs, volunteering — one per line</span>
            <textarea rows={5} value={form.activities} onChange={setText("activities")} />
          </label>
          <label className="field">
            <span>Interests and career goals — one per line</span>
            <textarea rows={5} value={form.interests} onChange={setText("interests")} />
          </label>
        </div>
        <label className="field">
          <span>Scholarships / honors already won — one per line</span>
          <textarea rows={2} value={form.awards} onChange={setText("awards")} />
        </label>

        <details>
          <summary>Optional: details some scholarships are restricted to (checked locally only)</summary>
          <div className="stack">
            <div className="grid2">
              <label className="field">
                <span>Gender</span>
                <select value={p.gender ?? ""} onChange={(e) => set({ gender: e.target.value })}>
                  {GENDERS.map((g) => (
                    <option key={g} value={g}>
                      {g || "—"}
                    </option>
                  ))}
                </select>
              </label>
              <fieldset className="field">
                <span>Race / ethnicity</span>
                <div className="checks">
                  {ETHNICITIES.map((x) => (
                    <label key={x} className="check">
                      <input
                        type="checkbox"
                        checked={(p.ethnicities ?? []).includes(x)}
                        onChange={(e) =>
                          set({
                            ethnicities: e.target.checked
                              ? [...(p.ethnicities ?? []), x]
                              : (p.ethnicities ?? []).filter((y) => y !== x),
                          })
                        }
                      />
                      {x}
                    </label>
                  ))}
                </div>
              </fieldset>
            </div>
            <div className="grid4">
              <TriSelect label="First-generation student?" value={p.first_generation} onChange={(v) => set({ first_generation: v })} />
              <TriSelect label="Documented disability?" value={p.disability} onChange={(v) => set({ disability: v })} />
              <TriSelect label="Military / veteran family?" value={p.military_affiliation} onChange={(v) => set({ military_affiliation: v })} />
              <TriSelect
                label="U.S. citizen / permanent resident?"
                value={p.us_citizen_or_resident}
                onChange={(v) => set({ us_citizen_or_resident: v })}
              />
            </div>
            <label className="field narrow">
              <span>Age (blank = skip). Only used for awards with an age limit.</span>
              <input
                type="number"
                min={1}
                max={120}
                value={p.age ?? ""}
                onChange={(e) => set({ age: e.target.value === "" ? null : Number(e.target.value) })}
              />
            </label>
            <label className="field">
              <span>Affiliations — one per line</span>
              <small className="muted">
                Some awards are only for a specific group: children of a company's employees, members of a club, transfer
                students. List any that fit you (for example “parent works at Smithfield Foods”). Until you add these or
                tick the box, such awards stay in your list with a Verify note and a lower estimated chance.
              </small>
              <textarea rows={3} value={form.affiliations} onChange={setText("affiliations")} />
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={!!p.no_special_affiliations}
                onChange={(e) => set({ no_special_affiliations: e.target.checked })}
              />
              None of these apply to me (hide awards limited to a specific group)
            </label>
          </div>
        </details>

        {error && <div className="notice error">{error}</div>}
        <div className="row">
          <button type="submit" className="primary" disabled={!!busy}>
            Save profile
          </button>
          {busy && <span className="muted">{busy}</span>}
        </div>
      </form>

      <h3>Past essays</h3>
      <p className="muted small">
        Upload essays you've already written (.txt or .pdf). The text is stored on this computer. It is sent to the AI
        once, with your name, county, city and high school removed, to pull out a short list of themes; after that only
        the themes are used to spot scholarships where you can reuse an essay. Other names in the essay (family,
        friends) are not removed.
      </p>
      {exists ? (
        <>
          <ul className="plain essays">
            {essays.map((e) => (
              <li key={e.index}>
                <strong>{e.title}</strong> <span className="muted small">· {e.chars} characters</span>
                {e.themes.length > 0 && <div className="small">Themes: {e.themes.join(" · ")}</div>}
                <button className="link" onClick={() => removeEssay(e.index)}>
                  Remove
                </button>
              </li>
            ))}
          </ul>
          {notes.map((n) => (
            <div key={n} className="notice warn">
              {n}
            </div>
          ))}
          <input
            type="file"
            accept=".txt,.pdf"
            multiple
            disabled={!!busy}
            onChange={(e) => {
              void upload(e.target.files);
              e.target.value = "";
            }}
            aria-label="Add essays"
          />
        </>
      ) : (
        <p className="muted">Save the profile first, then add essays.</p>
      )}

      {exists && (
        <details className="danger">
          <summary>Delete my data</summary>
          <p>Removes this profile, saved scholarships, match history and chats from this computer.</p>
          <button className="danger" onClick={deleteAll}>
            Delete everything for this profile
          </button>
        </details>
      )}
    </section>
  );
}
