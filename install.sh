#!/usr/bin/env bash
#
# install.sh -- Voice Commander (Whisper fork) installer
#
# THIS IS THE WHISPER FORK. It installs under voice-commander-whisper
# everywhere, fully COEXISTING with a parent Vosk install -- separate
# data/config dirs, service names, launcher, lock socket, and KWin
# placer. It never touches the parent's files (it only READS them to
# seed config / copy the vosk model on first install).
#
#   ~/.local/share/voice-commander-whisper/app/      -- application code
#   ~/.local/share/voice-commander-whisper/venv/     -- Python virtual environment
#   ~/.local/share/voice-commander-whisper/icons/    -- tray + service icons
#   ~/.local/share/voice-commander-whisper/vosk-model/ -- Vosk model (vosk backend)
#   ~/.local/share/voice-commander-whisper/models/   -- whisper ggml + silero VAD
#   ~/.local/share/kwin/scripts/vcw-window-placer/   -- KWin helper script
#   ~/.config/voice-commander-whisper/commands.json  -- user-editable config
#   ~/.config/systemd/user/voice-commander-whisper.service        -- app unit
#   ~/.config/systemd/user/voice-commander-whisper-server.service -- whisper-server unit
#   ~/.local/bin/voice-commander-whisper             -- launcher command
#
# Re-running this script is safe: it preserves the existing commands.json
# and uninstalls/reinstalls everything else.

set -euo pipefail

# -- Constants ---------------------------------------------------------------
readonly APP_NAME="voice-commander-whisper"
# Parent (Vosk daily-driver) install, read-only: used to seed config and
# copy the vosk model instead of re-downloading. Never written to.
readonly PARENT_APP_NAME="voice-commander"
readonly REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

readonly DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/${APP_NAME}"
readonly CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/${APP_NAME}"
readonly BIN_DIR="$HOME/.local/bin"
readonly SYSTEMD_USER_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
# Placer Id must match core/paths.py PLACER_ID -- it namespaces the kwinrc
# queue group, keeping the fork's placement queue separate from the parent's.
readonly KWIN_SCRIPT_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/kwin/scripts/vcw-window-placer"

readonly APP_DIR="${DATA_DIR}/app"
readonly VENV_DIR="${DATA_DIR}/venv"
readonly ICONS_DIR="${DATA_DIR}/icons"
readonly VOSK_DIR="${DATA_DIR}/vosk-model"
readonly MODELS_DIR="${DATA_DIR}/models"

readonly PARENT_DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/${PARENT_APP_NAME}"
readonly PARENT_CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/${PARENT_APP_NAME}/commands.json"

readonly VOSK_MODEL_NAME="vosk-model-small-en-us-0.15"
readonly VOSK_MODEL_URL="https://alphacephei.com/vosk/models/${VOSK_MODEL_NAME}.zip"

# Whisper backend assets. Model name must match the whisper_model config
# default (core/commands.py --emit-defaults).
readonly WHISPER_MODEL_NAME="base.en"
readonly WHISPER_MODEL_URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-${WHISPER_MODEL_NAME}.bin"
readonly SILERO_VAD_URL="https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/silero_vad.onnx"

# Extract __version__ from core/__init__.py so we can echo it on success.
# Pure-bash read keeps install.sh self-sufficient (no need to install the
# venv first just to query a version string).
VC_VERSION=$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "${REPO_DIR}/core/__init__.py")
readonly VC_VERSION

# -- Output helpers ----------------------------------------------------------
c_reset='\033[0m'
c_bold='\033[1m'
c_red='\033[31m'
c_green='\033[32m'
c_yellow='\033[33m'
c_blue='\033[34m'

info()  { printf "${c_blue}==>${c_reset} %s\n" "$*"; }
ok()    { printf "${c_green}\u2713${c_reset} %s\n" "$*"; }
warn()  { printf "${c_yellow}!${c_reset}  %s\n" "$*"; }
err()   { printf "${c_red}\u2717${c_reset} %s\n" "$*" >&2; }
die()   { err "$*"; exit 1; }

# -- Distro detection --------------------------------------------------------
detect_distro() {
    if [[ ! -r /etc/os-release ]]; then
        echo "unknown"
        return
    fi
    # shellcheck disable=SC1091
    . /etc/os-release
    case "${ID:-}:${ID_LIKE:-}" in
        *arch*|*:*arch*)        echo "arch" ;;
        *debian*|*ubuntu*|*:*debian*|*:*ubuntu*) echo "debian" ;;
        *)                      echo "unknown" ;;
    esac
}

