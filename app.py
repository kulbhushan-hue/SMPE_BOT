"""
SMP/E Work Simulator (ADCD z/OS 1.12 walkthrough) - Streamlit app, old-MVS green screen.

Tabs: 1 Guided lab (J01-J18)   2 TSO/ISPF terminal   3 SDSF   4 SMP/E zones   5 Practice challenges   6 Concepts & links
The JCL from the walkthrough PDF is used as *simulation examples*: you edit and submit the JCL text, and the
simulator interprets it (JOB/EXEC/DD, PROC, IEFBR14, IDCAMS, IEBGENER, IEBUPDTE, GIMSMP) against a simulated system.
No real command is ever run.   Run:  streamlit run app.py
"""
import html
import re

import pandas as pd
import streamlit as st

import content as C
import smpe_sim as S

st.set_page_config(page_title="SMP/E Work Simulator", page_icon="🟩", layout="wide")

st.markdown("""
<style>
.stApp{background:#000;color:#33ff33;font-family:'Courier New',monospace}
h1,h2,h3,h4,p,label,span,li,div[data-testid="stMarkdownContainer"]{font-family:'Courier New',monospace!important}
.term{background:#000;color:#33ff33;border:1px solid #1d7a1d;padding:10px;white-space:pre;overflow:auto;
      max-height:520px;font-family:'Courier New',monospace;font-size:13px;line-height:1.25}
.term .bad{color:#ff5555}.term .warn{color:#ffff55}.term .sim{color:#66ccff}.term .ok{color:#33ff33}
div[data-baseweb="textarea"] textarea,div[data-baseweb="input"] input{background:#000!important;color:#33ff33!important;
      font-family:'Courier New',monospace!important}
.flow{display:flex;gap:6px;align-items:center;margin:4px 0;font-size:13px}
.box{border:1px solid #1d7a1d;padding:4px 10px;min-width:150px;text-align:center}
.box.on{background:#0b3d0b;border-color:#33ff33}.box.off{color:#2c6b2c}
</style>""", unsafe_allow_html=True)

ORIG = C.original_members()


# ------------------------------------------------------------------ state
def system():
    if "sys" not in st.session_state:
        st.session_state["sys"] = S.new_system()
        st.session_state["term"] = ["READY", "TSO/E LOGON IN PROGRESS...", "", "Type HELP for the command list."]
        st.session_state["last"] = None
    return st.session_state["sys"]


def colour(text: str) -> str:
    out = []
    for ln in text.split("\n"):
        e = html.escape(ln)
        if "*SIM*" in ln:
            e = f'<span class="sim">{e}</span>'
        elif re.search(r"GIM\d+E|GIM\d+S|RETURN CODE WAS (0[8-9]|1\d|2\d)|NOT FOUND|ABEND|FAILED|JCL ERROR|IEF212I|IEF244I", ln):
            e = f'<span class="bad">{e}</span>'
        elif re.search(r"GIM\d+W|RETURN CODE WAS 04|ALREADY", ln):
            e = f'<span class="warn">{e}</span>'
        out.append(e)
    return "\n".join(out)


def term(text: str, height: int = 520):
    st.markdown(f'<div class="term" style="max-height:{height}px">{colour(text)}</div>', unsafe_allow_html=True)


def show_job(job, expected=None, where="lab"):
    ok = None if expected is None else ((job["maxcc"] == 0) if expected == 0 else (job["maxcc"] >= 4)) and not job["status"].startswith(("JCL", "ABEND"))
    head = f"{job['name']}  ({job['jobid']})   {job['status']}"
    if expected is not None:
        head += "   ✅ as the PDF says" if ok else f"   ⚠️ the PDF expects {'RC 0' if expected == 0 else 'a non-zero RC'}"
    st.markdown(f"**{head}**")
    names = [f"{s}:{d}" if s != "JES2" else d for s, d, _ in job["spool"]]
    pref = next((i for i, (s, d, _) in enumerate(job["spool"]) if d in ("SMPOUT", "SYSPRINT")), 0)
    key = f"sp_{where}_{job['jobid']}"
    pick = st.radio("Job output (SDSF style)", names, index=pref, horizontal=True, key=key)
    term(job["spool"][names.index(pick)][2] or "(empty)", 420)


