import sys, pathlib, subprocess
HERE = pathlib.Path(__file__).resolve().parent
PY = HERE.parent / ".venv" / "bin" / "python"

failures = []
for t in sorted(HERE.glob("test_*.py")):
    r = subprocess.run([str(PY), str(t)], cwd=HERE.parent)
    if r.returncode != 0:
        failures.append(t.name)

if failures:
    print(f"FAILED: {failures}")
    sys.exit(1)
print("all composition tests passed")