# -- Required binaries -------------------------------------------------------
# Format: binary_name=arch_pkg|debian_pkg
# A '-' on either side means "not packaged separately on this distro".
declare -A REQUIRED_BINS=(
    [python3]="python|python3"
    [pactl]="libpulse|pulseaudio-utils"
    [pw-record]="pipewire|pipewire-bin"
    [playerctl]="playerctl|playerctl"
    [kscreen-doctor]="libkscreen|libkscreen-bin"
    [notify-send]="libnotify|libnotify-bin"
    [kwriteconfig6]="kconfig|libkf6config-bin"
    [dbus-send]="dbus|dbus-bin"
    [xdg-open]="xdg-utils|xdg-utils"
    [curl]="curl|curl"
    [unzip]="unzip|unzip"
    [systemctl]="systemd|systemd"
)

check_prereqs() {
    info "Checking prerequisites..."
    local distro
    distro=$(detect_distro)

    local missing_bins=()
    local missing_pkgs=()

    for bin in "${!REQUIRED_BINS[@]}"; do
        if ! command -v "$bin" >/dev/null 2>&1; then
            missing_bins+=("$bin")
            local pkgs="${REQUIRED_BINS[$bin]}"
            local pkg
            case "$distro" in
                arch)    pkg="${pkgs%%|*}" ;;
                debian)  pkg="${pkgs##*|}" ;;
                *)       pkg="?" ;;
            esac
            [[ "$pkg" != "-" && "$pkg" != "?" ]] && missing_pkgs+=("$pkg")
        fi
    done

    if [[ ${#missing_bins[@]} -gt 0 ]]; then
        err "Missing required dependencies: ${missing_bins[*]}"
        echo
        case "$distro" in
            arch)
                printf "To install on Arch/CachyOS/Manjaro, run:\n"
                printf "    ${c_bold}sudo pacman -S %s${c_reset}\n" "${missing_pkgs[*]}"
                ;;
            debian)
                printf "To install on Debian/Ubuntu/Kubuntu, run:\n"
                printf "    ${c_bold}sudo apt install %s${c_reset}\n" "${missing_pkgs[*]}"
                ;;
            *)
                printf "Your distro was not auto-detected. Install these binaries via\n"
                printf "your package manager, then re-run this script:\n"
                printf "    ${c_bold}%s${c_reset}\n" "${missing_bins[*]}"
                ;;
        esac
        echo
        die "Re-run ./install.sh after installing."
    fi

    # KDE Plasma 6 version sanity check.
    # kwriteconfig6 already gates this -- Plasma 5 ships kwriteconfig5.
    # If the user has kwriteconfig6 they have Plasma 6.

    ok "All required dependencies present."

    # Optional: ReSpeaker xvf_host. Not a blocker.
    if command -v xvf_host >/dev/null 2>&1; then
        ok "ReSpeaker xvf_host detected -- LED control will be enabled."
        check_respeaker_udev
    else
        info "ReSpeaker xvf_host not found -- LED control disabled (this is normal)."
    fi
}

# -- ReSpeaker udev rule check ----------------------------------------------
# xvf_host needs write access to the USB device (VID 2886 / PID 001A) to
# control the LED ring. Without a udev rule, the device's default permissions
# either deny writes outright (LED stays whatever it booted into) or grant
# them only intermittently via uaccess (works until you replug).
#
# We don't install the rule ourselves -- that requires sudo, and install.sh
# is deliberately a userland install. Instead: detect, and print a clear
# one-liner the user can copy-paste if the rule is missing.
check_respeaker_udev() {
    local rule_file="/etc/udev/rules.d/99-respeaker.rules"
    if [[ -f "${rule_file}" ]] && grep -q '2886' "${rule_file}" 2>/dev/null; then
        ok "ReSpeaker udev rule already installed."
        return
    fi

    warn "ReSpeaker udev rule not found at ${rule_file}."
    printf "   Without it, xvf_host may not be able to write to the device,\n"
    printf "   and the LED won't change to reflect VC state.\n"
    printf "   To install the rule, run:\n"
    printf "\n"
    printf "     ${c_bold}sudo tee ${rule_file} <<'EOF'\n"
    printf "     SUBSYSTEM==\"usb\", ATTR{idVendor}==\"2886\", ATTR{idProduct}==\"001a\", MODE=\"0666\"\n"
    printf "     EOF\n"
    printf "     sudo udevadm control --reload-rules\n"
    printf "     sudo udevadm trigger${c_reset}\n"
    printf "\n"
    printf "   Then unplug and replug the ReSpeaker. (You can do this any\n"
    printf "   time -- install will continue without it.)\n"
}

