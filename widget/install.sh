#!/usr/bin/env bash
#
# Install the poke.run bar widget into the Omarchy shell.
#
# The Omarchy shell loads third-party plugins from ~/.config/omarchy/plugins/<id>/
# and nowhere else; $OMARCHY_PATH (/usr/share/omarchy) is package-owned and this
# script never writes to it. Everything here is the documented supported path --
# copy the folder, rescan, enable, set the widget's options -- so re-running it
# converges on the same state instead of stacking duplicates.
#
# It also retires this widget's previous id, `sapphire.run`, if it finds it: see
# the migration block near the bottom.
#
# Usage:
#   widget/install.sh                 # install, enable, pin this checkout's live dir
#   widget/install.sh --no-enable     # install the files only, print the rest
#   widget/install.sh --section left  # place the widget in a specific bar section

set -euo pipefail

PLUGIN_ID="poke.run"

# The id this widget shipped under until it learned to read any Gen 1-3 game.
LEGACY_ID="sapphire.run"

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_DIR="$REPO_DIR/widget/$PLUGIN_ID"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy"
DEST_ROOT="$CONFIG_DIR/plugins"
DEST_DIR="$DEST_ROOT/$PLUGIN_ID"
LEGACY_DIR="$DEST_ROOT/$LEGACY_ID"
SHELL_JSON="$CONFIG_DIR/shell.json"

# paths.py resolves the live directory the same way: SAPPHIRE_LIVE_DIR wins,
# otherwise <repo>/live. Ask it rather than guessing, so a checkout with a
# custom layout still gets pinned correctly.
FEED_DIR="${SAPPHIRE_LIVE_DIR:-$REPO_DIR/live}"
FEED_NAME="${SAPPHIRE_FEED:-default}"

SECTION=""
DO_ENABLE=1
while (($# > 0)); do
  case "$1" in
    --no-enable) DO_ENABLE=0; shift ;;
    --section) SECTION="${2:-}"; [[ -n $SECTION ]] || { echo "--section needs left|center|right" >&2; exit 1; }; shift 2 ;;
    --feed) FEED_NAME="${2:-}"; [[ -n $FEED_NAME ]] || { echo "--feed needs a name" >&2; exit 1; }; shift 2 ;;
    -h|--help) sed -n '14,17p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "install.sh: unknown option $1" >&2; exit 1 ;;
  esac
done

[[ -d $SRC_DIR ]] || { echo "install.sh: $SRC_DIR is missing" >&2; exit 1; }

