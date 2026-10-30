# SMP/E Work Simulator (ADCD z/OS 1.12 walkthrough)

Streamlit app with an old-MVS green-on-black screen. It simulates the whole SMP/E walkthrough from
`SMPE_Walkthrough_ADCD_zOS112.pdf`: the JCL in the PDF (and `jcl/SMPELOAD.jcl`) is used as **simulation examples**.
You edit and submit the JCL text; the simulator reads it (JOB / EXEC / DD, JCLLIB, PROC SMPEPRC overrides,
SET symbols, inline DD * / DD DATA) and "runs" it against a simulated z/OS system. No real command is executed.

    pip install -r requirements.txt
    streamlit run app.py          (or run.bat / run.sh)

Deploy note: keep all files in one folder (a flat GitHub upload works too). `SMPELOAD.jcl` is found in `jcl/`, next to `app.py`, or anywhere below it; if it is missing, a built-in copy (`smpeload_data.py`) is used.

## Tabs
1. **Guided lab** - SMPELOAD, J01ALLOC ... J18CLEAN in the PDF's order, with "What it does / Expect" from the PDF,
   an editable JCL box, SUBMIT, SDSF-style output (JESMSGLG, JESJCL, JESYSMSG, SMPOUT, SMPRPT, SMPLIST, SYSPRINT) and a
   check against the RC the PDF expects (J08PRECK must fail on purpose).
2. **TSO / ISPF terminal** - `3.4 SMPE.PRAC`, `BROWSE`, `LISTC LEVEL(...)`, `D U,DASD,ONLINE` / `VOLS`, `SUB 'SMPE.PRAC.JCL(J01ALLOC)'`, `ST`.
3. **SDSF** - job list and output of every job.
4. **SMP/E zones** - where each SYSMOD is: RECEIVED (GLOBAL/SMPPTS) -> APPLIED (MVST) -> ACCEPTED (MVSD), zones, DDDEFs, data sets.
5. **Practice challenges** - the 8 challenges from the PDF, each runnable as a scripted scenario.
6. **Concepts & links** - SMP/E in one page, return codes, the IBM documentation links from the PDF.

## What is simulated
* Programs: IEFBR14 (allocation: NEW,CATLG / OLD / SHR, volume and space checks), IDCAMS (DEFINE CLUSTER, DELETE, SET MAXCC, LISTC),
  IEBGENER, IEBUPDTE, GIMSMP.
* GIMSMP: SET BDY, UCLIN (ADD/DEL: GLOBALZONE+ZONEINDEX, TARGETZONE, DLIBZONE, OPTIONS, DDDEF), RECEIVE, APPLY [CHECK], RESTORE,
  REJECT, ACCEPT [CHECK], LIST SYSMODS/DDDEF, REPORT ERRSYSMODS/SYSMODS.
* Rules it enforces: SMPEPRC must exist in the JCLLIB (run SMPELOAD), the volume must be online with enough free cylinders
  (the PDF's USER00 is NOT online - edit it, or keep the sidebar auto-replace on), CSIs must exist before SET BDY, DDDEFs must exist
  and point to cataloged data sets, ++VER must match the zone SREL, PRE prerequisites, RESTORE impossible after ACCEPT, REJECT only
  for SYSMODs not applied/accepted, deleting a CSI (J18) wipes that zone.

## Honest limits
* Like the PDF, this was written from IBM documentation and not compared against a live z/OS system. Output layouts are approximate.
* Message IDs GIM20501I, GIM24801W/S and GIM32501E and the IDC / IEF / $HASP lines use real formats. Blue lines starting `*SIM*`
  are teaching messages; for the exact IBM wording look the situation up in "SMP/E Messages, Codes, and Diagnosis" (GA32-0883).
* Only the commands listed above are supported (others return a *SIM* "not supported" message).

## Tests
    python tests/test_sim.py
