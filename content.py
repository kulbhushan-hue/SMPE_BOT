"""Lesson text (from the walkthrough PDF), challenge scenarios and helper to build the original JCL members."""
import os
import re

import smpe_sim as S

HERE = os.path.dirname(os.path.abspath(__file__))


def smpeload_text() -> str:
    """SMPELOAD.jcl wherever it was uploaded (jcl/ folder, app folder, or anywhere below); built-in copy as last resort."""
    for p in (os.path.join(HERE, "jcl", "SMPELOAD.jcl"), os.path.join(HERE, "SMPELOAD.jcl"), os.path.join(os.getcwd(), "SMPELOAD.jcl")):
        if os.path.isfile(p):
            return open(p, encoding="utf-8", errors="replace").read()
    for root, _, files in os.walk(HERE):
        for f in files:
            if f.lower() in ("smpeload.jcl", "smpeload.txt", "smpeload"):
                return open(os.path.join(root, f), encoding="utf-8", errors="replace").read()
    from smpeload_data import TEXT
    return TEXT

STEPS = [
 ("SMPELOAD", "Setup: load the practice JCL", "IEFBR14 allocates SMPE.PRAC.JCL, IEBUPDTE loads all 19 members, including the cataloged procedure SMPEPRC used by every SMP/E job.", "RC 0. ISPF 3.4 with level SMPE.PRAC shows SMPE.PRAC.JCL with 19 members.", 0),
 ("J01ALLOC", "Step 1: Allocate the SMP/E data sets", "IEFBR14 allocates SMPPTS, SMPSCDS, SMPMTS, SMPSTS, SMPLOG, SMPLOGA and the two dummy libraries SDUMMYL (target) and ADUMMYL (dist).", "RC 0. ISPF 3.4 with level SMPE.PRAC shows 8 data sets (plus the JCL library).", 0),
 ("J02CSI", "Step 2: Define the three CSIs", "IDCAMS defines VSAM KSDS clusters (key length 24, record size 24-143, CI size 4096) for the global, target and dist zones.", "RC 0. LISTC ENT('SMPE.PRAC.GLOBAL.CSI') ALL shows the cluster.", 0),
 ("J03ZONES", "Step 3: Define the zones (UCLIN)", "One GIMSMP run uses UCLIN to create the GLOBAL zone and zone index, then the target and dist zones, their OPTIONS entries and DDDEFs.", "RC 0. SMPOUT shows each UCLIN processed.", 0),
 ("J04USRMD", "Step 4: Create the dummy USERMODs", "IEFBR14 allocates two sequential data sets, then IEBGENER writes the USERMOD text into them using DD DATA,DLM=@@ so the // lines are kept as data.", "RC 0. Browse SMPE.PRAC.USR0001: it starts with ++USERMOD(USR0001).", 0),
 ("J05RECV1", "Step 5: RECEIVE USR0001", "SMPPTFIN points at the sequential data set. SET BDY(GLOBAL) then RECEIVE.", "RC 0. SYSMOD is now in SMPPTS (RECEIVED).", 0),
 ("J06RECV2", "Step 6: RECEIVE USR0002", "Same for the second SYSMOD.", "RC 0.", 0),
 ("J07LIST", "Step 7: LIST the zones", "Lists SYSMODs in GLOBAL, MVST and MVSD, and the GLOBAL DDDEFs.", "GLOBAL shows both USERMODs as RECEIVED. MVST and MVSD show none yet.", 0),
 ("J08PRECK", "Step 8: Prerequisite test", "APPLY CHECK for USR0002 before USR0001 is applied.", "Non-zero RC (error): USR0002 is missing its PRE USR0001. Read the message in SMPOUT.", 8),
 ("J09APCK1", "Step 9: APPLY CHECK USR0001", "Dry run; nothing is installed. Always check before the real APPLY.", "RC 0.", 0),
 ("J10APPLY1", "Step 10: APPLY USR0001", "Installs DUMMYJ1 into SMPE.PRAC.SDUMMYL.", "RC 0. Member DUMMYJ1 exists in SDUMMYL.", 0),
 ("J11APPLY2", "Step 11: APPLY USR0002", "Now the prerequisite is satisfied.", "RC 0. SDUMMYL has DUMMYJ1 and DUMMYJ2.", 0),
 ("J12RESTR", "Step 12: RESTORE USR0002", "Backs it out of the target library using the SMPSCDS backup. Only possible before ACCEPT.", "RC 0. DUMMYJ2 is gone from SDUMMYL.", 0),
 ("J13REJCT", "Step 13: REJECT USR0002", "Removes it from SMPPTS and the GLOBAL zone.", "RC 0. J07LIST no longer shows USR0002.", 0),
 ("J14ACCK1", "Step 14: ACCEPT CHECK USR0001", "Dry run for the distribution zone.", "RC 0.", 0),
 ("J15ACPT1", "Step 15: ACCEPT USR0001", "Installs DUMMYJ1 into SMPE.PRAC.ADUMMYL and makes the change permanent.", "RC 0. RESTORE of USR0001 is no longer possible.", 0),
 ("J16REPRT", "Step 16: REPORT and LIST", "REPORT ERRSYSMODS and REPORT SYSMODS on the target zone, then LIST.", "RC 0. Reports are in SMPRPT.", 0),
 ("J17UCLIN", "Step 17: UCLIN practice", "Adds a DDDEF, lists it, deletes it, lists again.", "RC 0. The DDDEF appears in the first LIST only.", 0),
 ("J18CLEAN", "Step 18: Clean up", "Deletes every sandbox data set and CSI so you can repeat from J01.", "RC 0 (MAXCC is forced to 0 so missing items do not fail it).", 0),
]
STEP_IDS = [s[0] for s in STEPS]

