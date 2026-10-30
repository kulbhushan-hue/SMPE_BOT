"""
SMP/E work simulator engine.

A tiny z/OS batch emulator: it reads real JCL text (JOB/EXEC/DD, JCLLIB, PROC overrides, SET symbols,
inline data), allocates data sets in a simulated catalog and "runs" IEFBR14, IDCAMS, IEBGENER, IEBUPDTE
and GIMSMP.  GIMSMP keeps SMP/E state: GLOBAL / TARGET / DLIB zones, SMPPTS, DDDEFs, SYSMOD status.

Nothing here executes real commands.  Message text: IDs that are real and that were confirmed are used as is
(GIM20501I, GIM24801W/S, GIM32501E, IDC00xx, IEF142I, $HASP...).  Lines starting *SIM* are teaching messages
where the exact IBM message ID is not reproduced - look the real one up in "SMP/E Messages, Codes, and Diagnosis".
"""
from __future__ import annotations

import copy
import datetime as dt
import re

DEFAULT_VOLS = {"SMPV01": 600, "WORK01": 300, "USER01": 30, "SYSRES": 0}   # free cylinders
DEFAULT_VOL = "WORK01"


# ------------------------------------------------------------------ system state
def new_system(user: str = "IBMUSER", vols: dict | None = None) -> dict:
    s = {"user": user, "vols": dict(vols or DEFAULT_VOLS), "catalog": {}, "zones": {}, "pts": {}, "scds": {},
         "jobs": [], "jobno": 100, "sysname": "ADCD"}
    for d in ("SYS1.PARMLIB", "SYS1.PROCLIB", "SYS1.LINKLIB"):
        s["catalog"][d] = {"kind": "PO", "vol": "SYSRES", "cyl": 10, "members": {}, "lines": []}
    return s


def now():
    return dt.datetime.now()


# ------------------------------------------------------------------ JCL parsing
def split_top(s: str, sep: str = ","):
    out, depth, cur, q = [], 0, "", False
    for ch in s:
        if ch == "'":
            q = not q
        if not q:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == sep and depth == 0:
                out.append(cur)
                cur = ""
                continue
        cur += ch
    out.append(cur)
    return out


def first_field(s: str) -> str:
    depth, q = 0, False
    for i, ch in enumerate(s):
        if ch == "'":
            q = not q
        if not q:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == " " and depth == 0:
                return s[:i]
    return s


def params(s: str) -> dict:
    d = {}
    for p in split_top(s):
        p = p.strip()
        if not p:
            continue
        if "=" in p and not p.startswith("("):
            k, v = p.split("=", 1)
            d[k.strip().upper()] = v.strip()
        else:
            d.setdefault("_pos", []).append(p)
    return d


def parse_jcl(text: str) -> list[dict]:
    lines = text.replace("\r", "").split("\n")
    out, i = [], 0
    while i < len(lines):
        ln = lines[i]
        if not ln.strip() or ln.startswith("//*") or ln.startswith("/*"):
            i += 1
            continue
        if ln.rstrip() == "//":
            break
        if ln.startswith("//"):
            body = ln[2:].rstrip()
            while body.endswith(",") and i + 1 < len(lines) and lines[i + 1].startswith("//") \
                    and not lines[i + 1].startswith("//*"):
                i += 1
                body += lines[i][2:].strip()
            m = re.match(r"^(\S*)\s+(\S+)\s*(.*)$", body)
            if not m:
                out.append({"name": body.strip(), "op": "", "ops": "", "data": None})
                i += 1
                continue
            name, op, rest = m.group(1), m.group(2).upper(), m.group(3)
            ops = first_field(rest)
            st = {"name": name, "op": op, "ops": ops, "data": None}
            if op == "DD" and (ops == "*" or ops.upper().startswith("DATA")):
                p = params(ops)
                dlm = p.get("DLM", "").strip("'")
                data = []
                i += 1
                while i < len(lines):
                    L = lines[i]
                    if dlm and L.startswith(dlm):
                        break
                    if not dlm and L.startswith("/*"):
                        break
                    if not dlm and ops == "*" and L.startswith("//"):
                        i -= 1
                        break
                    data.append(L.rstrip())
                    i += 1
                st["data"] = data
            out.append(st)
        i += 1
    return out


def subst(text: str, sym: dict) -> str:
    return re.sub(r"&(\w+)\.?", lambda m: sym.get(m.group(1).upper(), m.group(0)), text)


def disp_of(p: dict):
    d = p.get("DISP", "")
    if not d and p.get("_pos") and p["_pos"][0] in ("SHR", "OLD", "NEW", "MOD"):
        d = p["_pos"][0]
    d = d.strip("()")
    parts = [x.strip() for x in split_top(d)] if d else []
    return (parts[0] if parts and parts[0] else "NEW"), (parts[1] if len(parts) > 1 else "")


def space_cyl(p: dict) -> float:
    m = re.match(r"\(?\s*(CYL|TRK)\s*,\s*\(?\s*(\d+)", p.get("SPACE", ""))
    if not m:
        return 1
    n = int(m.group(2))
    return n if m.group(1) == "CYL" else max(n / 15.0, 0.07)


def dsn_split(dsn: str):
    m = re.match(r"^([^()]+)\(([^)]+)\)$", dsn)
    return (m.group(1), m.group(2)) if m else (dsn, None)


# ------------------------------------------------------------------ job submit / run
class Job:
    def __init__(self, system, jcl):
        self.sys = system
        self.jcl = jcl
        self.spool = []
        self.msglg, self.sysmsg = [], []
        self.status, self.maxcc = "OK", 0

    def log(self, msg, to="sysmsg"):
        (self.sysmsg if to == "sysmsg" else self.msglg).append(msg)