# -- Python version check ----------------------------------------------------
check_python_version() {
    local pyver
    pyver=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    local major="${pyver%.*}"
    local minor="${pyver#*.}"
    if (( major < 3 || (major == 3 && minor < 11) )); then
        die "Python 3.11+ required, found ${pyver}"
    fi
    ok "Python ${pyver} detected."
}

# -- App code ----------------------------------------------------------------
install_app() {
    info "Installing application code to ${APP_DIR}..."
    rm -rf "${APP_DIR}"
    mkdir -p "${APP_DIR}"
    cp -r "${REPO_DIR}/core"           "${APP_DIR}/"
    cp -r "${REPO_DIR}/hardware"       "${APP_DIR}/"
    cp    "${REPO_DIR}/voice_commander.py" "${APP_DIR}/"
    # Strip __pycache__ if any snuck in from local dev
    find "${APP_DIR}" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    ok "Application code installed."
}

# -- Venv --------------------------------------------------------------------
install_venv() {
    info "Creating Python virtual environment..."
    rm -rf "${VENV_DIR}"
    python3 -m venv "${VENV_DIR}"
    "${VENV_DIR}/bin/pip" install --quiet --upgrade pip
    "${VENV_DIR}/bin/pip" install --quiet -r "${REPO_DIR}/requirements.txt"
    ok "Virtual environment ready."
}

# -- Icons -------------------------------------------------------------------
install_icons() {
    info "Installing tray icons to ${ICONS_DIR}..."
    mkdir -p "${ICONS_DIR}"
    cp "${REPO_DIR}/icons/"vc-*.svg "${ICONS_DIR}/"
    ok "Icons installed."
}

# -- KWin script -------------------------------------------------------------
install_kwin_script() {
    info "Installing KWin window-placer script..."
    rm -rf "${KWIN_SCRIPT_DIR}"
    mkdir -p "${KWIN_SCRIPT_DIR}"
    cp -r "${REPO_DIR}/vc-window-placer/." "${KWIN_SCRIPT_DIR}/"

    # CRITICAL: KWin refuses to start() a script whose plugin Id matches an
    # installed package that isn't enabled in [Plugins] -- loadScript returns
    # a valid id and start() succeeds, but the script is silently discarded.
    # (Found the hard way: every placement no-oped. The parent install only
    # worked because Tyler had enabled its plugin by hand years ago.)
    kwriteconfig6 --file "${XDG_CONFIG_HOME:-$HOME/.config}/kwinrc" \
        --group Plugins --key "vcw-window-placerEnabled" true

    # Tell KWin to reload its script list.
    if command -v dbus-send >/dev/null 2>&1; then
        dbus-send --session --print-reply \
            --dest=org.kde.KWin /KWin \
            org.kde.KWin.reconfigure >/dev/null 2>&1 || true
    fi
    ok "KWin script installed."
}

# -- Vosk model --------------------------------------------------------------
install_vosk_model() {
    local model_path="${VOSK_DIR}/${VOSK_MODEL_NAME}"
    if [[ -f "${model_path}/am/final.mdl" ]]; then
        ok "Vosk model already present, skipping download."
        return
    fi

    # A parent Vosk install already has this exact model: copy instead of
    # re-downloading 40 MB. Read-only on the parent side.
    local parent_model="${PARENT_DATA_DIR}/vosk-model/${VOSK_MODEL_NAME}"
    if [[ -f "${parent_model}/am/final.mdl" ]]; then
        info "Copying Vosk model from the parent install..."
        mkdir -p "${VOSK_DIR}"
        cp -r "${parent_model}" "${model_path}"
        ok "Vosk model copied from ${parent_model}."
        return
    fi

    info "Downloading Vosk speech recognition model (~40 MB)..."
    mkdir -p "${VOSK_DIR}"
    local tmp_zip
    tmp_zip=$(mktemp --suffix=.zip)
    # shellcheck disable=SC2064
    trap "rm -f '${tmp_zip}'" EXIT

    if ! curl -fL --progress-bar -o "${tmp_zip}" "${VOSK_MODEL_URL}"; then
        die "Failed to download Vosk model from ${VOSK_MODEL_URL}"
    fi

    info "Extracting model..."
    unzip -q "${tmp_zip}" -d "${VOSK_DIR}"
    rm -f "${tmp_zip}"
    trap - EXIT

    [[ -f "${model_path}/am/final.mdl" ]] \
        || die "Vosk model extracted but doesn't look valid (missing am/final.mdl)"
    ok "Vosk model installed."
}