CONCEPTS = """**SMP/E in one page.** SMP/E tracks software changes in zones, each a VSAM CSI data set. GLOBAL holds the staging library (SMPPTS) and the zone index. TARGET describes the running system libraries. DLIB (distribution) describes the pristine backup libraries.

| Step | Command | Zone (SET BDY) | What it does |
|---|---|---|---|
| 1 | RECEIVE | GLOBAL | Copies the SYSMOD into SMPPTS and records it |
| 2 | APPLY (CHECK first) | TARGET | Installs elements into target libraries; undo with RESTORE |
| 3 | ACCEPT (CHECK first) | DLIB | Installs into distribution libraries; makes it permanent |

REJECT removes a received SYSMOD from the GLOBAL zone and SMPPTS. RESTORE removes an applied SYSMOD from the target (not possible after ACCEPT). LIST and REPORT display and check zone contents. UCLIN edits zone entries directly, such as DDDEFs.

| RC | Meaning (general SMP/E) |
|---|---|
| 0 | Processing completed without problems |
| 4 | Warning, usually acceptable; read the message |
| 8 | Error: the command did not complete for at least one SYSMOD |
| 12 or more | Severe: stop and read the message before continuing |
"""

LINKS = [
 ("SMP/E User's Guide (primer, RECEIVE-APPLY-ACCEPT method, concepts)", "https://www.ibm.com/docs/en/SSLTBW_2.1.0/com.ibm.zos.v2r1.gim3000/toc.htm"),
 ("SMP/E Commands, SA23-2275", "https://www.ibm.com/docs/ja/SSLTBW_2.2.0/com.ibm.zos.v2r2.gim1000/toc.htm"),
 ("SMP/E Messages, Codes, and Diagnosis, GA32-0883", "https://publibz.boulder.ibm.com/epubs/pdf/gim3md12.pdf"),
 ("IBM Docs home for z/OS", "https://www.ibm.com/docs/en/zos"),
]


def original_members() -> dict:
    """Members of SMPELOAD.jcl (IEBUPDTE ./ ADD NAME=) so the editor can show every job before it is 'loaded'."""
    text = smpeload_text()
    body = text.split("DLM=##", 1)[1].split("\n", 1)[1]
    mem, cur = {}, None
    for L in body.split("\n"):
        m = re.match(r"\./\s+ADD\s+NAME=(\w+)", L)
        if m:
            cur = m.group(1)
            mem[cur] = []
        elif L.startswith("##"):
            break
        elif cur:
            mem[cur].append(L)
    return {k: "\n".join(v).rstrip() + "\n" for k, v in mem.items()}


