#!/usr/bin/env bash
# Behavioural test for scripts/link-skills.sh against a throwaway HOME.
# Run: bash scripts/test_link_skills.sh   (exit 0 = all assertions passed)
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd -P)
LINK="$REPO/scripts/link-skills.sh"
WORK=$(mktemp -d); export HOME="$WORK/home"; mkdir -p "$HOME"
SK="$WORK/repo/skills"; mkdir -p "$SK/demo"; printf -- '---\nname: demo\ndescription: Use when testing.\n---\n# demo\n' > "$SK/demo/SKILL.md"
cp "$LINK" "$WORK/repo/link-skills.sh" 2>/dev/null; mkdir -p "$WORK/repo/scripts"; cp "$LINK" "$WORK/repo/scripts/link-skills.sh" 2>/dev/null
L="$WORK/repo/scripts/link-skills.sh"; fails=0
t() { local name=$1 want=$2; shift 2; "$@" >"$WORK/out" 2>&1; local got=$?; if [ "$got" = "$want" ]; then echo "ok   $name"; else echo "FAIL $name (exit $got, want $want)"; sed 's/^/     /' "$WORK/out"; fails=$((fails+1)); fi; }
t "check before linking reports missing (exit 1)"      1 bash "$L" demo --check
t "link creates both symlinks (exit 0)"                0 bash "$L" demo
SKP=$(cd "$SK" && pwd -P)
t "agents link is absolute into repo"                  0 bash -c "[ \"\$(readlink \"$HOME/.agents/skills/demo\")\" = \"$SKP/demo\" ]"
t "claude link is relative to agents link"             0 bash -c "[ \"\$(readlink \"$HOME/.claude/skills/demo\")\" = ../../.agents/skills/demo ]"
t "check after linking is clean (exit 0)"              0 bash "$L" demo --check
t "idempotent re-link (exit 0)"                        0 bash "$L" demo
t "real directory is reported, not replaced (exit 1)"  1 bash -c "rm \"$HOME/.agents/skills/demo\"; mkdir \"$HOME/.agents/skills/demo\"; bash \"$L\" demo"
t "real directory still present afterwards"            0 bash -c "[ -d \"$HOME/.agents/skills/demo\" ] && [ ! -L \"$HOME/.agents/skills/demo\" ]"
t "check flags the real directory (exit 1)"            1 bash "$L" demo --check
t "no-arg links every skill (exit 0)"                  0 bash -c "rmdir \"$HOME/.agents/skills/demo\"; mkdir -p \"$SK/other\"; printf -- '---\nname: other\ndescription: Use when.\n---\n' > \"$SK/other/SKILL.md\"; bash \"$L\""
t "both skills linked"                                 0 bash -c "[ -L \"$HOME/.agents/skills/demo\" ] && [ -L \"$HOME/.agents/skills/other\" ]"
t "prune reports orphan after skill removed (exit 0)"  0 bash -c "rm -r \"$SK/other\"; bash \"$L\" --prune --dry-run | grep -q other"
t "unknown skill name is an error (exit 2)"            2 bash "$L" nope
echo "---"; [ "$fails" = 0 ] && echo "ALL PASSED" || echo "$fails FAILED"; rm -rf "$WORK"; exit "$fails"