def tstamp():
    return now().strftime("%H.%M.%S")


def submit(system: dict, jcl_text: str) -> dict:
    system["jobno"] += 1
    jobid = f"JOB{system['jobno']:05d}"
    job = Job(system, jcl_text)
    user = system["user"]
    stmts = parse_jcl(jcl_text)
    sym = {"SYSUID": user}
    if not stmts or stmts[0]["op"] != "JOB":
        job.status = "JCL ERROR"
        job.log("IEFC605I UNIDENTIFIED OPERATION FIELD  (the first statement must be a JOB statement)")
        return _finish(system, job, jobid, "????????")
    jobname = stmts[0]["name"] or "NONAME"
    job.log(f"{tstamp()} {jobid}  ---- {now():%A}, {now().day:02d} {now():%b %Y} ----", "msglg")
    job.log(f"{tstamp()} {jobid}  IRR010I  USERID {user:<8} IS ASSIGNED TO THIS JOB.", "msglg")
    jcllib, steps, cur = [], [], None
    for st in stmts[1:]:
        ops = subst(st["ops"], sym)
        if st["op"] == "SET":
            for k, v in params(ops).items():
                sym[k] = v
        elif st["op"] == "JCLLIB":
            jcllib = [x.strip() for x in params(ops).get("ORDER", "").strip("()").split(",") if x.strip()]
        elif st["op"] == "EXEC":
            p = params(ops)
            proc = p.get("PROC") or (p["_pos"][0] if p.get("_pos") and not p.get("PGM") else None)
            cur = {"name": st["name"] or f"STEP{len(steps) + 1}", "pgm": p.get("PGM"), "proc": proc,
                   "parm": p.get("PARM", "").strip("'"), "dds": [], "over": []}
            steps.append(cur)
        elif st["op"] == "DD" and cur is not None:
            dd = {"name": st["name"], "p": params(ops), "data": st["data"]}
            if "." in dd["name"]:
                cur["over"].append(dd)
            else:
                cur["dds"].append(dd)
    job.log(f"{tstamp()} {jobid}  $HASP373 {jobname:<8} STARTED - INIT 1    - CLASS A - SYS {system['sysname']}", "msglg")
    final = []
    for s in steps:
        if not s["proc"]:
            final.append(s)
            continue
        member = None
        for lib in jcllib + ["SYS1.PROCLIB"]:
            ds = system["catalog"].get(lib)
            if ds is None and lib != "SYS1.PROCLIB":
                job.log(f"*SIM* JCLLIB data set {lib} IS NOT CATALOGED (did you run SMPELOAD?)")
            if ds and s["proc"] in ds.get("members", {}):
                member = ds["members"][s["proc"]]
                break
        if member is None:
            job.status = "JCL ERROR"
            job.log(f"IEFC452I {s['proc']} - PROCEDURE NOT FOUND")
            job.log(f"IEF272I {jobname} {s['name']} - STEP WAS NOT EXECUTED.")
            return _finish(system, job, jobid, jobname)
        pcur, psteps = None, []
        for st in parse_jcl("\n".join(member)):
            if st["op"] == "EXEC":
                p = params(st["ops"])
                pcur = {"name": st["name"], "pgm": p.get("PGM"), "proc": None,
                        "parm": p.get("PARM", "").strip("'"), "dds": [], "over": []}
                psteps.append(pcur)
            elif st["op"] == "DD" and pcur is not None:
                pcur["dds"].append({"name": st["name"], "p": params(st["ops"]), "data": st["data"]})
        for ps in psteps:
            for o in s["over"]:
                sname, dname = o["name"].split(".", 1)
                if sname == ps["name"]:
                    o2 = dict(o, name=dname)
                    for k, d in enumerate(ps["dds"]):
                        if d["name"] == dname:
                            ps["dds"][k] = o2
                            break
                    else:
                        ps["dds"].append(o2)
            ps["stepname"] = s["name"]
            final.append(ps)
    for s in final:
        s.setdefault("stepname", s["name"])
        if run_step(system, job, jobname, jobid, s) is None:
            break
    return _finish(system, job, jobid, jobname)


def _finish(system, job, jobid, jobname):
    job.log(f"{tstamp()} {jobid}  $HASP395 {jobname:<8} ENDED", "msglg")
    if job.status == "OK":
        job.status = f"MAXCC={job.maxcc:04d}"
    res = {"jobid": jobid, "name": jobname, "owner": system["user"], "status": job.status, "maxcc": job.maxcc,
           "spool": [("JES2", "JESMSGLG", "\n".join(job.msglg)),
                     ("JES2", "JESJCL", "\n".join(f"{n:>6} {l}" for n, l in enumerate(job.jcl.split("\n"), 1) if l.strip())),
                     ("JES2", "JESYSMSG", "\n".join(job.sysmsg))] + job.spool,
           "jcl": job.jcl, "time": now().strftime("%H:%M:%S")}
    system["jobs"].append(res)
    return res