def fix_vol(text, vol):
    return text.replace("USER00", vol)


# ---------------------------------------------------------------- scripted scenarios (practice challenges)
def _cmd_job(cmds, ptfin=None, name="PRAC"):
    d = f"// DD DISP=SHR,DSN=SMPE.PRAC.{ptfin}\n" if False else ""
    pin = f"//SMPE.SMPPTFIN DD DISP=SHR,DSN=SMPE.PRAC.{ptfin}\n" if ptfin else ""
    return (f"//{name} JOB (ACCT),'CHALLENGE',CLASS=A,MSGCLASS=X,NOTIFY=&SYSUID\n// JCLLIB ORDER=SMPE.PRAC.JCL\n"
            f"//RUN EXEC SMPEPRC\n{pin}//SMPE.SMPCNTL DD *\n{cmds}\n/*\n")


def _make_usermod(modid, ver_line, samp_line, vol="SMPV01"):
    return (f"//MK{modid[-4:]} JOB (ACCT),'MAKE {modid}',CLASS=A,MSGCLASS=X,NOTIFY=&SYSUID\n"
            f"//ALLOC EXEC PGM=IEFBR14\n//U1 DD DSN=SMPE.PRAC.{modid},DISP=(NEW,CATLG),UNIT=SYSALLDA,\n"
            f"// VOL=SER={vol},SPACE=(TRK,(1,1)),DCB=(RECFM=FB,LRECL=80,BLKSIZE=27920)\n"
            f"//GEN1 EXEC PGM=IEBGENER\n//SYSPRINT DD SYSOUT=*\n//SYSIN DD DUMMY\n"
            f"//SYSUT2 DD DSN=SMPE.PRAC.{modid},DISP=OLD\n//SYSUT1 DD DATA,DLM=@@\n++USERMOD({modid}) .\n"
            f"{ver_line}\n{samp_line}\n//{samp_line.split('(')[1].split(')')[0]} JOB (ACCT),'DUMMY SMPE',CLASS=A\n"
            f"//STEP1 EXEC PGM=IEFBR14\n@@\n")


