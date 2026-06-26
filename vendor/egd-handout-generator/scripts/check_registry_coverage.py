"""Gate: every registered combo composes cleanly and no field is missing.
Run standalone (exit 1 on problems) or import `audit()` from tests / make verify.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import compose as _c

LANGS = ("en", "es")


def audit(registry=None):
    reg = registry or _c.load_registry()
    problems = []

    for aid, entry in reg["add_ons"].items():
        for lang in LANGS:
            if not entry.get(f"title_fragment_{lang}"):
                problems.append(f"add-on {aid}: missing title_fragment_{lang}")
        if aid.endswith("_generic"):
            spec = reg["specialties"].get(entry.get("specialty", ""), {})
            for lang in LANGS:
                if not spec.get(f"generic_blurb_{lang}"):
                    problems.append(f"add-on {aid}: specialty missing generic_blurb_{lang}")
        else:
            for lang in LANGS:
                if not entry.get(f"blurb_{lang}"):
                    problems.append(f"add-on {aid}: missing blurb_{lang}")

    for name, kdef in reg["knobs"].items():
        owner = kdef.get("owned_by_addon")
        if owner is not None and owner not in reg["add_ons"]:
            problems.append(f"knob {name}: owned_by_addon {owner!r} not in add_ons")
        for opt, odef in kdef["options"].items():
            for lang in LANGS:
                if f"fragment_{lang}" not in odef:
                    problems.append(f"knob {name}.{opt}: missing fragment_{lang} key")

    for aid in reg["add_ons"]:
        for lang in LANGS:
            try:
                c = _c.compose("egd", [aid], {}, lang, reg)
            except Exception as e:  # noqa: BLE001
                problems.append(f"compose egd+{aid} ({lang}) raised: {e!r}")
                continue
            if "{{" in c.title or "{{" in c.blurbs_html:
                problems.append(f"compose egd+{aid} ({lang}) left an unresolved token")
    return problems


def main():
    problems = audit()
    if problems:
        print("registry-coverage FAILED:")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)
    print("registry-coverage OK")


if __name__ == "__main__":
    main()