def _alloc(system, job, jobname, step, dd):
    """Return a handle, or None when allocation fails (like JES/allocation would)."""
    p = dd["p"]
    if "DUMMY" in p.get("_pos", []):
        return {"kind": "DUMMY"}
    if "SYSOUT" in p:
        return {"kind": "SYSOUT"}
    if "DSN" not in p and dd.get("data") is not None:
        return {"kind": "INLINE", "data": dd["data"]}
    if "DSN" not in p:
        return {"kind": "TEMP"}
    dsn, mem = dsn_split(p["DSN"].strip("'"))
    status, _ = disp_of(p)
    cat = system["catalog"]
    stop = f"IEF272I {jobname} {step} - STEP WAS NOT EXECUTED."
    if status == "NEW":
        if dsn in cat:
            job.log(f"*SIM* {dsn} IS ALREADY CATALOGED - DISP=(NEW,CATLG) FAILS (real z/OS: allocation/JCL error, duplicate name)")
            job.log(stop)
            return None
        vol = p["VOL"].split("=")[-1].strip("()") if "VOL" in p else DEFAULT_VOL
        if vol not in system["vols"]:
            job.log(f"IEF244I {jobname} {step} - UNABLE TO ALLOCATE 1 UNIT(S) AT THIS TIME")
            job.log(f"*SIM* VOLUME {vol} IS NOT ONLINE. Use D U,DASD,ONLINE (or VOLS) to find a volume, then edit VOL/VOLUMES in the JCL.")
            job.log(stop)
            return None
        need = space_cyl(p)
        if system["vols"][vol] < need:
            job.log(f"*SIM* ALLOCATION FAILED: {dsn} needs {need:g} CYL but {vol} has only {system['vols'][vol]:g} free. "
                    "Choose another volume or reduce SPACE.")
            job.log(stop)
            return None
        system["vols"][vol] -= need
        kind = "PO" if "DSORG=PO" in p.get("DCB", "").replace(" ", "") else "PS"
        cat[dsn] = {"kind": kind, "vol": vol, "cyl": need, "members": {}, "lines": []}
        return {"kind": kind, "dsn": dsn, "member": mem, "new": True, "vol": vol}
    if dsn not in cat:
        job.log(f"IEF212I {jobname} {step} {dd['name']} - DATA SET NOT FOUND")
        job.log(f"*SIM* {dsn} is not cataloged. Run the earlier job that creates it.")
        job.log(stop)
        return None
    return {"kind": cat[dsn]["kind"], "dsn": dsn, "member": mem, "new": False, "vol": cat[dsn]["vol"]}


def read_dd(system, dd, h) -> list[str]:
    if h["kind"] == "INLINE":
        return list(h.get("data") or [])
    if h.get("dsn"):
        ds = system["catalog"].get(h["dsn"], {"lines": [], "members": {}})
        if h.get("member"):
            return list(ds["members"].get(h["member"], []))
        return list(ds.get("lines", []))
    return []


def run_step(system, job, jobname, jobid, s):
    name = s["stepname"]
    pgm = (s["pgm"] or "").upper()
    handles = {}
    for dd in s["dds"]:
        h = _alloc(system, job, jobname, name, dd)
        if h is None:
            job.status = "JCL ERROR"
            return None
        handles[dd["name"]] = (dd, h)
    job.log(f"{tstamp()} {jobid}  IEF403I {jobname} - STARTED - TIME={tstamp()}", "msglg")
    out, rc = {}, 0
    ctx = {"system": system, "job": job, "dds": handles, "out": out, "parm": s["parm"]}
    try:
        if pgm == "IEFBR14":
            rc = 0
        elif pgm == "IDCAMS":
            rc = prog_idcams(ctx)
        elif pgm == "IEBGENER":
            rc = prog_iebgener(ctx)
        elif pgm == "IEBUPDTE":
            rc = prog_iebupdte(ctx)
        elif pgm == "GIMSMP":
            rc = Gim(ctx).run()
        else:
            job.log(f"CSV003I REQUESTED MODULE {pgm} NOT FOUND")
            job.log(f"IEF450I {jobname} {name} - ABEND=S806 U0000 REASON=00000004")
            job.status = "ABEND S806"
            return None
    except Exception as e:  # a simulator bug is shown in the job log instead of crashing the app
        job.log(f"*SIM* the simulator could not process this step: {e!r}")
        rc = 16
    for dd_name, (dd, h) in handles.items():
        if h["kind"] == "SYSOUT":
            job.spool.append((name, dd_name, "\n".join(out.get(dd_name, []))))
        elif h.get("dsn"):
            job.log(f"IEF285I   {h['dsn']:<44}{'CATALOGED' if h.get('new') else 'KEPT'}")
            job.log(f"IEF285I   VOL SER NOS= {h['vol']:<6}.")
    job.log(f"IEF142I {jobname} {name} - STEP WAS EXECUTED - COND CODE {rc:04d}")
    job.log(f"{tstamp()} {jobid}  IEF404I {jobname} - ENDED - TIME={tstamp()}", "msglg")
    job.maxcc = max(job.maxcc, rc)
    return rc


# ------------------------------------------------------------------ utility programs
def merge_continued(lines):
    cmds, cur = [], ""
    for L in lines:
        t = L.rstrip()
        if not t.strip():
            continue
        if t.endswith("-"):
            cur += " " + t[:-1].strip()
            continue
        cur += " " + t.strip()
        cmds.append(cur.strip())
        cur = ""
    if cur.strip():
        cmds.append(cur.strip())
    return cmds