# ------------------------------------------------------------------ sidebar
sysm = system()
with st.sidebar:
    st.markdown("### 🟩 SMP/E SIMULATOR")
    st.caption("ADCD z/OS 1.12 walkthrough, simulated. Nothing real is run.")
    vol = st.selectbox("My DASD volume", [v for v in sysm["vols"] if v != "SYSRES"],
                       help="The PDF says to change USER00 to a volume with 100+ free cylinders (D U,DASD,ONLINE).")
    auto = st.checkbox("Auto-replace USER00 with my volume when loading JCL", value=True)
    st.caption("Free cylinders: " + ", ".join(f"{v} {f:g}" for v, f in sysm["vols"].items()))
    if st.button("♻️ Reset whole system", key="reset"):
        for k in ("sys", "term", "last"):
            st.session_state.pop(k, None)
        for k in [k for k in st.session_state if k.startswith(("ed_", "sp_"))]:
            st.session_state.pop(k, None)
        st.rerun()
    st.caption("Message note: lines in blue starting *SIM* are teaching messages. GIM20501I, GIM24801, GIM32501E and the "
               "IDC/IEF/$HASP lines follow the real formats; check the SMP/E Messages book for exact wording.")

st.title("SMP/E Work Simulator")
tabs = st.tabs(["1 · Guided lab", "2 · TSO / ISPF terminal", "3 · SDSF", "4 · SMP/E zones", "5 · Practice challenges", "6 · Concepts & links"])

# ================================================================== 1 guided lab
with tabs[0]:
    ids = C.STEP_IDS
    cur = st.session_state.get("lab_step", ids[0])
    c1, c2 = st.columns([1, 3])
    with c1:
        st.markdown("**Order of play**")
        sel = st.radio("Job", ids, index=ids.index(cur), key="lab_radio", label_visibility="collapsed",
                       format_func=lambda i: ("✅ " if any(j["name"] == i and j["maxcc"] == 0 and not j["status"].startswith(("JCL", "ABEND")) for j in sysm["jobs"]) else "⬜ ") + i)
        st.session_state["lab_step"] = sel
    meta = next(m for m in C.STEPS if m[0] == sel)
    with c2:
        st.subheader(meta[1])
        st.markdown(f"**What it does:** {meta[2]}")
        st.markdown(f"**Expect:** {meta[3]}")
        if sel != "SMPELOAD" and "SMPEPRC" not in sysm["catalog"].get("SMPE.PRAC.JCL", {}).get("members", {}):
            st.warning("SMPE.PRAC.JCL is not loaded yet. Run SMPELOAD first (the procedure SMPEPRC lives there).")
        src = C.smpeload_text() if sel == "SMPELOAD" else ORIG[sel]
        if auto:
            src = C.fix_vol(src, vol)
        ek = f"ed_{sel}_{vol}_{auto}"
        if ek not in st.session_state:
            st.session_state[ek] = src
        text = st.text_area("JCL (edit it like in ISPF, then submit)", key=ek, height=300 if sel != "J04USRMD" else 380)
        b1, b2, _ = st.columns([1, 1, 4])
        if b1.button("▶ SUBMIT", type="primary", key=f"sub_{sel}"):
            st.session_state["last"] = S.submit(sysm, text)
            st.rerun()
        if b2.button("↺ Original JCL", key=f"orig_{sel}"):
            st.session_state[ek] = src
            st.rerun()
        job = st.session_state.get("last")
        if job:
            exp = next((m[4] for m in C.STEPS if m[0] == job["name"]), None)
            show_job(job, exp)
            if job["name"] == sel and sel in ids[:-1]:
                st.caption(f"Next job in the walkthrough: **{ids[ids.index(sel) + 1]}**  (pick it on the left)")

# ================================================================== 2 terminal
HELP = """Commands (TSO / ISPF / console style):
  3.4 SMPE.PRAC                  data set list (ISPF 3.4)
  BROWSE 'SMPE.PRAC.USR0001'     show a sequential data set      BROWSE 'SMPE.PRAC.SDUMMYL(DUMMYJ1)'  show a member
  BROWSE 'SMPE.PRAC.JCL'         member list of a library
  LISTC LEVEL(SMPE.PRAC)         catalog entries (TSO LISTCAT)
  D U,DASD,ONLINE  or  VOLS      online volumes and free cylinders
  SUB 'SMPE.PRAC.JCL(J01ALLOC)'  submit a member from the simulated library
  ST                             job list (SDSF ST)
  CLEAR                          clear the screen"""


