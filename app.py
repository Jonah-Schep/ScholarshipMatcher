"""ScholarMatch — Streamlit front end.     Run:  streamlit run app.py"""
from __future__ import annotations

import io
import re

import pandas as pd
import streamlit as st

from scholarmatch import config, db
from scholarmatch.agents import workers
from scholarmatch.agents.chat import ChatAgent
from scholarmatch.agents.supervisor import Supervisor
from scholarmatch.llm import get_llm
from scholarmatch.matching import rubric
from scholarmatch.models import Essay, MatchResult, StudentProfile
from scholarmatch.run_summary import review_mode_label
from scholarmatch.textutil import is_zero_amount, money

st.set_page_config(page_title="ScholarMatch", page_icon="🎓", layout="wide")

LEVELS = {"hs_senior": "High school senior / incoming freshman", "undergrad": "Current college student",
          "graduate": "Graduate student"}
SCHOOL_OPTS = {k: v[0] for k, v in config.SCHOOLS.items()}
ETHNICITIES = ["Black / African American", "Hispanic / Latino", "Native American", "Asian / Pacific Islander", "White", "Other"]
YES_NO = {"Prefer not to say": None, "Yes": True, "No": False}
SAVED_STATUS = ["interested", "applying", "submitted", "won", "not selected"]


@st.cache_resource
def llm():
    return get_llm()


def lines(text: str) -> list[str]:
    return [x.strip() for x in re.split(r"[\n;]+", text or "") if x.strip()]


def yn_index(value) -> int:
    return list(YES_NO.values()).index(value)


# ----------------------------------------------------------------------------- sidebar
def sidebar() -> tuple[str, str]:
    with st.sidebar:
        st.title("🎓 ScholarMatch")
        st.caption("Scholarships you can actually win — VA · NC · SC")
        profiles = db.list_profiles()
        # Navigation requested by a button on the previous run must be applied before the widgets exist.
        if "goto" in st.session_state:
            st.session_state["page"] = st.session_state.pop("goto")
        if st.session_state.get("pending_profile") in profiles:
            st.session_state["profile_choice"] = st.session_state.pop("pending_profile")
        if st.session_state.get("profile_choice") not in profiles + ["➕ New profile"]:
            st.session_state.pop("profile_choice", None)
        st.session_state.setdefault("page", "Matches" if profiles else "Profile")
        choice = st.selectbox("Profile", profiles + ["➕ New profile"], key="profile_choice")
        if choice == "➕ New profile":
            new = st.text_input("Choose a profile name", placeholder="e.g. jonah").strip().lower()
            pid = re.sub(r"[^a-z0-9_-]", "", new)
        else:
            pid = choice
        page = st.radio("Go to", ["Profile", "Matches", "Saved", "Ask the advisor", "Data & audit"], key="page")
        st.divider()
        if llm().available:
            st.success(f"AI agents on · {llm().name}")
        else:
            st.warning("Rule-based mode: no LLM key configured. Matching works; AI fit review and chat are off. See README.")
        stats = db.catalog_stats()
        st.caption(f"Catalog: {stats['total']} scholarships" + (f" · updated {stats['last_scraped'][:10]}" if stats["last_scraped"] else ""))
    return pid, page