def prog_idcams(c):
    sysm, out = c["system"], c["out"].setdefault("SYSPRINT", [])
    dd = c["dds"].get("SYSIN")
    cmds = merge_continued(read_dd(sysm, *dd)) if dd else []
    out.append(f"1IDCAMS  SYSTEM SERVICES                                           TIME: {now():%H:%M:%S}        {now():%m/%d/%y}        PAGE      1")
    maxcc = lastcc = 0
    cat = sysm["catalog"]
    for cmd in cmds:
        out.append("  " + cmd[:120])
        up = cmd.upper()
        if up.startswith("DEFINE CLUSTER"):
            name = re.search(r"NAME\(\s*([\w.$#@]+)\s*\)", up).group(1)
            vol = (re.search(r"VOLUMES?\(\s*(\w+)", up) or [None, DEFAULT_VOL])[1]
            cyl = int((re.search(r"CYLINDERS?\(\s*(\d+)", up) or [None, 1])[1])
            if name in cat:
                out += ["  IDC3013I DUPLICATE DATA SET NAME", "  IDC0014I LASTCC=8"]
                lastcc = 8
            elif vol not in sysm["vols"] or sysm["vols"][vol] < cyl:
                why = "VOLUME NOT ONLINE" if vol not in sysm["vols"] else "NOT ENOUGH SPACE ON VOLUME"
                out += [f"  *SIM* DEFINE FAILED: {why} ({vol}). Edit VOLUMES(...) to a volume with free space.", "  IDC0014I LASTCC=12"]
                lastcc = 12
            else:
                sysm["vols"][vol] -= cyl
                cat[name] = {"kind": "VSAM", "vol": vol, "cyl": cyl, "members": {}, "lines": []}
                out += [f"  IDC0508I DATA ALLOCATION STATUS FOR VOLUME {vol} IS 0",
                        f"  IDC0509I INDEX ALLOCATION STATUS FOR VOLUME {vol} IS 0",
                        "  IDC0001I FUNCTION COMPLETED, HIGHEST CONDITION CODE WAS 0"]
                lastcc = 0
        elif up.startswith("DELETE"):
            name = up.split()[1]
            if name in cat:
                ds = cat.pop(name)
                if ds["vol"] in sysm["vols"]:
                    sysm["vols"][ds["vol"]] += ds["cyl"]
                drop_zones_for_csi(sysm, name)
                out.append(f"  IDC0550I ENTRY ({'C' if ds['kind'] == 'VSAM' else 'A'}) {name} DELETED")
                lastcc = 0
            else:
                out += [f"  IDC3012I ENTRY {name} NOT FOUND", f"  IDC0551I ** ENTRY {name} NOT DELETED", "  IDC0014I LASTCC=8"]
                lastcc = 8
        elif up.startswith("SET MAXCC") or up.startswith("SET LASTCC"):
            v = int(re.search(r"=\s*(\d+)", up).group(1))
            if "MAXCC" in up:
                maxcc = v
            lastcc = v
            continue
        elif up.startswith(("LISTC", "LISTCAT")):
            ent = re.search(r"ENT(?:RIES)?\(\s*'?([\w.]+)", up)
            if ent and ent.group(1) in cat:
                d = cat[ent.group(1)]
                out += [f"  {'CLUSTER' if d['kind'] == 'VSAM' else 'NONVSAM'} ------- {ent.group(1)}",
                        f"       VOLSER------------{d['vol']}   ALLOCATION: CYLINDERS---{d['cyl']:g}",
                        "  IDC0001I FUNCTION COMPLETED, HIGHEST CONDITION CODE WAS 0"]
                lastcc = 0
            else:
                out += ["  IDC3012I ENTRY NOT FOUND", "  IDC0014I LASTCC=4"]
                lastcc = 4
        else:
            out += ["  *SIM* IDCAMS COMMAND NOT SUPPORTED BY THE SIMULATOR", "  IDC0014I LASTCC=12"]
            lastcc = 12
        maxcc = max(maxcc, lastcc)
    out.append(f"  IDC0002I IDCAMS PROCESSING COMPLETE. MAXIMUM CONDITION CODE WAS {maxcc}")
    return maxcc


def drop_zones_for_csi(system, dsn):
    """Deleting a CSI wipes the zone that lives in it (and SMPPTS bookkeeping when it is the GLOBAL CSI)."""
    g = system["zones"].get("GLOBAL")
    for n, z in list(system["zones"].items()):
        if z.get("csi") == dsn:
            del system["zones"][n]
    if g and g.get("csi") == dsn:
        system["pts"].clear()
        system["scds"].clear()


def prog_iebgener(c):
    sysm, out = c["system"], c["out"].setdefault("SYSPRINT", [])
    u1, u2 = c["dds"].get("SYSUT1"), c["dds"].get("SYSUT2")
    if not u1 or not u2 or not u2[1].get("dsn"):
        out.append("*SIM* IEBGENER NEEDS SYSUT1 AND SYSUT2")
        return 12
    data = read_dd(sysm, *u1)
    ds = sysm["catalog"][u2[1]["dsn"]]
    if u2[1].get("member"):
        ds["members"][u2[1]["member"]] = data
    else:
        ds["lines"] = data
    out.append(f"*SIM* IEBGENER COPIED {len(data)} RECORD(S) TO {u2[1]['dsn']}")
    return 0


def prog_iebupdte(c):
    sysm, out = c["system"], c["out"].setdefault("SYSPRINT", [])
    sin, u2 = c["dds"].get("SYSIN"), c["dds"].get("SYSUT2")
    lines = read_dd(sysm, *sin)
    ds = sysm["catalog"][u2[1]["dsn"]]
    cur, n = None, 0
    for L in lines:
        if L.startswith("./"):
            m = re.match(r"\./\s+ADD\s+NAME=(\w+)", L)
            cur = m.group(1) if m else None
            if cur:
                ds["members"][cur] = []
                n += 1
            out.append(L)
        elif cur:
            ds["members"][cur].append(L)
    out.append(f"*SIM* IEBUPDTE ADDED {n} MEMBER(S) TO {u2[1]['dsn']}")
    return 0