# True when shell.json's bar layout still names the given widget id. Asked of
# the config rather than of `omarchy plugin list`, because a layout entry can
# outlive the folder it points at and that is exactly the case worth catching.
in_bar_layout() {
  [[ -f $SHELL_JSON ]] || return 1
  jq -e --arg id "$1" '
    (.bar.layout // {}) | to_entries | map(.value // []) | flatten
    | any(.[]; (.id // "") == $id)
  ' "$SHELL_JSON" >/dev/null 2>&1
}

# --------------------------------------------------------------- copy the files

# The shell hot-reloads plugin code on every write under ~/.config/omarchy/plugins/,
# so copying file-by-file straight into the destination makes it load a torn
# plugin -- Panel.qml present, Feed.qml not yet -- once per file. That is not
# theoretical: doing it that way took the running shell down (it reloaded
# mid-copy and its lock service aborted). So stage the whole plugin OUTSIDE the
# watched directory and swap it in with a single rename. The staging directory
# is a sibling of plugins/ rather than /tmp, because a rename is only atomic
# within one filesystem.
STAGE_ROOT="$(mktemp -d "$CONFIG_DIR/.$PLUGIN_ID.install.XXXXXX")"
trap 'rm -rf "$STAGE_ROOT"' EXIT
STAGE="$STAGE_ROOT/$PLUGIN_ID"
mkdir -p "$STAGE" "$DEST_ROOT"

# An explicit extension list, not a recursive copy: a stray editor swap file or
# __pycache__ has no business inside a directory the shell loads code from. The
# sprite assets travel with the QML so the installed plugin is self-contained --
# it keeps its mark when this checkout moves or is deleted.
shopt -s nullglob
for f in "$SRC_DIR"/*.qml "$SRC_DIR"/*.js "$SRC_DIR"/manifest.json \
         "$SRC_DIR"/*.png "$SRC_DIR"/*.gif; do
  install -m 644 "$f" "$STAGE/$(basename "$f")"
done
shopt -u nullglob

# The party sprites are a whole directory, so they get their own pass with the
# same extension discipline: 384 PNGs derived by make_sprites.py, each named
# after the decomp directory it came from.
if compgen -G "$SRC_DIR/sprites/*.png" > /dev/null; then
  mkdir -p "$STAGE/sprites"
  shopt -s nullglob
  for f in "$SRC_DIR"/sprites/*.png; do
    install -m 644 "$f" "$STAGE/sprites/$(basename "$f")"
  done
  shopt -u nullglob
else
  echo "install.sh: no party sprites in $SRC_DIR/sprites." >&2
  echo "install.sh: regenerate with: .venv/bin/python widget/make_sprites.py" >&2
fi

# The mark is generated art. Panel.qml degrades to a glyph without it, but
# quietly installing a logo-less widget is not a state anyone chose.
if [[ ! -f $STAGE/torchic.gif ]]; then
  echo "install.sh: the Torchic mark is missing from $SRC_DIR." >&2
  echo "install.sh: regenerate it with: .venv/bin/python widget/make_logo.py" >&2
fi

# Validate the staged copy before it goes anywhere near the shell: a manifest
# the registry would reject should never reach the plugins directory at all.
if command -v omarchy-plugin-validate >/dev/null 2>&1; then
  omarchy-plugin-validate "$STAGE"
  echo "Manifest validated against the shell's plugin schema."
fi

# Move any previous install aside first, so the watcher sees one complete
# removal followed by one complete addition rather than a half-populated dir.
# This is also what makes re-running idempotent AND what drops files an older
# version shipped and this one no longer does.
if [[ -d $DEST_DIR ]]; then
  mv "$DEST_DIR" "$STAGE_ROOT/previous"
fi
mv "$STAGE" "$DEST_DIR"

echo "Installed $PLUGIN_ID -> $DEST_DIR"

mkdir -p "$FEED_DIR"

# ------------------------------------------------------- register with the shell

# enablePlugin and setBarWidget are shell IPC calls, so they need the shell to
# be running. Installing from a TTY or over SSH is legitimate; say what is left
# to do instead of failing.
if ! omarchy-shell shell ping >/dev/null 2>&1; then
  cat <<EOF

The Omarchy shell is not reachable, so the files were copied but nothing was
enabled or migrated. From inside a graphical session, run:

  omarchy-shell shell rescanPlugins
  omarchy plugin enable $PLUGIN_ID${SECTION:+ $SECTION}
  omarchy bar set $PLUGIN_ID feedDir $FEED_DIR
  omarchy bar set $PLUGIN_ID feed $FEED_NAME
  omarchy plugin remove $LEGACY_ID --yes    # only if $LEGACY_ID is still installed
EOF
  exit 0
fi

omarchy-shell shell rescanPlugins >/dev/null
echo "Shell rescanned its plugin directories."

if ((DO_ENABLE == 0)); then
  cat <<EOF

Files installed and the shell has seen them. To put the widget on the bar:

  omarchy plugin enable $PLUGIN_ID${SECTION:+ $SECTION}
  omarchy bar set $PLUGIN_ID feedDir $FEED_DIR
  omarchy bar set $PLUGIN_ID feed $FEED_NAME
EOF
  exit 0
fi

# Enabling an already-enabled widget would add a second copy to the bar, and
# allowMultiple is deliberately on (one widget per feed name). Only enable when
# this id is not already in the layout.
if omarchy plugin list --json 2>/dev/null |
  jq -e --arg id "$PLUGIN_ID" 'any(.[]; .id == $id and .enabled == true)' >/dev/null 2>&1; then
  echo "Already on the bar; leaving its placement alone."
else
  PLACEMENT=()
  if [[ -n $SECTION ]]; then
    PLACEMENT=(--section "$SECTION")
  elif in_bar_layout "$LEGACY_ID"; then
    # Land where the widget being retired was sitting. Without this, the rename
    # would silently move a widget the user had positioned to the end of its
    # section, which looks like the install broke the bar.
    PLACEMENT=(--after "$LEGACY_ID")
  fi
  omarchy plugin enable "$PLUGIN_ID" ${PLACEMENT[@]+"${PLACEMENT[@]}"}
fi

# Pin the feed directory onto the widget's shell.json entry. The QML falls back
# to $SAPPHIRE_LIVE_DIR, but the shell's environment is the login session's, not
# the one the driver runs in, so the explicit value is what makes it work.
omarchy bar set "$PLUGIN_ID" feedDir "$FEED_DIR"
omarchy bar set "$PLUGIN_ID" feed "$FEED_NAME"

# ------------------------------------------------------ retire the old widget id

# This widget shipped as `sapphire.run` while it could only read a Sapphire run.
# It now shows whichever Gen 1-3 game the publisher names, so that id no longer
# tells the truth and has been cut over to `poke.run`.
#
# Retiring the old one is not optional housekeeping: left alone it stays on the
# bar reading the same feed, so the user gets two identical widgets -- and once
# its folder is gone, a layout entry pointing at a plugin the shell cannot find.
# This runs AFTER the new widget is placed, so there is never a moment with
# neither of them on the bar.
#
# Both branches are conditional, which is what makes re-running this script
# after the migration a no-op.
if [[ -d $LEGACY_DIR ]]; then
  # `plugin remove` disables first -- and disabling a bar widget is exactly what
  # splices its entry out of shell.json -- then moves the folder to a
  # dot-prefixed backup that the shell's plugin scan skips. Both steps are the
  # supported path; nothing here hand-edits shell.json.
  echo
  omarchy plugin remove "$LEGACY_ID" --yes
  echo "Retired $LEGACY_ID; this widget is now $PLUGIN_ID."
elif in_bar_layout "$LEGACY_ID"; then
  # Folder already gone but the layout still names it. Disabling by id still
  # splices the entry out, even for a plugin the shell can no longer find.
  echo
  omarchy plugin disable "$LEGACY_ID" >/dev/null
  echo "Dropped the stale $LEGACY_ID entry from the bar layout."
fi

cat <<EOF

Done. The widget is on the bar, reading:

  $FEED_DIR/$FEED_NAME.json    state    (polled ~0.5 Hz closed, 2.5 Hz open)
  $FEED_DIR/$FEED_NAME.png     screen   (popup only)
  $FEED_DIR/$FEED_NAME.jsonl   narration (popup only)

Publish to it from the driver:

  from pokeagent.live import LiveFeed
  feed = LiveFeed("$FEED_NAME").attach(driver)

Left click opens the popup, middle click forces a re-read, right click sends the
status line as a notification. To remove it again:

  omarchy plugin disable $PLUGIN_ID
  rm -rf $DEST_DIR
EOF
