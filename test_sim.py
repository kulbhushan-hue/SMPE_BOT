"""Run:  python tests/test_sim.py   (from the project folder)"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT); sys.path.insert(0, ROOT)
import content as C, smpe_sim as S

ORIG = C.original_members()
EXPECT_RC = {m[0]: m[4] for m in C.STEPS}


def fresh():
    s = S.new_system()
    S.submit(s, C.smpeload_text())
    return s


def test_load_has_19_members():
    assert len(fresh()["catalog"]["SMPE.PRAC.JCL"]["members"]) == 19


def test_walkthrough_rcs_match_pdf():
    s = fresh()
    for m in C.STEP_IDS[1:]:
        j = S.submit(s, C.fix_vol(ORIG[m], "SMPV01"))
        assert (j["maxcc"] == 0) if EXPECT_RC[m] == 0 else (j["maxcc"] >= 4), (m, j["status"])


def test_dummy_members_installed_and_restored():
    s = fresh()
    for m in "J01ALLOC J02CSI J03ZONES J04USRMD J05RECV1 J06RECV2 J10APPLY1 J11APPLY2".split():
        S.submit(s, C.fix_vol(ORIG[m], "SMPV01"))
    assert sorted(s["catalog"]["SMPE.PRAC.SDUMMYL"]["members"]) == ["DUMMYJ1", "DUMMYJ2"]
    S.submit(s, C.fix_vol(ORIG["J12RESTR"], "SMPV01"))
    assert sorted(s["catalog"]["SMPE.PRAC.SDUMMYL"]["members"]) == ["DUMMYJ1"]


def test_unknown_volume_and_missing_proc():
    s = fresh()
    assert "NOT ONLINE" in S.submit(s, ORIG["J01ALLOC"])["spool"][2][2]      # USER00 does not exist
    s2 = S.new_system()
    assert S.submit(s2, ORIG["J05RECV1"])["status"] == "JCL ERROR"          # SMPELOAD not run


def test_challenges_show_expected_message():
    for ch in C.CHALLENGES:
        _, res = C.run_challenge(ch, ORIG)
        txt = "\n".join(t for _, j in res for _, d, t in j["spool"] if d in ("SMPOUT", "SYSPRINT", "JESYSMSG"))
        assert ch["expect"] in txt, ch["n"]


def test_app_runs():
    try:
        from streamlit.testing.v1 import AppTest
    except Exception:
        return
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=60).run()
    assert not at.exception, at.exception
    at.button(key="sub_SMPELOAD").click().run()
    assert not at.exception, at.exception


if __name__ == "__main__":
    bad = 0
    for n, f in sorted(globals().items()):
        if n.startswith("test_"):
            try:
                f(); print("PASS", n)
            except Exception as e:
                bad += 1; print("FAIL", n, repr(e)[:300])
    sys.exit(bad)