# -- Whisper backend models ---------------------------------------------------
install_whisper_models() {
    mkdir -p "${MODELS_DIR}/whisper"

    local ggml="${MODELS_DIR}/whisper/ggml-${WHISPER_MODEL_NAME}.bin"
    if [[ -f "${ggml}" ]]; then
        ok "Whisper model already present, skipping download."
    else
        info "Downloading Whisper model ${WHISPER_MODEL_NAME} (~142 MB)..."
        curl -fL --progress-bar -o "${ggml}" "${WHISPER_MODEL_URL}" \
            || die "Failed to download Whisper model from ${WHISPER_MODEL_URL}"
        ok "Whisper model installed."
    fi

    local silero="${MODELS_DIR}/silero_vad.onnx"
    if [[ -f "${silero}" ]]; then
        ok "Silero VAD model already present, skipping download."
    else
        info "Downloading Silero VAD model (~2 MB)..."
        curl -fL --progress-bar -o "${silero}" "${SILERO_VAD_URL}" \
            || die "Failed to download Silero VAD model from ${SILERO_VAD_URL}"
        ok "Silero VAD model installed."
    fi

    # whisper-server is a system binary (pacman: whisper-cpp), not something
    # we install. Only the whisper backend needs it, so warn instead of die.
    if ! command -v whisper-server >/dev/null 2>&1; then
        warn "whisper-server not found on PATH. The whisper backend needs it: install the 'whisper-cpp' package."
    fi
}

# -- Config ------------------------------------------------------------------
install_config() {
    mkdir -p "${CONFIG_DIR}"
    local config="${CONFIG_DIR}/commands.json"
    if [[ -f "${config}" ]]; then
        ok "Existing commands.json preserved (not overwritten)."
    elif [[ -f "${PARENT_CONFIG}" ]]; then
        # First install with a parent Vosk setup present: carry the user's
        # commands/overrides/settings over instead of starting from defaults.
        # Whisper-only keys are absent in the copy; the app falls back to
        # their defaults (all getters use .get()). One-time copy -- the two
        # configs are independent from here on.
        info "Seeding commands.json from the parent install's config..."
        cp "${PARENT_CONFIG}" "${config}"
        # A Models-tab absolute path in vosk_model would point the fork at
        # the PARENT's model dir (breaks if the parent is uninstalled). The
        # fork has its own copy, so reduce it to the bare model name, which
        # resolves against the fork's model dir.
        "${VENV_DIR}/bin/python" - "${config}" <<'PYEOF'
import json, os, sys
path = sys.argv[1]
with open(path) as f:
    cfg = json.load(f)
raw = cfg.get("vosk_model", "")
if os.path.isabs(raw):
    cfg["vosk_model"] = os.path.basename(raw.rstrip("/"))
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)
PYEOF
        ok "Config copied from ${PARENT_CONFIG} (now independent)."
    else
        info "Generating default commands.json..."
        # Generate the default config by running core.commands as a module.
        # This keeps the default command list defined in ONE place (Python),
        # used by both the installer and the in-app 'Restore Defaults' button.
        (cd "${APP_DIR}" && "${VENV_DIR}/bin/python" -m core.commands --emit-defaults) > "${config}" 2>/dev/null
        if [[ $? -ne 0 || ! -s "${config}" ]]; then
            rm -f "${config}"
            die "Failed to generate default config."
        fi
        ok "Default commands.json installed."
    fi
}

# -- README ------------------------------------------------------------------
# Copy README.md to the data dir so the default 'open_readme' command has
# something to open. Overwritten on every install so README changes propagate.
install_readme() {
    info "Installing README to ${DATA_DIR}..."
    cp "${REPO_DIR}/README.md" "${DATA_DIR}/README.md"
    ok "README installed."
}