def tso(cmd: str) -> list[str]:
    c = cmd.strip()
    u = c.upper()
    if not c:
        return []
    if u == "HELP":
        return HELP.split("\n")
    if u == "CLEAR":
        st.session_state["term"] = []
        return []
    if u.startswith("3.4"):
        return S.dslist(sysm, u[3:].strip() or "SMPE.PRAC")
    if u.startswith("BROWSE") or u.startswith("B "):
        return S.browse(sysm, c.split(None, 1)[1]) if len(c.split(None, 1)) > 1 else ["IKJ56700A ENTER DATA SET NAME"]
    if u.startswith(("LISTC", "LISTCAT")):
        m = re.search(r"LEV(?:EL)?\(\s*'?([\w.]+)", u)
        return S.listcat(sysm, m.group(1) if m else "SMPE.PRAC")
    if u.startswith("D U") or u == "VOLS":
        return S.volume_lines(sysm)
    if u.startswith("SUB"):
        spec = c.split(None, 1)[1] if len(c.split(None, 1)) > 1 else ""
        t = S.member_text(sysm, spec)
        if t is None:
            return [f"IKJ56709I {spec} NOT FOUND - run SMPELOAD first, or check the name"]
        if auto:
            t = C.fix_vol(t, vol)
        j = S.submit(sysm, t)
        st.session_state["last"] = j
        return [f"JOB {j['name']}({j['jobid']}) SUBMITTED", f"$HASP165 {j['name']} ENDED AT {sysm['sysname']}  MAXCC={j['maxcc']:04d}" if j["status"].startswith("MAXCC") else f"$HASP... {j['name']} {j['status']}"]
    if u in ("ST", "SDSF ST", "SDSF"):
        rows = S.job_rows(sysm)
        return [f"{'JOBNAME':<9}{'JOBID':<10}{'OWNER':<9}{'STATUS':<8}{'RETCODE'}"] + [f"{r['JOBNAME']:<9}{r['JOBID']:<10}{r['OWNER']:<9}{r['STATUS']:<8}{r['RETCODE']}" for r in rows] if rows else ["NO JOBS"]
    return [f"IKJ56500I COMMAND {u.split()[0]} NOT FOUND (type HELP)"]


with tabs[1]:
    st.markdown("**ISPF 3.4 / TSO READY** - type commands like on a 3270 screen.")
    with st.form("tso_form", clear_on_submit=True):
        cmd = st.text_input("Command ===>", key="tso_cmd")
        go = st.form_submit_button("ENTER")
    if go and cmd:
        st.session_state["term"] += ["", f"READY", f"{cmd}"] + tso(cmd)
    q = st.columns(5)
    for i, (lbl, c) in enumerate([("3.4 SMPE.PRAC", "3.4 SMPE.PRAC"), ("VOLS", "VOLS"), ("LISTC", "LISTC LEVEL(SMPE.PRAC)"),
                                  ("ST", "ST"), ("HELP", "HELP")]):
        if q[i].button(lbl, key=f"q_{lbl}"):
            st.session_state["term"] += ["", "READY", c] + tso(c)
            st.rerun()
    term("\n".join(st.session_state["term"][-400:]), 480)

# ================================================================== 3 SDSF
with tabs[2]:
    rows = S.job_rows(sysm)
    st.markdown("**SDSF  ST - Status display**")
    if not rows:
        st.info("No jobs yet. Submit one from the Guided lab or the terminal.")
    else:
        st.dataframe(pd.DataFrame(rows[::-1]), hide_index=True, width="stretch")
        pick = st.selectbox("Open job output", [f"{j['name']} ({j['jobid']}) {j['status']}" for j in sysm["jobs"][::-1]], key="sdsf_pick")
        show_job(sysm["jobs"][::-1][[f"{j['name']} ({j['jobid']}) {j['status']}" for j in sysm["jobs"][::-1]].index(pick)], where="sdsf")

