#!/usr/bin/env bash
# Link skills from this repository into the user-level skill directories.
#
#   bash scripts/link-skills.sh [NAME...]        link the named skills (default: all under skills/)
#   bash scripts/link-skills.sh [NAME...] --check  report drift, change nothing (exit 1 on drift)
#   bash scripts/link-skills.sh --prune [--dry-run] move orphan links (target gone) to the Trash
#
# Creates  ~/.agents/skills/NAME -> <repo>/skills/NAME   (absolute; read by Codex and Claude Code)
#      and ~/.claude/skills/NAME -> ../../.agents/skills/NAME (relative)
# A real directory or file at either path is reported and never replaced.
# Exit: 0 ok · 1 drift or refused · 2 usage
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd -P)
SKILLS="$REPO/skills"
AG="$HOME/.agents/skills"; CL="$HOME/.claude/skills"
mode=link; dry=0; names=()
for a in "$@"; do
  case "$a" in
    --check) mode=check ;; --prune) mode=prune ;; --dry-run) dry=1 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    -*) echo "unknown option: $a" >&2; exit 2 ;;
    *) names+=("$a") ;;
  esac
done
all_skills() { for d in "$SKILLS"/*/; do [ -f "$d/SKILL.md" ] && basename "$d"; done; }
if [ ${#names[@]} -eq 0 ] && [ "$mode" != prune ]; then while IFS= read -r n; do names+=("$n"); done < <(all_skills); fi
for n in "${names[@]:-}"; do [ -z "$n" ] && continue; [ -f "$SKILLS/$n/SKILL.md" ] || { echo "no such skill: $n (expected $SKILLS/$n/SKILL.md)" >&2; exit 2; }; done
rc=0
# ensure_link PATH TARGET LABEL
ensure_link() {
  local p=$1 t=$2 label=$3
  if [ -L "$p" ]; then
    local cur; cur=$(readlink "$p")
    if [ "$cur" = "$t" ]; then echo "ok      $label"; return 0; fi
    if [ "$mode" = check ]; then echo "DRIFT   $label -> $cur (want $t)"; return 1; fi
    ln -sfn "$t" "$p" && echo "relink  $label -> $t"; return 0
  elif [ -e "$p" ]; then
    local kind=file; [ -d "$p" ] && kind=directory
    echo "REFUSE  $label is a real $kind, not touching"; return 1
  else
    if [ "$mode" = check ]; then echo "MISSING $label"; return 1; fi
    mkdir -p "$(dirname "$p")" && ln -s "$t" "$p" && echo "link    $label -> $t"; return 0
  fi
}
trash() { if [ "$dry" = 1 ]; then echo "would trash $1"; else osascript -e "tell application \"Finder\" to delete POSIX file \"$1\"" >/dev/null && echo "trashed $1"; fi; }
if [ "$mode" = prune ]; then
  for l in "$AG"/*; do
    [ -L "$l" ] || continue; t=$(readlink "$l"); n=$(basename "$l")
    case "$t" in "$SKILLS"/*) ;; *) continue ;; esac
    [ -f "$t/SKILL.md" ] && continue
    echo "orphan: $l -> $t"; trash "$l"; [ -L "$CL/$n" ] && { echo "orphan: $CL/$n"; trash "$CL/$n"; }
  done
  exit 0
fi
for n in "${names[@]}"; do
  ensure_link "$AG/$n" "$SKILLS/$n" "~/.agents/skills/$n" || rc=1
  ensure_link "$CL/$n" "../../.agents/skills/$n" "~/.claude/skills/$n" || rc=1
done
exit $rc