# ------------------------------------------------------------------ GIMSMP (SMP/E)
def parse_sysmods(lines):
    mods, cur, elem = {}, None, None
    for L in lines:
        if L.startswith("++"):
            m = re.match(r"\+\+(\w+)\(\s*(\w+)\s*\)(.*)$", L.strip())
            if not m:
                continue
            kind, ident, rest = m.group(1).upper(), m.group(2), m.group(3)
            if kind in ("USERMOD", "PTF", "APAR", "FUNCTION"):
                cur = {"id": ident, "type": kind, "ver": [], "pre": [], "elements": []}
                mods[ident] = cur
                elem = None
            elif kind == "VER" and cur is not None:
                cur["ver"].append(ident)
                for grp in re.findall(r"PRE\(\s*([\w, ]+?)\s*\)", rest):
                    cur["pre"] += [x.strip() for x in grp.split(",") if x.strip()]
            elif cur is not None:
                elem = {"type": kind, "name": ident, "text": [],
                        "distlib": (re.search(r"DISTLIB\(\s*(\w+)", rest) or [None, ""])[1],
                        "syslib": (re.search(r"SYSLIB\(\s*(\w+)", rest) or [None, ""])[1]}
                cur["elements"].append(elem)
        elif elem is not None:
            elem["text"].append(L)
    return mods


def smp_stmts(lines):
    t = re.sub(r"/\*.*?\*/", " ", "\n".join(lines), flags=re.S)
    t = re.sub(r"\s+", " ", t)
    return [p.strip() for p in re.split(r"\s\.(?=\s|$)", t + " ") if p.strip()]