# ----------------------------------------------------------------------------- profile page
def page_profile(pid: str) -> None:
    st.header("Your profile")
    if not pid:
        st.info("Pick a profile name in the sidebar to get started.")
        return
    p = db.load_profile(pid) or StudentProfile(profile_id=pid)
    st.caption("Stored only on this computer. Name, exact location and demographics are used by local rule "
               "checks and are never sent to an AI model.")
    with st.form("profile"):
        c1, c2, c3 = st.columns(3)
        display_name = c1.text_input("Name (optional)", p.display_name)
        level = c2.selectbox("I am a…", list(LEVELS), format_func=LEVELS.get, index=list(LEVELS).index(p.level))
        class_year = c3.selectbox("Class year (college students)", ["", "freshman", "sophomore", "junior", "senior"],
                                  index=["", "freshman", "sophomore", "junior", "senior"].index(p.class_year or ""))
        c1, c2, c3, c4 = st.columns(4)
        state = c1.selectbox("State of residence", ["VA", "NC", "SC", "Other"],
                             index=["VA", "NC", "SC", "Other"].index(p.state if p.state in ("VA", "NC", "SC") else "Other"))
        county = c2.text_input("County", p.county)
        city = c3.text_input("City", p.city)
        high_school = c4.text_input("High school", p.high_school)
        c1, c2, c3 = st.columns(3)
        gpa = c1.number_input("Unweighted GPA (4.0 scale, 0 = skip)", 0.0, 4.0, float(p.gpa or 0.0), 0.01)
        current = c2.selectbox("Current college", ["", *SCHOOL_OPTS], format_func=lambda k: SCHOOL_OPTS.get(k, "None / other"),
                               index=(["", *SCHOOL_OPTS].index(p.current_school) if p.current_school in SCHOOL_OPTS else 0))
        intended = c3.multiselect("Colleges I'm applying to / admitted to", list(SCHOOL_OPTS), format_func=SCHOOL_OPTS.get,
                                  default=[s for s in p.intended_schools if s in SCHOOL_OPTS])
        majors = st.text_input("Major(s) or intended major(s), comma separated", ", ".join(p.majors))

        st.subheader("Money")
        c1, c2, c3, c4 = st.columns(4)
        pell = c1.selectbox("Pell Grant eligible?", list(YES_NO), index=yn_index(p.pell_eligible))
        need = c2.selectbox("Financial need (per FAFSA)?", list(YES_NO), index=yn_index(p.financial_need))
        min_award = c3.number_input("Smallest award worth my time ($)", 0, 50000, int(p.min_award), 100)
        no_essay = c4.checkbox("Only show no-essay scholarships", p.no_essay_only)

        st.subheader("About you (helps the fit review)")
        c1, c2 = st.columns(2)
        activities = c1.text_area("Clubs, sports, jobs, volunteering — one per line", "\n".join(p.activities), height=140)
        interests = c2.text_area("Interests and career goals — one per line", "\n".join(p.interests), height=140)
        awards = st.text_area("Scholarships / honors already won — one per line", "\n".join(p.awards_won), height=70)

        with st.expander("Optional: details some scholarships are restricted to (checked locally only)"):
            c1, c2 = st.columns(2)
            gender = c1.selectbox("Gender", ["", "female", "male", "nonbinary", "prefer not to say"],
                                  index=["", "female", "male", "nonbinary", "prefer not to say"].index(p.gender or ""))
            eth = c2.multiselect("Race / ethnicity", ETHNICITIES, default=[e for e in p.ethnicities if e in ETHNICITIES])
            c1, c2, c3, c4 = st.columns(4)
            first_gen = c1.selectbox("First-generation student?", list(YES_NO), index=yn_index(p.first_generation))
            disability = c2.selectbox("Documented disability?", list(YES_NO), index=yn_index(p.disability))
            military = c3.selectbox("Military / veteran family?", list(YES_NO), index=yn_index(p.military_affiliation))
            citizen = c4.selectbox("U.S. citizen / permanent resident?", list(YES_NO), index=yn_index(p.us_citizen_or_resident))
            st.markdown("**Affiliations**")
            st.caption("Some awards are only for a specific group: children of a company's employees, descendants "
                       "of a named family, members of a club, transfer students, holders of another scholarship. "
                       "List any that fit you (for example \"parent works at Smithfield Foods\", \"USATT member\", "
                       "\"transferring from a community college\"). Until you add these or tick the box, such awards "
                       "stay in your list with a Verify note and a lower estimated chance.")
            affiliations = st.text_area("Affiliations — one per line", "\n".join(p.affiliations), height=90)
            no_special = st.checkbox("None of these apply to me (hide awards limited to a specific group)",
                                     p.no_special_affiliations)

        st.subheader("Past essays")
        st.caption("Upload essays you've already written (.txt or .pdf). The app keeps the text locally and only "
                   "shares a short list of themes with the AI, to spot scholarships where you can reuse them.")
        uploads = st.file_uploader("Add essays", type=["txt", "pdf"], accept_multiple_files=True)
        keep = st.multiselect("Essays on file", [e.title for e in p.essays], default=[e.title for e in p.essays])
        submitted = st.form_submit_button("Save profile", type="primary")

    if submitted:
        essays = [e for e in p.essays if e.title in keep]
        for up in uploads or []:
            text = read_upload(up)
            if text and up.name not in [e.title for e in essays]:
                with st.spinner(f"Reading {up.name}…"):
                    try:
                        themes = workers.essay_themes(llm(), up.name, text, (display_name,))
                    except Exception as e:
                        themes = []
                        st.warning(f"Couldn't summarise {up.name} ({e}); saved without themes.")
                essays.append(Essay(title=up.name, text=text, themes=themes))
        db.save_profile(StudentProfile(
            profile_id=pid, display_name=display_name, level=level, class_year=class_year,
            state=state if state != "Other" else "XX", county=county, city=city, high_school=high_school,
            gpa=gpa or None, current_school=current, intended_schools=intended,
            majors=[m.strip() for m in majors.split(",") if m.strip()], pell_eligible=YES_NO[pell],
            financial_need=YES_NO[need], min_award=float(min_award), no_essay_only=no_essay,
            activities=lines(activities), interests=lines(interests), awards_won=lines(awards),
            gender=gender, ethnicities=eth, first_generation=YES_NO[first_gen], disability=YES_NO[disability],
            military_affiliation=YES_NO[military], us_citizen_or_resident=YES_NO[citizen],
            affiliations=lines(affiliations), no_special_affiliations=no_special, essays=essays))
        st.session_state["pending_profile"] = pid
        st.session_state["goto"] = "Matches"
        st.rerun()

    if db.load_profile(pid):
        with st.expander("Delete my data"):
            st.write("Removes this profile, saved scholarships, match history and chats from this computer.")
            if st.button("Delete everything for this profile", type="secondary"):
                db.delete_profile(pid)
                st.session_state["goto"] = "Profile"
                st.rerun()


