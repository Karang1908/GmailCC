#!/usr/bin/env bash
# clientmail installer -- macOS / Linux
#
#   curl -fsSL https://raw.githubusercontent.com/OWNER/REPO/main/install.sh | bash
# or, from a clone:
#   ./install.sh
#
# Safe to re-run: it never overwrites your config.json, your drafts, or templates
# you have edited.

set -euo pipefail

REPO="${CLIENTMAIL_REPO:-Karang1908/GmailCC}"
BRANCH="${CLIENTMAIL_BRANCH:-main}"
HOME_DIR="${CLIENTMAIL_HOME:-$HOME/.clientmail}"
APP_DIR="$HOME_DIR/app"
BIN_DIR="${CLIENTMAIL_BIN:-$HOME/.local/bin}"
SKILLS_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills"

bold=$'\033[1m'; red=$'\033[31m'; green=$'\033[32m'; yellow=$'\033[33m'; dim=$'\033[2m'; off=$'\033[0m'
step() { printf '%s==>%s %s\n' "$bold" "$off" "$1"; }
ok()   { printf '    %s✓%s %s\n' "$green" "$off" "$1"; }
warn() { printf '    %s!%s %s\n' "$yellow" "$off" "$1"; }
die()  { printf '%serror%s %s\n' "$red" "$off" "$1" >&2; exit 1; }

# --- 1. prerequisites -----------------------------------------------------
step "Checking prerequisites"

PYTHON=""
for candidate in python3 python3.13 python3.12 python3.11 python; do
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)' 2>/dev/null; then
    PYTHON="$(command -v "$candidate")"; break
  fi
done
[ -n "$PYTHON" ] || die "Python 3.9+ not found. Install it (macOS: brew install python) and re-run."
ok "python $("$PYTHON" -c 'import platform;print(platform.python_version())') at $PYTHON"

command -v git >/dev/null 2>&1 || warn "git not found -- /client-work cannot read a repo baseline without it."
command -v git >/dev/null 2>&1 && ok "git $(git --version | awk '{print $3}')"

HAVE_CLAUDE=1
command -v claude >/dev/null 2>&1 || { HAVE_CLAUDE=0; warn "claude CLI not found -- I'll print the MCP command for you to run later."; }
[ "$HAVE_CLAUDE" = 1 ] && ok "claude CLI found"

# --- 2. get the source ----------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -d "$SCRIPT_DIR/server/clientmail" ]; then
  step "Installing from local checkout"
  SRC="$SCRIPT_DIR"
  ok "$SRC"
else
  step "Downloading clientmail ($REPO@$BRANCH)"
  command -v curl >/dev/null 2>&1 || die "curl is required to download."
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  URL="https://codeload.github.com/$REPO/tar.gz/refs/heads/$BRANCH"
  curl -fsSL "$URL" -o "$TMP/src.tar.gz" \
    || die "Download failed from $URL — check the repo name, or set CLIENTMAIL_REPO=owner/name."
  tar -xzf "$TMP/src.tar.gz" -C "$TMP"
  SRC="$(find "$TMP" -maxdepth 1 -type d -name '*-*' | head -1)"
  [ -d "$SRC/server/clientmail" ] || die "Downloaded archive did not contain server/clientmail."
  ok "downloaded"
fi

# --- 3. lay out files -----------------------------------------------------
step "Installing to $HOME_DIR"
mkdir -p "$APP_DIR" "$HOME_DIR/templates" "$HOME_DIR/drafts" "$HOME_DIR/sessions" "$BIN_DIR"

rm -rf "$APP_DIR/server"
cp -R "$SRC/server" "$APP_DIR/server"
cp -R "$SRC/n8n" "$APP_DIR/n8n" 2>/dev/null || true
cp -R "$SRC/tools" "$APP_DIR/tools" 2>/dev/null || true
cp "$SRC/config.example.json" "$APP_DIR/config.example.json"
cp -R "$SRC/templates" "$APP_DIR/templates_stock"
ok "code -> $APP_DIR"

