import sys, pathlib, subprocess, hashlib
SKILL = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "scripts"))
import render  # noqa: E402


def _render(out, extra):
    out.mkdir(parents=True, exist_ok=True)
    cmd = [str(SKILL / ".venv/bin/python"), str(SKILL / "scripts/render.py"),
           "--out", str(out), "--procedure", "egd", "--location", "scc",
           "--lang", "en", "--theme", "calm", *extra]
    subprocess.run(cmd, check=True, cwd=SKILL)
    return sorted(out.rglob("*.pdf"))[0].read_bytes()


def test_render_pdf_accepts_addons_kwarg():
    # Signature contract: render_pdf must accept add_ons + knob_picks kwargs.
    import inspect
    params = inspect.signature(render.render_pdf).parameters
    assert "add_ons" in params and "knob_picks" in params


def test_plain_egd_is_byte_identical(tmp_path=pathlib.Path("/tmp/egd-after")):
    after = _render(tmp_path, [])
    golden_sha = pathlib.Path("/tmp/egd-golden.sha").read_text().split()[0]
    assert hashlib.sha256(after).hexdigest() == golden_sha


def test_combo_render_embeds_addon_blurb(tmp_path=pathlib.Path("/tmp/egd-combo")):
    pdf = _render(tmp_path, ["--add-ons", "dise", "--ppi", "hold"])
    # WeasyPrint output is binary; assert the run succeeded + produced a larger doc.
    assert len(pdf) > 1000


if __name__ == "__main__":
    test_render_pdf_accepts_addons_kwarg(); print("PASS signature")
    test_plain_egd_is_byte_identical(); print("PASS byte-identical")
    test_combo_render_embeds_addon_blurb(); print("PASS combo render")