BUILD = ["J01ALLOC", "J02CSI", "J03ZONES", "J04USRMD"]
CHALLENGES = [
 dict(n=1, title="Run J10APPLY1 before J05RECV1", learn="APPLY needs a received SYSMOD; read the 'not found' message.",
      pre=BUILD, runs=[("J10APPLY1", None)], expect="GIM24801S"),
 dict(n=2, title="Remove the SDUMMYL DDDEF with UCLIN, then run APPLY", learn="DDDEF errors and how to fix them with UCLIN.",
      pre=BUILD + ["J05RECV1", "J06RECV2"],
      runs=[("UCLIN: delete DDDEF SDUMMYL", _cmd_job(" SET BDY(MVST) .\n UCLIN .\n DEL DDDEF(SDUMMYL) .\n ENDUCL .\n LIST DDDEF .", name="CH2DEL")),
            ("J10APPLY1", None),
            ("UCLIN: put the DDDEF back (the fix)", _cmd_job(" SET BDY(MVST) .\n UCLIN .\n ADD DDDEF(SDUMMYL) DA(SMPE.PRAC.SDUMMYL) SHR .\n ENDUCL .", name="CH2FIX")),
            ("J10APPLY1", None)], expect="DDDEF ENTRY SDUMMYL NOT FOUND"),
 dict(n=3, title="Run J15ACPT1, then try a RESTORE of USR0001", learn="Why RESTORE fails after ACCEPT.",
      pre=BUILD + ["J05RECV1", "J10APPLY1", "J15ACPT1"],
      runs=[("RESTORE USR0001", _cmd_job(" SET BDY(MVST) .\n RESTORE S(USR0001) .", name="CH3REST"))], expect="RESTORE IS NOT POSSIBLE"),
 dict(n=4, title="Re-run J05RECV1 for an already received SYSMOD", learn="How SMP/E reports duplicates; REJECT first, then RECEIVE again.",
      pre=BUILD + ["J05RECV1"],
      runs=[("J05RECV1", None),
            ("REJECT USR0001", _cmd_job(" SET BDY(GLOBAL) .\n REJECT S(USR0001) .", name="CH4REJ")),
            ("J05RECV1", None)], expect="ALREADY RECEIVED"),
 dict(n=5, title="Add a third USERMOD, USR0003, with PRE(USR0002)", learn="Chains of prerequisites; select them together in one APPLY.",
      pre=BUILD + ["J05RECV1", "J06RECV2"],
      runs=[("Create USR0003 (PRE USR0002)", _make_usermod("USR0003", "++VER(Z038) PRE(USR0002) .", "++SAMP(DUMMYJ3) DISTLIB(ADUMMYL) SYSLIB(SDUMMYL) .")),
            ("RECEIVE USR0003", _cmd_job(" SET BDY(GLOBAL) .\n RECEIVE S(USR0003) .", "USR0003", "CH5REC")),
            ("APPLY USR0003 alone (fails)", _cmd_job(" SET BDY(MVST) .\n APPLY S(USR0003) .", name="CH5AP1")),
            ("APPLY USR0001 USR0002 USR0003 together", _cmd_job(" SET BDY(MVST) .\n APPLY SELECT(USR0003 USR0002 USR0001) .", name="CH5AP2"))],
      expect="PREREQUISITE USR0002"),
 dict(n=6, title="Change ++VER(Z038) to Z999 in a copy, then RECEIVE and APPLY", learn="SREL mismatch errors.",
      pre=BUILD + ["J05RECV1"],
      runs=[("Create USR0004 with ++VER(Z999)", _make_usermod("USR0004", "++VER(Z999) .", "++SAMP(DUMMYJ4) DISTLIB(ADUMMYL) SYSLIB(SDUMMYL) .")),
            ("RECEIVE USR0004", _cmd_job(" SET BDY(GLOBAL) .\n RECEIVE S(USR0004) .", "USR0004", "CH6REC")),
            ("APPLY USR0004", _cmd_job(" SET BDY(MVST) .\n APPLY S(USR0004) .", name="CH6APP"))], expect="DOES NOT MATCH SREL"),
 dict(n=7, title="Name a DISTLIB that has no DDDEF in MVSD, then ACCEPT", learn="ACCEPT needs the DLIB DDDEF.",
      pre=BUILD + ["J05RECV1"],
      runs=[("Create USR0005 (DISTLIB NODLIB)", _make_usermod("USR0005", "++VER(Z038) .", "++SAMP(DUMMYJ5) DISTLIB(NODLIB) SYSLIB(SDUMMYL) .")),
            ("RECEIVE USR0005", _cmd_job(" SET BDY(GLOBAL) .\n RECEIVE S(USR0005) .", "USR0005", "CH7REC")),
            ("ACCEPT USR0005", _cmd_job(" SET BDY(MVSD) .\n ACCEPT S(USR0005) .", name="CH7ACC"))], expect="DDDEF ENTRY NODLIB NOT FOUND"),
 dict(n=8, title="Run J18CLEAN, then rebuild from J01 without notes", learn="Repeatability: the goal is to do it from memory.",
      pre=BUILD + ["J05RECV1", "J06RECV2", "J10APPLY1", "J11APPLY2"],
      runs=[("J18CLEAN", None)] + [(m, None) for m in BUILD + ["J05RECV1", "J06RECV2", "J10APPLY1", "J11APPLY2"]], expect="MAXCC"),
]


def run_challenge(ch, orig, vol="SMPV01"):
    """Build a scratch system, run the prerequisite jobs quietly, then the scripted runs; return list of results."""
    sysm = S.new_system()
    S.submit(sysm, smpeload_text())
    for m in ch["pre"]:
        S.submit(sysm, fix_vol(orig[m], vol))
    res = []
    for label, jcl in ch["runs"]:
        text = jcl if jcl else fix_vol(orig[label], vol)
        res.append((label, S.submit(sysm, text)))
    return sysm, res


def spool_text(job, ddname):
    for _, dd, t in job["spool"]:
        if dd == ddname:
            return t
    return ""