class Gim:
    def __init__(self, ctx):
        self.c, self.s, self.out = ctx, ctx["system"], ctx["out"]
        for n in ("SMPOUT", "SMPRPT", "SMPLIST", "SMPSNAP", "SYSPRINT"):
            self.out.setdefault(n, [])
        self.rc, self.bdy, self.uclin = 0, None, False

    def say(self, msg, to="SMPOUT"):
        self.out[to].append(msg)

    def err(self, msg, rc=8):
        self.say(msg)
        self.rc = max(self.rc, rc)

    def zone(self, n):
        return self.s["zones"].get(n)

    def done(self, cmd, rc):
        self.rc = max(self.rc, rc)
        self.say(f"GIM20501I {cmd} PROCESSING IS COMPLETE. THE HIGHEST RETURN CODE WAS {rc:02d}.")

    def dddef_ds(self, zone, name):
        d = zone["dddef"].get(name)
        if not d:
            return None, f"DDDEF ENTRY {name} NOT FOUND IN ZONE {zone['name']}"
        if d["dsn"] not in self.s["catalog"]:
            return None, f"DDDEF {name} POINTS TO {d['dsn']} WHICH IS NOT CATALOGED"
        return self.s["catalog"][d["dsn"]], None

    def run(self):
        cs = self.c["dds"].get("SMPCNTL")
        if not cs:
            self.err("*SIM* SMPCNTL DD STATEMENT IS MISSING", 12)
            return self.rc
        self.say(f" SMP/E  {self.c['parm'] or 'DATE=U'}      {now():%m/%d/%y} {now():%H:%M:%S}")
        for st in smp_stmts(read_dd(self.s, *cs)):
            self.say(f"   {st} .")
            self.command(st)
        return self.rc

    def command(self, st):
        verb = st.split()[0].upper()
        if self.uclin:
            if verb == "ENDUCL":
                self.uclin = False
                self.done("ENDUCL", 0)
            else:
                self.uclin_stmt(st)
            return
        try:
            getattr(self, "cmd_" + verb.lower(), self.cmd_unknown)(st)
        except Exception as e:
            self.err(f"*SIM* {verb} could not be simulated: {e!r}", 12)

    def cmd_unknown(self, st):
        self.err(f"*SIM* COMMAND '{st.split()[0]}' IS NOT SUPPORTED BY THIS SIMULATOR", 8)

    def cmd_set(self, st):
        m = re.search(r"(?:BDY|BOUNDARY)\(\s*(\w+)\s*\)", st, re.I)
        if not m:
            return self.err("*SIM* SET NEEDS BDY(zone)", 8)
        z = m.group(1).upper()
        g = self.zone("GLOBAL")
        if z != "GLOBAL":
            if not g or z not in g.get("zoneindex", {}):
                return self.err(f"*SIM* ZONE {z} IS NOT IN THE GLOBAL ZONE INDEX - define it with UCLIN first", 12)
            csi = g["zoneindex"][z][0]
            if csi not in self.s["catalog"]:
                return self.err(f"*SIM* CSI {csi} FOR ZONE {z} IS NOT CATALOGED (run the IDCAMS DEFINE job)", 12)
        self.bdy = z
        self.say(f"*SIM* ZONE {z} NOW IN EFFECT")
        self.done("SET", 0)

    def cmd_uclin(self, st):
        if not self.bdy:
            return self.err("*SIM* UCLIN NEEDS SET BDY FIRST", 8)
        self.uclin = True
        self.say("*SIM* UCLIN MODE - ADD / DEL / REP statements follow, end with ENDUCL")

    def uclin_stmt(self, st):
        op, up, zname = st.split()[0].upper(), st.upper(), self.bdy
        z = self.zone(zname)
        if op == "ADD" and re.search(r"\bGLOBALZONE\b", up):
            zi = {a: (b, c) for a, b, c in re.findall(r"\(\s*(\w+)\s*,\s*([\w.]+)\s*,\s*(\w+)\s*\)", st)}
            cs = self.c["dds"].get("SMPCSI")
            if not (cs and cs[1].get("dsn") in self.s["catalog"]):
                return self.err("*SIM* GLOBAL CSI IS NOT AVAILABLE", 12)
            self.s["zones"]["GLOBAL"] = {"name": "GLOBAL", "type": "GLOBAL", "srel": (re.search(r"SREL\(\s*(\w+)", up) or [0, ""])[1],
                                         "zoneindex": zi, "csi": cs[1]["dsn"], "dddef": (z or {}).get("dddef", {}), "sysmods": (z or {}).get("sysmods", {})}
            self.say("*SIM* GLOBALZONE AND ZONEINDEX ENTRIES ADDED")
        elif op == "ADD" and re.search(r"\b(TARGETZONE|DLIBZONE)\b", up):
            m = re.search(r"(TARGETZONE|DLIBZONE)\(\s*(\w+)", up)
            n = m.group(2)
            if n != zname:
                return self.err(f"*SIM* {m.group(1)}({n}) DOES NOT MATCH THE ZONE IN EFFECT ({zname})", 8)
            old = self.zone(n) or {}
            self.s["zones"][n] = {"name": n, "type": "TARGET" if m.group(1) == "TARGETZONE" else "DLIB",
                                  "srel": (re.search(r"SREL\(\s*(\w+)", up) or [0, ""])[1],
                                  "related": (re.search(r"RELATED\(\s*(\w+)", up) or [0, ""])[1],
                                  "csi": (self.zone("GLOBAL") or {}).get("zoneindex", {}).get(n, ("",))[0],
                                  "dddef": old.get("dddef", {}), "sysmods": old.get("sysmods", {})}
            self.say(f"*SIM* {m.group(1)} ENTRY {n} ADDED")
        elif op == "ADD" and re.search(r"\bOPTIONS\(", up):
            self.say("*SIM* OPTIONS ENTRY ADDED")
        elif op in ("ADD", "REP") and re.search(r"\bDDDEF\(", up):
            if z is None:
                z = self.s["zones"].setdefault(zname, {"name": zname, "type": "?", "srel": "", "dddef": {}, "sysmods": {}})
            n = re.search(r"DDDEF\(\s*(\w+)", up).group(1)
            da = re.search(r"DA\(\s*([\w.$#@]+)", up)
            z["dddef"][n] = {"dsn": da.group(1) if da else "", "disp": "MOD" if " MOD" in up else "SHR"}
            self.say(f"*SIM* DDDEF {n} ADDED TO ZONE {zname} -> {z['dddef'][n]['dsn']}")
        elif op == "DEL" and re.search(r"\bDDDEF\(", up):
            n = re.search(r"DDDEF\(\s*(\w+)", up).group(1)
            if z and n in z["dddef"]:
                del z["dddef"][n]
                self.say(f"*SIM* DDDEF {n} DELETED FROM ZONE {zname}")
            else:
                self.err(f"*SIM* DDDEF {n} NOT FOUND IN ZONE {zname}", 8)
        else:
            self.err(f"*SIM* UCLIN STATEMENT NOT SUPPORTED: {st[:50]}", 4)

    @staticmethod
    def selected(st):
        ids = []
        for m in re.finditer(r"\b(?:S|SELECT)\(\s*([\w\s,]+?)\s*\)", st, re.I):
            ids += re.split(r"[\s,]+", m.group(1).strip())
        return [i.upper() for i in ids if i]

    def cmd_receive(self, st):
        g = self.zone("GLOBAL")
        if self.bdy != "GLOBAL" or not g:
            return self.err("*SIM* RECEIVE MUST RUN WITH SET BDY(GLOBAL) AND A DEFINED GLOBAL ZONE", 8)
        _, why = self.dddef_ds(g, "SMPPTS")
        if why:
            return self.err(f"*SIM* {why} (RECEIVE needs the SMPPTS DDDEF)", 12)
        pf = self.c["dds"].get("SMPPTFIN")
        if not pf:
            return self.err("*SIM* SMPPTFIN DD STATEMENT IS MISSING", 12)
        mods = parse_sysmods(read_dd(self.s, *pf))
        want = self.selected(st) or list(mods)
        hit = [i for i in want if i in mods]
        for i in want:
            if i not in mods:
                self.say(f"*SIM* SYSMOD {i} IS NOT IN SMPPTFIN")
        if not hit:
            self.say("GIM24801W NO SYSMODS SATISFIED THE OPERANDS SPECIFIED ON THE RECEIVE COMMAND.")
            return self.done("RECEIVE", 4)
        rc = 0
        for i in hit:
            if i in g["sysmods"]:
                self.say(f"*SIM* SYSMOD {i} IS ALREADY RECEIVED - SKIPPED. (REJECT it first to receive it again.)")
                rc = 4
                continue
            g["sysmods"][i] = {"status": "RECEIVED", "date": now().strftime("%y.%j")}
            self.s["pts"][i] = mods[i]
            self.say(f"*SIM* SYSMOD {i} ({mods[i]['type']}) RECEIVED INTO SMPPTS AND THE GLOBAL ZONE")
        self.done("RECEIVE", rc)

    def _install(self, st, mode):
        z = self.zone(self.bdy) if self.bdy else None
        want = "TARGET" if mode == "APPLY" else "DLIB"
        if not z or z["type"] != want:
            return self.err(f"*SIM* {mode} MUST RUN WITH SET BDY({'a target' if mode == 'APPLY' else 'a distribution'} zone)", 8)
        g = self.zone("GLOBAL")
        check = bool(re.search(r"\bCHECK\b", st, re.I))
        ids = self.selected(st)
        todo = [i for i in ids if i in g["sysmods"]]
        for i in ids:
            if i not in g["sysmods"]:
                self.say(f"*SIM* SYSMOD {i} IS NOT RECEIVED (not found in the GLOBAL zone)")
        if not todo:
            self.say(f"GIM24801S ** NO SYSMODS SATISFIED THE OPERANDS SPECIFIED ON THE {mode} COMMAND.")
            return self.done(mode, 12)
        order, left = [], list(todo)
        while left:                                   # prerequisites inside the same command go first
            nxt = [i for i in left if all(p not in left or p in order for p in self.s["pts"][i]["pre"])] or left[:1]
            order.append(nxt[0])
            left.remove(nxt[0])
        worst = 0
        rel = self.zone(z.get("related", "")) if z.get("related") else None
        libkey = "syslib" if mode == "APPLY" else "distlib"
        for i in order:
            mod = self.s["pts"][i]
            have = z["sysmods"].get(i)
            if have:
                self.say(f"*SIM* SYSMOD {i} IS ALREADY {'APPLIED' if mode == 'APPLY' else 'ACCEPTED'} IN ZONE {z['name']} - SKIPPED")
                worst = max(worst, 4)
                continue
            problems = []
            if z["srel"] not in mod["ver"]:
                problems.append(f"++VER({','.join(mod['ver'])}) DOES NOT MATCH SREL {z['srel']} OF ZONE {z['name']} - SYSMOD NOT APPLICABLE")
            for pre in mod["pre"]:
                if mode == "APPLY":
                    ok = pre in z["sysmods"] or (rel and (rel["sysmods"].get(pre) or {}).get("status") == "ACC") or pre in order[:order.index(i)]
                else:
                    ok = (z["sysmods"].get(pre) or {}).get("status") == "ACC" or pre in order[:order.index(i)]
                if not ok:
                    problems.append(f"PREREQUISITE {pre} IS NOT {'APPLIED' if mode == 'APPLY' else 'ACCEPTED'} AND IS NOT IN THIS {mode}")
            for el in mod["elements"]:
                _, why = self.dddef_ds(z, el[libkey])
                if why:
                    problems.append(f"{why} (needed for element {el['name']})")
            if mode == "APPLY" and not check:
                _, why = self.dddef_ds(z, "SMPSCDS")
                if why:
                    problems.append(why)
            if problems:
                self.say(f"GIM32501E ** {mode} PROCESSING HAS FAILED FOR SYSMOD {i}.")
                for p in problems:
                    self.say(f"*SIM*   REASON: {p}")
                worst = max(worst, 8)
                continue
            if check:
                self.say(f"*SIM* CHECK ONLY: SYSMOD {i} COULD BE {'APPLIED' if mode == 'APPLY' else 'ACCEPTED'}. NOTHING WAS INSTALLED.")
                continue
            for el in mod["elements"]:
                ds, _ = self.dddef_ds(z, el[libkey])
                if mode == "APPLY":
                    self.s["scds"].setdefault(i, []).append((el[libkey], el["name"], ds["members"].get(el["name"])))
                ds["members"][el["name"]] = list(el["text"])
                self.say(f"*SIM* {el['type']} {el['name']} INSTALLED IN {z['dddef'][el[libkey]]['dsn']}")
            z["sysmods"][i] = {"status": "APP" if mode == "APPLY" else "ACC", "date": now().strftime("%y.%j")}
            if mode == "ACCEPT" and rel and i in rel["sysmods"]:
                rel["sysmods"][i]["accepted"] = True
                self.s["scds"].pop(i, None)
                self.say(f"*SIM* SMPSCDS BACKUP FOR {i} DELETED - RESTORE IS NO LONGER POSSIBLE")
            self.say(f"*SIM* SYSMOD {i} {'APPLIED' if mode == 'APPLY' else 'ACCEPTED'} IN ZONE {z['name']}")
        self.done(mode, worst)

    def cmd_apply(self, st):
        self._install(st, "APPLY")

    def cmd_accept(self, st):
        self._install(st, "ACCEPT")

    def cmd_restore(self, st):
        z = self.zone(self.bdy) if self.bdy else None
        if not z or z["type"] != "TARGET":
            return self.err("*SIM* RESTORE MUST RUN WITH SET BDY(target zone)", 8)
        worst = 0
        for i in self.selected(st):
            e = z["sysmods"].get(i)
            deps = [o for o in z["sysmods"] if o != i and i in self.s["pts"].get(o, {}).get("pre", [])]
            if not e:
                self.say(f"*SIM* RESTORE PROCESSING HAS FAILED FOR SYSMOD {i}. REASON: NOT APPLIED IN ZONE {z['name']}")
                worst = 8
            elif e.get("accepted"):
                self.say(f"*SIM* RESTORE PROCESSING HAS FAILED FOR SYSMOD {i}. REASON: SYSMOD WAS ACCEPTED - THE BACKUP IS GONE, RESTORE IS NOT POSSIBLE")
                worst = 8
            elif deps:
                self.say(f"*SIM* RESTORE PROCESSING HAS FAILED FOR SYSMOD {i}. REASON: {', '.join(deps)} REQUIRES IT (PRE) AND IS STILL APPLIED")
                worst = 8
            else:
                for lib, name, old in self.s["scds"].pop(i, []):
                    ds, _ = self.dddef_ds(z, lib)
                    if ds is None:
                        continue
                    if old is None:
                        ds["members"].pop(name, None)
                    else:
                        ds["members"][name] = old
                    self.say(f"*SIM* ELEMENT {name} REMOVED FROM {z['dddef'][lib]['dsn']}")
                del z["sysmods"][i]
                self.say(f"*SIM* SYSMOD {i} RESTORED - BACK TO RECEIVED STATUS")
        self.done("RESTORE", worst)

    def cmd_reject(self, st):
        g = self.zone("GLOBAL")
        if self.bdy != "GLOBAL" or not g:
            return self.err("*SIM* REJECT MUST RUN WITH SET BDY(GLOBAL)", 8)
        worst = 0
        for i in self.selected(st):
            if i not in g["sysmods"]:
                self.say(f"*SIM* SYSMOD {i} IS NOT RECEIVED - NOTHING TO REJECT")
                worst = max(worst, 4)
                continue
            inz = [z["name"] for z in self.s["zones"].values() if z["type"] != "GLOBAL" and i in z["sysmods"]]
            if inz:
                self.say(f"*SIM* REJECT PROCESSING HAS FAILED FOR SYSMOD {i}. REASON: STILL APPLIED/ACCEPTED IN {', '.join(inz)} (RESTORE it first)")
                worst = 8
                continue
            del g["sysmods"][i]
            self.s["pts"].pop(i, None)
            self.say(f"*SIM* SYSMOD {i} REMOVED FROM SMPPTS AND THE GLOBAL ZONE")
        self.done("REJECT", worst)

    def cmd_list(self, st):
        z = self.zone(self.bdy) if self.bdy else None
        up, to = st.upper(), "SMPLIST"
        if not z:
            self.say(f" *SIM* ZONE {self.bdy} HAS NO ENTRIES YET", to)
        elif "DDDEF" in up:
            self.say(f" {z['type']}ZONE {z['name']}  DDDEF ENTRIES", to)
            for n, d in sorted(z["dddef"].items()):
                self.say(f"   {n:<10} DATASET({d['dsn']})  {d['disp']}", to)
            if not z["dddef"]:
                self.say("   *SIM* NO DDDEF ENTRIES", to)
        else:
            self.say(f" {z['type']}ZONE {z['name']}  SYSMOD ENTRIES", to)
            self.say("   NAME      TYPE      STATUS     DATE", to)
            for i, e in sorted(z["sysmods"].items()):
                t = self.s["pts"].get(i, {}).get("type", "USERMOD")
                self.say(f"   {i:<9} {t:<9} {e['status'] + (' ACC' if e.get('accepted') else ''):<10} {e.get('date', '')}", to)
            if not z["sysmods"]:
                self.say("   *SIM* NO SYSMOD ENTRIES IN THIS ZONE", to)
        self.done("LIST", 0)

    def cmd_report(self, st):
        z = self.zone(self.bdy) if self.bdy else None
        if not z or z["type"] != "TARGET":
            return self.err("*SIM* REPORT NEEDS A TARGET ZONE IN EFFECT", 8)
        if "ERRSYSMODS" in st.upper():
            self.say(f" REPORT ERRSYSMODS - ZONE {z['name']}", "SMPRPT")
            self.say("   *SIM* NO SYSMODS WITH ERROR HOLDS (SMPHOLD is DUMMY in this lab)", "SMPRPT")
        else:
            self.say(f" REPORT SYSMODS - ZONE {z['name']}", "SMPRPT")
            dl = self.zone(z.get("related", ""))
            for i, e in sorted(z["sysmods"].items()):
                acc = "ACCEPTED" if (dl and (dl["sysmods"].get(i) or {}).get("status") == "ACC") else "NOT ACCEPTED (still restorable)"
                self.say(f"   {i:<9} APPLIED   {acc}", "SMPRPT")
            if not z["sysmods"]:
                self.say("   *SIM* NO APPLIED SYSMODS", "SMPRPT")
        self.done("REPORT", 0)