def read_upload(up) -> str:
    data = up.getvalue()
    if up.name.lower().endswith(".pdf"):
        try:
            from pypdf import PdfReader
            return "\n".join((pg.extract_text() or "") for pg in PdfReader(io.BytesIO(data)).pages).strip()
        except Exception as e:
            st.warning(f"Could not read {up.name}: {e}")
            return ""
    return data.decode("utf-8", errors="replace").strip()


# ----------------------------------------------------------------------------- matches page
def page_matches(pid: str) -> None:
    st.header("Your matches")
    p = db.load_profile(pid) if pid else None
    if not p:
        st.info("Create and save a profile first.")
        return
    if db.catalog_stats()["total"] == 0:
        st.error("The scholarship catalog is empty. Run `python scripts/load_sample.py` for a small sample, or "
                 "`python -m scholarmatch.scrapers.runner` for the full scrape (see README).")
        return
    sup = Supervisor(llm())
    c1, c2 = st.columns([1, 3])
    go = c1.button("🔎 Find my scholarships", type="primary")
    fresh = c2.checkbox("Re-run from scratch (ignore the stored result for today)", False)
    run = db.latest_run(pid)
    if go:
        bar = st.progress(0.0, "Starting…")
        run = sup.run(p, force=fresh, progress=lambda msg, frac: bar.progress(min(1.0, frac), msg))
        bar.empty()
    if not run:
        st.info("Press **Find my scholarships**. Hard rules run first; only real candidates are reviewed by the AI agents.")
        return

    stale = run.fingerprint != sup.fingerprint(p, config_today())
    s = run.stats
    st.caption(f"Run {run.run_id} · evaluated as of {run.as_of} · {review_mode_label(run)}"
               + (" · ⚠️ your profile or the catalog changed since this run — press Find to refresh" if stale else ""))
    m = st.columns(4)
    m[0].metric("In catalog", s["catalog"])
    m[1].metric("Pass hard rules", s["eligible"])
    m[2].metric("Reviewed in depth", s["reviewed"])
    m[3].metric("Matches for you", s["matched"])

    if not run.results:                                           # R13: explicit no-match notification
        st.warning("**No scholarships match your profile right now.** Nothing was hidden silently — the reasons are below.")
        reasons = dict(s["sql_excluded"])
        for e in run.exclusions:
            reasons[e.stage.replace("_", " ")] = reasons.get(e.stage.replace("_", " "), 0) + 1
        st.dataframe(pd.DataFrame(sorted(reasons.items(), key=lambda kv: -kv[1]), columns=["Why scholarships were excluded", "Count"]), hide_index=True, width="stretch")
        st.markdown("**Things to try:** add colleges you're applying to · fill in GPA, county and major · "
                    "turn off *no-essay only* · lower the minimum award · re-run the scrapers to refresh the catalog.")
        show_exclusions(run)
        return

    c1, c2, c3 = st.columns([2, 1, 1])
    order = c1.selectbox("Order by", list(rubric.SORTS))
    top_n = c2.number_input("Show top", 1, max(1, len(run.results)), min(config.MAX_RESULTS, len(run.results)))
    hide_unverified = c3.checkbox("Hide items needing verification", False)
    results = rubric.sort_results(run.results, order)
    if hide_unverified:
        results = [r for r in results if not any(f.startswith(("Verify", "Check")) for f in r.flags)]
    results = results[: int(top_n)]

    st.dataframe(pd.DataFrame([{
        "Scholarship": r.name, "Match": r.match_score, "Est. chance": r.win_probability, "Effort (h)": r.effort_hours,
        "Award": r.amount_display, "Expected value": r.expected_value, "Deadline": r.deadline or "—",
        "Fit": r.judgement.fit if r.judgement else "", "From": r.source} for r in results]),
        hide_index=True, width="stretch",
        column_config={"Match": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f"),
                       "Est. chance": st.column_config.NumberColumn(format="percent"),
                       "Expected value": st.column_config.NumberColumn(format="dollar")})
    st.caption("**Est. chance** is a rough estimate from who can apply, how many awards exist and your fit — not a "
               "statistic. **Expected value** = chance × award.")

    saved = {x["key"] for x in db.list_saved(pid)}
    for i, r in enumerate(results, 1):
        result_card(pid, r, i, r.scholarship_key in saved)
    show_exclusions(run)


