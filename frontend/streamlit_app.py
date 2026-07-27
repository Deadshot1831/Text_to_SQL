"""Phase 4.2: Streamlit UI. Talks to the FastAPI service over HTTP."""
from __future__ import annotations

import os

import httpx
import pandas as pd
import streamlit as st

API = os.environ.get("API_URL", "http://localhost:8000")

# One-click examples that each exercise a different behavior of the pipeline.
EXAMPLES = [
    "What is the gross revenue by category?",
    "Which products are out of stock?",
    "How many orders are completed?",
    "What is our revenue?",                        # ambiguous -> clarification
    "What was the weather on each order date?",    # unanswerable -> refusal
]

st.set_page_config(page_title="Text-to-SQL with Guardrails", layout="wide")
st.title("🛡️ Text-to-SQL with Guardrails & Hallucination Detection")


def api_post(path: str, payload: dict) -> dict:
    return httpx.post(f"{API}{path}", json=payload, timeout=60).json()


def api_get(path: str, params: dict | None = None) -> dict | list:
    return httpx.get(f"{API}{path}", params=params or {}, timeout=30).json()


def queue_question(q: str) -> None:
    """Button callback: schedule a question to run on the next rerun.

    Every entry point (Run, example chips, history) routes through this so the
    execution logic lives in exactly one place.
    """
    if q and q.strip():
        st.session_state["pending"] = q.strip()


# ---- sidebar: options, schema, history ----
with st.sidebar:
    st.header("Options")
    multi_query = st.checkbox("Multi-query validation", value=False,
                              help="Generate a second, independently-phrased query and check the results agree.")
    row_limit = st.number_input("Row limit", min_value=1, max_value=10000, value=1000, step=100)

    st.divider()
    with st.expander("📚 Database schema"):
        try:
            sch = api_get("/v1/schema")
            for t in sch["tables"]:
                cols = ", ".join(c["name"] for c in t["columns"])
                st.markdown(f"**{t['name']}** — {t.get('description') or ''}\n\n`{cols}`")
        except Exception as e:  # noqa: BLE001
            st.warning(f"schema unavailable: {e}")

    st.divider()
    st.subheader("History")
    st.caption("Click any past question to run it again.")
    try:
        for h in api_get("/v1/history", {"limit": 15}):
            mark = {"correct": "✅", "incorrect": "❌"}.get(h.get("feedback"), "")
            conf = f" · {h['confidence']:.0%}" if h.get("confidence") is not None else ""
            label = f"#{h['id']} [{h['status']}]{conf} {mark} {h['question']}"
            st.button(label, key=f"hist_{h['id']}", use_container_width=True,
                      on_click=queue_question, args=(h["question"],))
    except Exception as e:  # noqa: BLE001
        st.caption(f"history unavailable: {e}")


def confidence_panel(conf: dict) -> None:
    overall = conf["overall"]
    color = "green" if overall >= 0.75 else "orange" if overall >= 0.5 else "red"
    st.markdown(f"### Confidence: :{color}[{overall:.0%}]")
    breakdown = {
        "Syntax valid": conf["syntax_valid"],
        "Back-translation alignment": conf["backtranslation_alignment"],
        "Sanity pass rate": conf["sanity_pass_rate"],
        "Schema coverage": conf["schema_coverage"],
    }
    if conf.get("multiquery_agreement") is not None:
        breakdown["Multi-query agreement"] = conf["multiquery_agreement"]
    for label, val in breakdown.items():
        st.write(f"{label}: **{val:.0%}**")
        st.progress(min(max(float(val), 0.0), 1.0))