# ================================================================== 4 zones
with tabs[3]:
    st.markdown("**Where is each SYSMOD?**  RECEIVE → GLOBAL (SMPPTS), APPLY → target zone MVST, ACCEPT → dist zone MVSD")
    mods = sorted(set(sysm["pts"]) | {i for z in sysm["zones"].values() for i in z["sysmods"]})
    if not mods:
        st.info("No SYSMODs known yet. Run J05RECV1 / J06RECV2 after the sandbox is built.")
    for m in mods:
        g, t, d = (sysm["zones"].get(n, {}).get("sysmods", {}).get(m) for n in ("GLOBAL", "MVST", "MVSD"))
        mod = sysm["pts"].get(m, {})
        pre = f"   PRE({', '.join(mod['pre'])})" if mod.get("pre") else ""
        st.markdown(f"**{m}**{pre}")
        st.markdown('<div class="flow">' + "".join(
            f'<div class="box {"on" if x else "off"}">{lbl}<br>{"●" if x else "○"}</div>{arr}'
            for lbl, x, arr in [("RECEIVED<br>GLOBAL / SMPPTS", g, " → "), ("APPLIED<br>MVST / SDUMMYL", t, " → "), ("ACCEPTED<br>MVSD / ADUMMYL", d, "")]) + "</div>",
            unsafe_allow_html=True)
    z1, z2 = st.columns(2)
    with z1:
        st.markdown("**Zones and DDDEFs**")
        zr = [{"ZONE": n, "TYPE": z["type"], "SREL": z.get("srel", ""), "DDDEFS": ", ".join(sorted(z["dddef"])) or "-",
               "SYSMODS": ", ".join(f"{i}:{e['status']}" for i, e in z["sysmods"].items()) or "-"} for n, z in sysm["zones"].items()]
        st.dataframe(pd.DataFrame(zr) if zr else pd.DataFrame(columns=["ZONE"]), hide_index=True, width="stretch")
    with z2:
        st.markdown("**Sandbox data sets (SMPE.PRAC)**")
        dr = [{"DSN": d, "ORG": x["kind"], "VOL": x["vol"], "CYL": x["cyl"],
               "MEMBERS": ", ".join(sorted(x["members"])[:6]) + (" ..." if len(x["members"]) > 6 else "")}
              for d, x in sorted(sysm["catalog"].items()) if d.startswith("SMPE.PRAC") and d != "SMPE.PRAC.JCL"]
        st.dataframe(pd.DataFrame(dr) if dr else pd.DataFrame(columns=["DSN"]), hide_index=True, width="stretch")

# ================================================================== 5 challenges
with tabs[4]:
    st.markdown("Each challenge builds a scratch system, runs the needed earlier jobs, then runs the scenario so you can "
                "read the messages. You can also try it yourself in the Guided lab (edit the JCL, then submit).")
    for ch in C.CHALLENGES:
        with st.expander(f"{ch['n']}. {ch['title']}"):
            st.markdown(f"**You should learn:** {ch['learn']}")
            st.caption("Earlier jobs run first: " + ", ".join(ch["pre"]))
            if st.button("▶ Run this scenario", key=f"ch_{ch['n']}"):
                st.session_state[f"chres_{ch['n']}"] = C.run_challenge(ch, ORIG, vol)[1]
            for label, job in st.session_state.get(f"chres_{ch['n']}", []):
                st.markdown(f"**{label}** → {job['name']} {job['status']}")
                body = "\n".join(C.spool_text(job, d) for d in ("SMPOUT", "SYSPRINT") if C.spool_text(job, d))
                term(body or "(no SMPOUT)", 260)

# ================================================================== 6 concepts
with tabs[5]:
    st.markdown(C.CONCEPTS)
    st.markdown("**IBM documentation** (use the V3R6 books for syntax; z/OS 1.12 ships an earlier SMP/E level).")
    for name, url in C.LINKS:
        st.link_button(name, url)
    st.caption("Where to look: User's Guide chapter 'SMP/E primer' for the flow; 'Allocating a CSI data set' for J02; "
               "'Specifying the zone to be processed: SET' for SET BDY; the Commands book 'APPLY CHECK facility' and 'ACCEPT CHECK facility'.")