# ------------------------------------------------------------------ TSO / ISPF style helpers
def volume_lines(system):
    ls = ["IEE457I  UNIT STATUS", " UNIT TYPE STATUS  VOLSER VOLSTATE    FREE CYL"]
    for n, (v, f) in enumerate(sorted(system["vols"].items())):
        ls.append(f" 0A{80 + n:02X} 3390 O-RSDNT {v:<6} PRIV/RSDNT {f:>8g}")
    return ls


def dslist(system, level):
    lv = level.upper().strip("'")
    rows = [(d, x) for d, x in sorted(system["catalog"].items()) if d.startswith(lv)]
    ls = [f" DSLIST - Data Sets Matching {lv}", "", "  Data Set Name                                  Volume   Org    Cyl"]
    for d, x in rows:
        ls.append(f"  {d:<46} {x['vol']:<8} {x['kind']:<5} {x['cyl']:>4g}")
    ls.append(f"***End of data set list - {len(rows)} data set(s)")
    return ls


def listcat(system, level):
    lv = level.upper().strip("'")
    ls = [f"{'CLUSTER' if x['kind'] == 'VSAM' else 'NONVSAM'} ------- {d}" for d, x in sorted(system["catalog"].items()) if d.startswith(lv)]
    return ls or [f"IKJ58401I NO ENTRIES FOUND FOR {lv}"]


