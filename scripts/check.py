#!/usr/bin/env python3
"""Validate every skill under skills/: frontmatter, size budget, and tests.

Usage: python3 scripts/check.py   (exit 1 on any problem)
"""
import os, pathlib, re, subprocess, sys

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
WORD_BUDGET = 1300
QUICK_VALIDATE = pathlib.Path.home() / ".codex/skills/.system/skill-creator/scripts/quick_validate.py"


def parse_frontmatter(text):
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end < 0:
        return None
    fm = {}
    for line in text[4:end].splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm


def validate_skill(fm, body_words):
    problems = []
    name = fm.get("name", "")
    if not NAME_RE.match(name):
        problems.append(f"name {name!r} must be lowercase letters, digits, hyphens")
    desc = fm.get("description", "")
    if not desc:
        problems.append("description missing")
    else:
        if len(desc) >= 500:
            problems.append(f"description is {len(desc)} chars (limit 500)")
        if not desc.startswith("Use when"):
            problems.append("description should start with 'Use when'")
    if body_words > WORD_BUDGET:
        problems.append(f"SKILL.md is {body_words} words (budget {WORD_BUDGET})")
    return problems


def find_skills(root):
    skills = root / "skills"
    if not skills.is_dir():
        return []
    return sorted(p for p in skills.iterdir() if (p / "SKILL.md").is_file())


def main():
    root = pathlib.Path(__file__).resolve().parent.parent
    skills = find_skills(root)
    if not skills:
        print("no skills under skills/")
        return 0
    failed = False
    for sk in skills:
        text = (sk / "SKILL.md").read_text(encoding="utf-8")
        fm = parse_frontmatter(text)
        words = len(text.split())
        probs = ["no YAML frontmatter"] if fm is None else validate_skill(fm, words)
        if QUICK_VALIDATE.is_file():
            r = subprocess.run([sys.executable, "-I", str(QUICK_VALIDATE), str(sk)], capture_output=True, text=True)
            if r.returncode != 0:
                probs.append("quick_validate: " + (r.stdout + r.stderr).strip()[-300:])
        tests = sorted((sk / "scripts").glob("test_*.py")) if (sk / "scripts").is_dir() else []
        if tests:
            r = subprocess.run([sys.executable, "-I", "-m", "unittest", "discover", "-s", str(sk / "scripts"), "-p", "test_*.py"], capture_output=True, text=True)
            tail = (r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout).strip() else ""
            print(f"{sk.name}: tests -> {tail}")
            if r.returncode != 0:
                probs.append("unit tests failed")
        status = "FAIL" if probs else "ok"
        print(f"{sk.name}: {status} ({words} words)")
        for p in probs:
            print(f"  - {p}")
        failed |= bool(probs)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