# Templates you have edited are never clobbered.
NEW_T=0; KEPT_T=0
for t in "$SRC/templates"/*; do
  name="$(basename "$t")"
  if [ -e "$HOME_DIR/templates/$name" ]; then KEPT_T=$((KEPT_T+1)); else cp "$t" "$HOME_DIR/templates/$name"; NEW_T=$((NEW_T+1)); fi
done
ok "templates: $NEW_T added, $KEPT_T left as you had them"
[ "$KEPT_T" -gt 0 ] && printf '      %sstock copies are in %s if you want to diff them%s\n' "$dim" "$APP_DIR/templates_stock" "$off"

if [ -e "$HOME_DIR/config.json" ]; then
  ok "config.json already present -- untouched"
  FRESH_CONFIG=0
else
  cp "$SRC/config.example.json" "$HOME_DIR/config.json"
  SECRET="$("$PYTHON" -c 'import secrets;print(secrets.token_urlsafe(32))')"
  "$PYTHON" - "$HOME_DIR/config.json" "$SECRET" <<'PY'
import json, sys
path, secret = sys.argv[1], sys.argv[2]
with open(path) as fh: cfg = json.load(fh)
cfg["webhook_secret"] = secret
with open(path, "w") as fh: json.dump(cfg, fh, indent=2)
PY
  ok "config.json created with a generated webhook_secret"
  FRESH_CONFIG=1
fi

# --- 4. cli shim ----------------------------------------------------------
cat > "$BIN_DIR/clientmail" <<EOF
#!/usr/bin/env bash
exec "$PYTHON" "$APP_DIR/server/clientmail_cli.py" "\$@"
EOF
chmod +x "$BIN_DIR/clientmail"
ok "clientmail command -> $BIN_DIR/clientmail"

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) warn "$BIN_DIR is not on your PATH. Add this to ~/.zshrc:"
     printf '        export PATH="%s:$PATH"\n' "$BIN_DIR" ;;
esac

# --- 5. claude code skills ------------------------------------------------
step "Installing Claude Code skills"
mkdir -p "$SKILLS_DIR"
for skill in client-work client-update; do
  mkdir -p "$SKILLS_DIR/$skill"
  cp "$SRC/skills/$skill/SKILL.md" "$SKILLS_DIR/$skill/SKILL.md"
  ok "/$skill"
done

# --- 6. register the mcp server -------------------------------------------
step "Registering the MCP server"
MCP_CMD=(claude mcp add clientmail -s user -- "$PYTHON" "$APP_DIR/server/clientmail_server.py")
if [ "$HAVE_CLAUDE" = 1 ]; then
  claude mcp remove clientmail -s user >/dev/null 2>&1 || true
  if "${MCP_CMD[@]}" >/dev/null 2>&1; then
    ok "registered as 'clientmail' (user scope)"
  else
    warn "automatic registration failed. Run this yourself:"
    printf '        %s\n' "${MCP_CMD[*]}"
  fi
else
  warn "run this once the claude CLI is installed:"
  printf '        %s\n' "${MCP_CMD[*]}"
fi

# --- 7. what's left -------------------------------------------------------
printf '\n%sInstalled.%s\n\n' "$bold" "$off"
printf 'One-time setup left -- about 5 minutes:\n\n'
printf '  1. Set up n8n (import the workflow, connect Gmail):\n'
printf '     %s%s/n8n/SETUP.md%s\n\n' "$dim" "$APP_DIR" "$off"
printf '  2. Put your n8n webhook URL + secret in:\n'
printf '     %s%s/config.json%s\n' "$dim" "$HOME_DIR" "$off"
if [ "${FRESH_CONFIG:-0}" = 1 ]; then
  printf '     %s(a webhook_secret was generated for you -- copy it into n8n)%s\n' "$dim" "$off"
fi
printf '\n  3. Check it:            %sclientmail check --ping%s\n' "$bold" "$off"
printf '  4. Mail yourself first: %sclientmail test-email you@example.com%s\n\n' "$bold" "$off"
printf '%sNote:%s allowed_recipients starts locked to one address, so the first send to a\n' "$yellow" "$off"
printf 'real client will be refused until you add them. That is deliberate.\n\n'
printf 'Then in any repo:  %s/client-work%s to start, %s/client-update%s when the work is done.\n\n' "$bold" "$off" "$bold" "$off"