# -- Service file ------------------------------------------------------------
install_service() {
    info "Generating systemd user service file..."
    mkdir -p "${SYSTEMD_USER_DIR}"
    local service_file="${SYSTEMD_USER_DIR}/${APP_NAME}.service"
    local venv_python="${VENV_DIR}/bin/python"

    sed -e "s|@VENV_PYTHON@|${venv_python}|g" \
        -e "s|@APP_DIR@|${APP_DIR}|g" \
        "${REPO_DIR}/voice-commander.service.in" > "${service_file}"

    # whisper-server companion unit: BindsTo the app unit (never outlives
    # it). Only actually started when recognizer_backend=whisper -- the app
    # starts it on demand (core/whisper_server.py).
    local server_unit="${SYSTEMD_USER_DIR}/${APP_NAME}-server.service"
    sed -e "s|@APP_NAME@|${APP_NAME}|g" \
        "${REPO_DIR}/vc-whisper-server.service.in" > "${server_unit}"

    systemctl --user daemon-reload
    ok "Service units installed at ${service_file} and ${server_unit}"
}

# -- Launcher ----------------------------------------------------------------
install_launcher() {
    info "Installing launcher to ${BIN_DIR}/${APP_NAME}..."
    mkdir -p "${BIN_DIR}"
    local launcher="${BIN_DIR}/${APP_NAME}"
    cat > "${launcher}" <<EOF
#!/usr/bin/env bash
# Voice Commander launcher (installed by install.sh -- do not edit).
exec "${VENV_DIR}/bin/python" "${APP_DIR}/voice_commander.py" "\$@"
EOF
    chmod +x "${launcher}"
    ok "Launcher installed."

    # Warn if ~/.local/bin is not on PATH.
    case ":${PATH}:" in
        *":${BIN_DIR}:"*) ;;
        *) warn "${BIN_DIR} is not on your PATH. Add it to your shell config to use the '${APP_NAME}' command directly." ;;
    esac
}

# -- Desktop entry (app-menu launcher) ---------------------------------------
install_desktop_file() {
    local apps_dir="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
    local desktop_file="${apps_dir}/${APP_NAME}.desktop"
    info "Installing app-menu entry to ${desktop_file}..."
    mkdir -p "${apps_dir}"
    cat > "${desktop_file}" <<EOF
[Desktop Entry]
Type=Application
Name=Voice Commander (Whisper)
Comment=Hands-free voice control for your desktop
Exec=${BIN_DIR}/${APP_NAME} --activate
Icon=${ICONS_DIR}/vc-sleeping.svg
Terminal=false
Categories=Utility;AudioVideo;
StartupNotify=false
EOF
    # Refresh the menu cache so the entry appears without a re-login.
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "${apps_dir}" >/dev/null 2>&1 || true
    fi
    ok "App-menu entry installed."
}

# -- Start (autostart is opt-in; the installer does NOT enable) --------------
start_service() {
    # 0.9.0: autostart is OPT-IN. The installer no longer enables the service at
    # login -- that's the user's choice via Settings -> Options ("Launch on
    # login") or `systemctl --user enable voice-commander.service`. We only
    # start it now so it's usable immediately. On re-install over a running
    # instance, restart to pick up new code.
    if systemctl --user is-active --quiet "${APP_NAME}.service"; then
        info "Service is already running -- restarting to pick up new code..."
        systemctl --user restart "${APP_NAME}.service"
        ok "Service restarted."
    else
        info "Starting service..."
        systemctl --user start "${APP_NAME}.service"
        ok "Service started."
    fi
}

# -- Main --------------------------------------------------------------------
main() {
    printf "${c_bold}Voice Commander (Whisper fork) installer${c_reset}\n"
    printf "Repo:    %s\n" "${REPO_DIR}"
    printf "Install: %s\n" "${DATA_DIR}"
    echo

    check_prereqs
    check_python_version
    install_app
    install_venv
    install_icons
    install_kwin_script
    install_vosk_model
    install_whisper_models
    install_readme
    install_config
    install_service
    install_launcher
    install_desktop_file
    start_service

    echo
    printf "${c_green}${c_bold}Voice Commander (Whisper) ${VC_VERSION} is installed and running.${c_reset}\n"
    echo
    printf "Check status:   ${c_bold}systemctl --user status %s${c_reset}\n" "${APP_NAME}"
    printf "View logs:      ${c_bold}journalctl --user -u %s -f${c_reset}\n" "${APP_NAME}"
    printf "Restart:        ${c_bold}systemctl --user restart %s${c_reset}\n" "${APP_NAME}"
    printf "Open settings:  right-click the tray icon, or say your wake word followed by \"open settings\"\n"
    printf "Launch on login: ${c_bold}off by default${c_reset} -- turn it on in Settings -> Options\n"
    printf "Uninstall:      ${c_bold}./uninstall.sh${c_reset}\n"
    echo
}

main "$@"