def browse(system, spec):
    dsn, mem = dsn_split(spec.upper().strip("'"))
    ds = system["catalog"].get(dsn)
    if not ds:
        return [f"IKJ56709I DATA SET {dsn} NOT FOUND"]
    if mem:
        if mem not in ds["members"]:
            return [f"IKJ56709I MEMBER {mem} NOT FOUND IN {dsn}"]
        return [f"{n:06d} {l}" for n, l in enumerate(ds["members"][mem], 1)] or ["(empty member)"]
    if ds["kind"] == "PO":
        return [f" Member list of {dsn}:"] + [f"   {m}" for m in sorted(ds["members"])]
    return [f"{n:06d} {l}" for n, l in enumerate(ds["lines"], 1)] or ["(empty data set)"]


def member_text(system, spec):
    dsn, mem = dsn_split(spec.upper().strip("'"))
    ds = system["catalog"].get(dsn)
    if ds and mem and mem in ds["members"]:
        return "\n".join(ds["members"][mem])
    if ds and not mem and ds["kind"] == "PS":
        return "\n".join(ds["lines"])
    return None


def job_rows(system):
    return [{"JOBNAME": j["name"], "JOBID": j["jobid"], "OWNER": j["owner"], "STATUS": "OUTPUT",
             "RETCODE": j["status"] if not j["status"].startswith("MAXCC") else f"CC {j['maxcc']:04d}", "TIME": j["time"]}
            for j in system["jobs"]]