def config_today() -> str:
    from scholarmatch.textutil import today_iso
    return today_iso()


def result_card(pid: str, r: MatchResult, rank: int, is_saved: bool) -> None:
    with st.container(border=True):
        head, act = st.columns([5, 1])
        title = f"{rank}. [{r.name}]({r.url})" if r.url else f"{rank}. {r.name}"
        head.markdown(f"#### {title}")
        head.caption(f"{r.sponsor or r.source} · apply: {r.application_method.replace('_', ' ')}"
                     + (f" · opens {r.open_date}" if r.open_date else ""))
        if is_saved:
            if act.button("★ Saved", key=f"unsave-{r.scholarship_key}"):
                db.unsave_scholarship(pid, r.scholarship_key)
                st.rerun()
        elif act.button("☆ Save", key=f"save-{r.scholarship_key}"):
            db.save_scholarship(pid, r.scholarship_key, r.model_dump())
            st.rerun()
        if act.button("💬 Ask", key=f"ask-{r.scholarship_key}"):
            st.session_state["chat_key"] = r.scholarship_key
            st.session_state["goto"] = "Ask the advisor"
            st.rerun()
        m = st.columns(6)
        m[0].metric("Match", f"{r.match_score:.0f}/100")
        m[1].metric("Est. chance", f"{r.win_probability:.0%}")
        m[2].metric("Effort", f"~{r.effort_hours:g} h")
        m[3].metric("Award", money(r.amount_value) if r.amount_value else "Varies")
        m[4].metric("Expected value", money(r.expected_value))
        m[5].metric("Deadline", r.deadline or "Not listed")
        if r.judgement and r.judgement.summary:
            st.write(r.judgement.summary)
        for f in r.flags:
            st.markdown(f"- ⚠️ {f}")
        with st.expander("Why this score — rubric, evidence and eligibility checks"):
            st.table(pd.DataFrame([{"Dimension": l.dimension, "Rating": l.rating, "Points": f"{l.points:g} / {l.max_points:g}",
                                    "Why": l.why} for l in r.rubric]).set_index("Dimension"))
            if r.hard_checks:
                icon = {"pass": "✅", "fail": "❌", "unknown": "❔"}
                st.markdown("**Eligibility checks (rule-based)**")
                for h in r.hard_checks:
                    st.markdown(f"- {icon[h.result]} **{h.name}** — {h.detail}")
            j = r.judgement
            if j and j.fit_evidence:
                st.markdown("**Fit evidence (each quote was verified against the listing)**")
                st.table(pd.DataFrame([{"Looking for": e.criterion, "Listing says": f"“{e.scholarship_quote}”",
                                        "From your profile": e.student_evidence or "— (gap)", "Met": "✅" if e.met else "—"}
                                       for e in j.fit_evidence]).set_index("Looking for"))
            if j and (j.strengths or j.gaps):
                a, b = st.columns(2)
                a.markdown("**Strengths**\n" + "\n".join(f"- {x}" for x in j.strengths))
                b.markdown("**Gaps**\n" + "\n".join(f"- {x}" for x in j.gaps))
            if j and j.essay_reuse in ("full", "partial"):
                st.info(f"Essay tip: your essay “{j.reusable_essay_title}” looks {j.essay_reuse}ly reusable here.")
            req = r.requirements.model_dump(exclude_none=True)
            if req:
                st.caption("Application needs: " + ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in req.items() if v not in ([], False)))


def show_exclusions(run) -> None:
    with st.expander(f"Why not the others? ({sum(run.stats['sql_excluded'].values()) + len(run.exclusions)} excluded — full audit trail)"):
        st.markdown("**Removed by database rules**")
        st.dataframe(pd.DataFrame(sorted(run.stats["sql_excluded"].items(), key=lambda kv: -kv[1]), columns=["Reason", "Count"]), hide_index=True, width="stretch")
        if run.exclusions:
            st.markdown("**Removed individually**")
            st.dataframe(pd.DataFrame([{"Scholarship": e.name, "Stage": e.stage.replace("_", " "), "Reason": e.reason}
                                       for e in run.exclusions]), hide_index=True, width="stretch")