def render(resp: dict) -> None:
    status = resp["status"]
    st.subheader(f"❯ {resp.get('question', '')}")

    if status == "clarification_needed":
        clar = resp["clarification"]
        st.warning(f"🤔 {clar['question']}")
        for interp in clar["interpretations"]:
            st.markdown(f"**{interp['label']}** — {interp['description']}")
            st.code(interp["example_sql"], language="sql")
        return

    if status == "blocked":
        st.error("⛔ Query blocked by guardrails")
        for v in resp["guardrail"]["violations"]:
            st.write(f"- {v}")
        st.code(resp["guardrail"]["final_sql"], language="sql")
        return

    gen = resp.get("generated") or {}
    if gen.get("explanation"):
        st.info(gen["explanation"])

    # At-a-glance metrics.
    execu = resp.get("execution") or {}
    m1, m2, m3 = st.columns(3)
    m1.metric("Status", status.upper())
    m2.metric("Rows returned", execu.get("row_count", "—"))
    m3.metric("Exec time", f"{execu.get('execution_ms', '—')} ms")

    # Generated SQL — editable for power users (re-runs through the guardrails).
    guard = resp.get("guardrail") or {}
    shown_sql = guard.get("final_sql") or gen.get("sql", "")
    edited = st.text_area("Generated SQL (editable)", value=shown_sql, height=140)
    if edited.strip() and edited.strip() != shown_sql.strip():
        if st.button("▶ Run edited SQL"):
            new = api_post("/v1/query", {"question": resp["question"], "sql_override": edited,
                                         "row_limit": int(row_limit), "multi_query": multi_query})
            st.session_state["last"] = new
            st.rerun()

    for w in resp.get("warnings", []):
        st.warning(f"⚠️ {w}")

    if execu.get("error"):
        st.error(f"Execution error: {execu['error']}")
    elif execu.get("columns"):
        df = pd.DataFrame(execu["rows"], columns=execu["columns"])
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.caption(f"{execu['row_count']} rows · {execu['execution_ms']} ms"
                   + (" · truncated" if execu.get("truncated") else ""))
        st.download_button("⬇ Download CSV", df.to_csv(index=False).encode(),
                           file_name="results.csv", mime="text/csv")

    col1, col2 = st.columns([2, 1])
    with col1:
        val = resp.get("validation") or {}
        if val.get("backtranslation"):
            st.caption(f"🔁 Back-translation: *{val['backtranslation']}*")
        mq = val.get("multiquery")
        if mq:
            icon = "✅" if mq["agree"] else "⚠️"
            st.caption(f"{icon} Multi-query: {mq['note']}")
    with col2:
        if resp.get("confidence"):
            confidence_panel(resp["confidence"])

    # Feedback flywheel.
    if resp.get("query_id"):
        st.divider()
        c1, c2, _ = st.columns([1, 1, 6])
        if c1.button("👍 Correct"):
            api_post("/v1/feedback", {"query_id": resp["query_id"], "correct": True})
            st.toast("Thanks — saved as a few-shot candidate.")
        if c2.button("👎 Incorrect"):
            api_post("/v1/feedback", {"query_id": resp["query_id"], "correct": False})
            st.toast("Thanks — saved as an eval case.")


# ---- main input (a form, so Enter submits) ----
with st.form("ask", clear_on_submit=False):
    question = st.text_input("Ask a question about the data", key="question_box",
                             placeholder="e.g. What is the total gross revenue from completed orders?")
    submitted = st.form_submit_button("Run", type="primary")
if submitted:
    queue_question(st.session_state.get("question_box", ""))

st.caption("Or try an example — click to run:")
for col, ex in zip(st.columns(len(EXAMPLES)), EXAMPLES):
    col.button(ex, key=f"ex_{ex[:20]}", use_container_width=True,
               on_click=queue_question, args=(ex,))

# ---- single run path: whatever queued a question, execute it here ----
if "pending" in st.session_state:
    q = st.session_state.pop("pending")
    with st.spinner("Generating, guarding, executing, validating…"):
        st.session_state["last"] = api_post(
            "/v1/query",
            {"question": q, "row_limit": int(row_limit), "multi_query": multi_query},
        )

if "last" in st.session_state:
    render(st.session_state["last"])
