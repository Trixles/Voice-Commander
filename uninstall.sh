#!/usr/bin/env bash
#
# uninstall.sh -- Voice Commander uninstaller
#
# Removes everything install.sh put in place:
#   - the systemd user service (stopped, disabled, file deleted)
#   - the launcher script in ~/.local/bin/
#   - the app, venv, icons, KWin script, Vosk model
#
# By default, the user's commands.json is PRESERVED. Pass --purge to also
# delete the config directory.

set -euo pipefail

readonly APP_NAME="voice-commander"

readonly DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/${APP_NAME}"
readonly CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/${APP_NAME}"
readonly BIN_DIR="$HOME/.local/bin"
readonly SYSTEMD_USER_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
readonly KWIN_SCRIPT_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/kwin/scripts/vc-window-placer"

c_reset='\033[0m'
c_green='\033[32m'
c_blue='\033[34m'
c_yellow='\033[33m'

info()  { printf "${c_blue}==>${c_reset} %s\n" "$*"; }
ok()    { printf "${c_green}\u2713${c_reset} %s\n" "$*"; }
warn()  { printf "${c_yellow}!${c_reset}  %s\n" "$*"; }

purge=false
if [[ "${1:-}" == "--purge" ]]; then
    purge=true
fi

info "Stopping and disabling service..."
systemctl --user disable --now "${APP_NAME}.service" 2>/dev/null || true
rm -f "${SYSTEMD_USER_DIR}/${APP_NAME}.service"
systemctl --user daemon-reload
ok "Service removed."

info "Removing launcher..."
rm -f "${BIN_DIR}/${APP_NAME}"
rm -f "${XDG_DATA_HOME:-$HOME/.local/share}/applications/${APP_NAME}.desktop"
ok "Launcher removed."

info "Removing application data..."
rm -rf "${DATA_DIR}"
ok "Application data removed."

info "Removing KWin script..."
rm -rf "${KWIN_SCRIPT_DIR}"
if command -v dbus-send >/dev/null 2>&1; then
    dbus-send --session --print-reply \
        --dest=org.kde.KWin /KWin \
        org.kde.KWin.reconfigure >/dev/null 2>&1 || true
fi
ok "KWin script removed."

if $purge; then
    info "Removing config directory (--purge)..."
    rm -rf "${CONFIG_DIR}"
    ok "Config removed."
else
    if [[ -d "${CONFIG_DIR}" ]]; then
        warn "Config directory preserved: ${CONFIG_DIR}"
        warn "  (use --purge to remove it as well)"
    fi
fi

echo
printf "${c_green}Voice Commander has been uninstalled.${c_reset}\n"