# ----------------------------------------------------------------------------- saved page
def page_saved(pid: str) -> None:
    st.header("Saved scholarships")
    items = db.list_saved(pid) if pid else []
    if not items:
        st.info("Nothing saved yet. Press ☆ Save on a match to keep it here.")
        return
    for it in items:
        snap, key = it["snapshot"], it["key"]
        live = db.get_scholarship(key)
        with st.container(border=True):
            a, b, c = st.columns([4, 2, 1])
            a.markdown(f"**[{snap.get('name', key)}]({snap.get('url', '')})**")
            amt = snap.get("amount_display", "")
            a.caption(f"{'Varies' if not amt or is_zero_amount(amt) else amt} · deadline {snap.get('deadline') or 'not listed'}"
                      + (" · ⚠️ now closed" if live and live.status == "ended" else ""))
            status = b.selectbox("Status", SAVED_STATUS, index=SAVED_STATUS.index(it["status"]), key=f"st-{key}", label_visibility="collapsed")
            note = a.text_input("Note", it["note"], key=f"note-{key}", label_visibility="collapsed", placeholder="Notes…")
            if status != it["status"] or note != it["note"]:
                db.save_scholarship(pid, key, snap, status, note)
            if c.button("Remove", key=f"rm-{key}"):
                db.unsave_scholarship(pid, key)
                st.rerun()
            if c.button("💬 Ask", key=f"ask-saved-{key}"):
                st.session_state["chat_key"] = key
                st.session_state["goto"] = "Ask the advisor"
                st.rerun()


# ----------------------------------------------------------------------------- chat page
def page_chat(pid: str) -> None:
    st.header("Ask the advisor")
    p = db.load_profile(pid) if pid else None
    run = db.latest_run(pid) if pid else None
    options: dict[str, str] = {r.scholarship_key: r.name for r in (run.results if run else [])}
    options.update({x["key"]: x["snapshot"].get("name", x["key"]) for x in (db.list_saved(pid) if pid else [])})
    if not p or not options:
        st.info("Find matches first, then pick one to ask about.")
        return
    keys = list(options)
    preset = st.session_state.get("chat_key")
    key = st.selectbox("Scholarship", keys, format_func=options.get, index=keys.index(preset) if preset in keys else 0)
    st.caption("Answers are grounded in the listing and your match analysis. The advisor won't guess facts that "
               "aren't in the listing, and it won't write your essays for you.")
    for t in db.chat_history(pid, key):
        st.chat_message(t["role"]).write(t["content"])
    q = st.chat_input("e.g. What should I do first? Which essay could I reuse? How does this compare to my other matches?")
    if q:
        st.chat_message("user").write(q)
        with st.chat_message("assistant"), st.spinner("Thinking…"):
            st.write(ChatAgent(llm(), p, run).ask(key, q))


# ----------------------------------------------------------------------------- data page
def page_data(pid: str) -> None:
    st.header("Data & audit")
    stats = db.catalog_stats()
    a, b, c = st.columns(3)
    a.markdown("**By source**")
    a.dataframe(pd.DataFrame(sorted(stats["by_source"].items()), columns=["Source", "Rows"]), hide_index=True, width="stretch")
    b.markdown("**By status** (ended awards are kept, never shown)")
    b.dataframe(pd.DataFrame(sorted(stats["by_status"].items()), columns=["Status", "Rows"]), hide_index=True, width="stretch")
    c.markdown("**Scam screen**")
    c.dataframe(pd.DataFrame(sorted(stats["scam"].items()), columns=["Level", "Rows"]), hide_index=True, width="stretch")
    st.caption(f"AI-extracted criteria cached for {stats['llm_extracted']} scholarships · database: {config.DB_PATH}")
    run = db.latest_run(pid) if pid else None
    if run:
        st.subheader(f"Latest run {run.run_id}")
        st.json({k: v for k, v in run.stats.items()}, expanded=False)
        log = db.audit_for(run.run_id)
        if log:
            st.markdown("**Supervisor log** — every accept / reject decision")
            st.dataframe(pd.DataFrame(log), hide_index=True, width="stretch")


PAGES = {"Profile": page_profile, "Matches": page_matches, "Saved": page_saved,
         "Ask the advisor": page_chat, "Data & audit": page_data}

pid, page = sidebar()
PAGES[page](pid)
