#!/usr/bin/env bash
# Manual installer for quota-guard (use this if you do not install it as a plugin).
#
#   ./install.sh                  link the skill into ~/.claude/skills and cc-quota into ~/.local/bin
#   ./install.sh --statusline     also set cc-quota as the Claude Code status line (backs up settings.json)
#   ./install.sh --copy           copy files instead of symlinking them
#   ./install.sh --force          replace an existing skill, cc-quota, or status line
#   ./install.sh --uninstall      remove what this script installed
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
skill_src="$repo/skills/quota-guard"
skill_dst="$HOME/.claude/skills/quota-guard"
bin_dst="$HOME/.local/bin/cc-quota"
settings="$HOME/.claude/settings.json"

copy=0 force=0 statusline=0 uninstall=0
for arg in "$@"; do
  case "$arg" in
    --copy) copy=1 ;;
    --force) force=1 ;;
    --statusline) statusline=1 ;;
    --uninstall) uninstall=1 ;;
    -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

command -v python3 >/dev/null || { echo "python3 is required." >&2; exit 1; }

place() { # place SRC DST
  local src="$1" dst="$2"
  if [ -e "$dst" ] || [ -L "$dst" ]; then
    if [ "$force" -eq 0 ]; then
      echo "skip   $dst (exists; use --force to replace)"
      return
    fi
    rm -rf "$dst"
  fi
  mkdir -p "$(dirname "$dst")"
  if [ "$copy" -eq 1 ]; then cp -R "$src" "$dst"; else ln -s "$src" "$dst"; fi
  echo "ok     $dst"
}

if [ "$uninstall" -eq 1 ]; then
  for path in "$skill_dst" "$bin_dst"; do
    if [ -L "$path" ] && [[ "$(readlink "$path")" == "$repo"/* ]]; then
      rm "$path"; echo "removed $path"
    elif [ -e "$path" ]; then
      echo "kept   $path (not a link into this repo; remove it by hand if it is ours)"
    fi
  done
  echo "The status line and ~/.cache/cc-quota were left untouched."
  exit 0
fi

place "$skill_src" "$skill_dst"
place "$skill_src/scripts/cc-quota" "$bin_dst"

if [ "$statusline" -eq 1 ]; then
  python3 - "$settings" "$bin_dst" "$force" <<'PY'
import json, pathlib, shutil, sys, time
path, binary, force = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3] == "1"
data = json.loads(path.read_text()) if path.exists() else {}
wanted = {"type": "command", "command": f"{binary} --capture"}
current = data.get("statusLine")
if current == wanted:
    print(f"ok     {path} (status line already set)")
    sys.exit(0)
if current and not force:
    print(f"skip   {path} (statusLine already set to {current.get('command')!r}; use --force)")
    sys.exit(0)
if path.exists():
    backup = path.with_name(f"settings.json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(path, backup)
    print(f"backup {backup}")
data["statusLine"] = wanted
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(data, indent=2) + "\n")
print(f"ok     {path} (statusLine -> cc-quota --capture)")
PY
fi

case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) echo "note   ~/.local/bin is not on PATH; add it to use cc-quota directly." ;;
esac
echo "Done. Restart Claude Code, then try: python3 $skill_dst/scripts/quota_status.py"
