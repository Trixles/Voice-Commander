#!/usr/bin/env bash
#
# install.sh -- Voice Commander installer
#
# Installs under the voice-commander namespace. 2.0.0 reclaimed that name
# from the retired Vosk build (now the Voice-Commander-Vosk repo); every
# path below derives from core/paths.py APP_NAME, which is what made the
# rename a two-constant change.
#
#   ~/.local/share/voice-commander/app/      -- application code
#   ~/.local/share/voice-commander/venv/     -- Python virtual environment
#   ~/.local/share/voice-commander/icons/    -- tray + service icons
#   ~/.local/share/voice-commander/models/   -- whisper ggml + silero VAD
#   ~/.local/share/kwin/scripts/vc-window-placer/   -- KWin helper script
#   ~/.config/voice-commander/commands.json  -- user-editable config
#   ~/.config/systemd/user/voice-commander.service        -- app unit
#   ~/.config/systemd/user/voice-commander-server.service -- whisper-server unit
#   ~/.local/bin/voice-commander             -- launcher command
#
# Re-running this script is safe: it preserves the existing commands.json
# and uninstalls/reinstalls everything else.

set -euo pipefail

# -- Constants ---------------------------------------------------------------
readonly APP_NAME="voice-commander"
readonly REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

readonly DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/${APP_NAME}"
readonly CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/${APP_NAME}"
readonly BIN_DIR="$HOME/.local/bin"
readonly SYSTEMD_USER_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
# Placer Id must match core/paths.py PLACER_ID -- it namespaces the kwinrc
# queue group, keeping the fork's placement queue separate from the parent's.
readonly KWIN_SCRIPT_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/kwin/scripts/vc-window-placer"

readonly APP_DIR="${DATA_DIR}/app"
readonly VENV_DIR="${DATA_DIR}/venv"
readonly ICONS_DIR="${DATA_DIR}/icons"
readonly MODELS_DIR="${DATA_DIR}/models"

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

# Global variables for remote whisper configuration
USE_REMOTE_WHISPER="false"
REMOTE_WHISPER_HOST=""
REMOTE_WHISPER_PORT=""

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

# -- Remote Whisper Configuration Prompt ------------------------------------
prompt_whisper_backend() {
    info "Whisper Backend Configuration"
    printf "Do you want to use a remote Whisper server (IP:Port) instead of local? [y/N]: "
    read -r use_remote

    if [[ "$use_remote" =~ ^[Yy]$ ]]; then
        printf "Enter remote Whisper IP/Host [default: 127.0.0.1]: "
        read -r REMOTE_WHISPER_HOST
        REMOTE_WHISPER_HOST="${REMOTE_WHISPER_HOST:-127.0.0.1}"

        printf "Enter remote Whisper Port [default: 8080]: "
        read -r REMOTE_WHISPER_PORT
        REMOTE_WHISPER_PORT="${REMOTE_WHISPER_PORT:-8080}"

        USE_REMOTE_WHISPER="true"
        ok "Configured to use remote Whisper server at ${REMOTE_WHISPER_HOST}:${REMOTE_WHISPER_PORT}"
    else
        USE_REMOTE_WHISPER="false"
        ok "Configured to use local Whisper backend."
    fi
    echo
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
    [systemctl]="systemd|systemd"
    [whisper-cli]="whisper-cpp|whisper-cpp"
)

# List of packages that belong to the AUR on Arch (adjust as needed)
declare -A ARCH_AUR_PKGS=(
    [whisper-cpp]=1
)

check_prereqs() {
    # If using a remote whisper server, whisper-cli is not required locally
    if [[ "$USE_REMOTE_WHISPER" == "true" ]]; then
        unset REQUIRED_BINS[whisper-cli]
    fi

    info "Checking prerequisites..."
    local distro
    distro=$(detect_distro)

    local missing_bins=()
    local arch_pacman_pkgs=()
    local arch_aur_pkgs=()
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

            if [[ "$pkg" != "-" && "$pkg" != "?" ]]; then
                if [[ "$distro" == "arch" ]]; then
                    if [[ -n "${ARCH_AUR_PKGS[$pkg]}" ]]; then
                        arch_aur_pkgs+=("$pkg")
                    else
                        arch_pacman_pkgs+=("$pkg")
                    fi
                else
                    missing_pkgs+=("$pkg")
                fi
            fi
        fi
    done

    if [[ ${#missing_bins[@]} -gt 0 ]]; then
        err "Missing required dependencies: ${missing_bins[*]}"
        echo
        case "$distro" in
            arch)
                if [[ ${#arch_pacman_pkgs[@]} -gt 0 ]]; then
                    printf "To install official packages on Arch/CachyOS/Manjaro, run:\n"
                    printf "    ${c_bold}sudo pacman -S %s${c_reset}\n\n" "${arch_pacman_pkgs[*]}"
                fi
                if [[ ${#arch_aur_pkgs[@]} -gt 0 ]]; then
                    if command -v yay >/dev/null 2>&1; then
                        printf "To install AUR packages, run:\n"
                        printf "    ${c_bold}yay -S %s${c_reset}\n\n" "${arch_aur_pkgs[*]}"
                    else
                        printf "${c_bold}Note:${c_reset} The following package(s) are from the AUR (yay not detected):\n"
                        printf "    %s\n" "${arch_aur_pkgs[*]}"
                        printf "Please install them manually using your preferred AUR helper (e.g., yay, paru).\n\n"
                    fi
                fi
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
    # Named interpreter symlink: the service and launcher exec through
    # this so the process shows up as "voice-commander" in ps/System
    # Monitor instead of an anonymous "python" (the kernel names a
    # process after the path it was exec'd as). "voice-commander" is
    # exactly 15 chars -- the kernel's comm limit -- so it fits untruncated.
    # venv detection still works: pyvenv.cfg resolves relative to the
    # symlink's directory.
    ln -sf python "${VENV_DIR}/bin/voice-commander"
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
        --group Plugins --key "vc-window-placerEnabled" true

    # Tell KWin to reload its script list.
    if command -v dbus-send >/dev/null 2>&1; then
        dbus-send --session --print-reply \
            --dest=org.kde.KWin /KWin \
            org.kde.KWin.reconfigure >/dev/null 2>&1 || true
    fi
    ok "KWin script installed."
}

# -- Whisper backend models ---------------------------------------------------
install_whisper_models() {
    mkdir -p "${MODELS_DIR}/whisper"

    if [[ "$USE_REMOTE_WHISPER" == "true" ]]; then
        info "Using remote Whisper server -- skipping local model download."
    else
        local ggml="${MODELS_DIR}/whisper/ggml-${WHISPER_MODEL_NAME}.bin"
        if [[ -f "${ggml}" ]]; then
            ok "Whisper model already present, skipping download."
        else
            info "Downloading Whisper model ${WHISPER_MODEL_NAME} (~142 MB)..."
            curl -fL --progress-bar -o "${ggml}" "${WHISPER_MODEL_URL}" \
                || die "Failed to download Whisper model from ${WHISPER_MODEL_URL}"
            ok "Whisper model installed."
        fi
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
    # we install. Only the local whisper backend needs it, so warn instead of die.
    if [[ "$USE_REMOTE_WHISPER" != "true" ]]; then
        if ! command -v whisper-server >/dev/null 2>&1; then
            warn "whisper-server not found on PATH. The local whisper backend needs it: install the 'whisper-cpp' package."
        fi
    fi
}

# -- Config ------------------------------------------------------------------
install_config() {
    mkdir -p "${CONFIG_DIR}"
    local config="${CONFIG_DIR}/commands.json"
    if [[ -f "${config}" ]]; then
        ok "Existing commands.json preserved (not overwritten)."

        # If using remote whisper, we can update or inject settings if needed
        if [[ "$USE_REMOTE_WHISPER" == "true" ]]; then
            info "Configuring existing commands.json to use remote whisper at ${REMOTE_WHISPER_HOST}:${REMOTE_WHISPER_PORT}..."
            "${VENV_DIR}/bin/python" -c "
import json, os
path = '${config}'
try:
    with open(path, 'r') as f:
        data = json.load(f)
    data['recognizer_backend'] = 'whisper_remote' # adjust keys according to your codebase if necessary
    data['whisper_host'] = os.environ.get('REMOTE_WHISPER_HOST')
    data['whisper_port'] = int(os.environ.get('REMOTE_WHISPER_PORT', 8080))
    with open(path, 'w') as f:
        json.dump(data, f, indent=4)
except Exception as e:
    print(f'Warning: Could not auto-update config: {e}')
" 2>/dev/null || true
        fi
    else
        info "Generating default commands.json..."
        (cd "${APP_DIR}" && "${VENV_DIR}/bin/python" -m core.commands --emit-defaults) > "${config}" 2>/dev/null
        if [[ $? -ne 0 || ! -s "${config}" ]]; then
            rm -f "${config}"
            die "Failed to generate default config."
        fi

        # If remote whisper is enabled, patch the newly generated config file
        if [[ "$USE_REMOTE_WHISPER" == "true" ]]; then
            info "Injecting remote whisper settings into commands.json..."
            "${VENV_DIR}/bin/python" -c "
import json, os
path = '${config}'
with open(path, 'r') as f:
    data = json.load(f)
data['whisper_host'] = os.environ.get('REMOTE_WHISPER_HOST')
data['whisper_port'] = int(os.environ.get('REMOTE_WHISPER_PORT', 8080))
with open(path, 'w') as f:
    json.dump(data, f, indent=4)
" 2>/dev/null || true
        fi

        ok "Default commands.json installed."
    fi
}

# -- README ------------------------------------------------------------------
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
    local venv_python="${VENV_DIR}/bin/voice-commander"

    sed -e "s|@VENV_PYTHON@|${venv_python}|g" \
        -e "s|@APP_DIR@|${APP_DIR}|g" \
        "${REPO_DIR}/voice-commander.service.in" > "${service_file}"

    # whisper-server companion unit: only created/used if NOT using a remote whisper server
    local server_unit="${SYSTEMD_USER_DIR}/${APP_NAME}-server.service"
    if [[ "$USE_REMOTE_WHISPER" == "true" ]]; then
        # Remove local server unit if it exists from previous installations
        rm -f "${server_unit}"
        ok "Skipped local whisper-server companion unit (using remote server)."
    else
        sed -e "s|@APP_NAME@|${APP_NAME}|g" \
            "${REPO_DIR}/vc-whisper-server.service.in" > "${server_unit}"
        ok "Service units installed at ${service_file} and ${server_unit}"
    fi

    systemctl --user daemon-reload
}

# -- Launcher ----------------------------------------------------------------
install_launcher() {
    info "Installing launcher to ${BIN_DIR}/${APP_NAME}..."
    mkdir -p "${BIN_DIR}"
    local launcher="${BIN_DIR}/${APP_NAME}"
    cat > "${launcher}" <<EOF
#!/usr/bin/env bash
# Voice Commander launcher (installed by install.sh -- do not edit).
exec "${VENV_DIR}/bin/voice-commander" "${APP_DIR}/voice_commander.py" "\$@"
EOF
    chmod +x "${launcher}"
    ok "Launcher installed."

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
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "${apps_dir}" >/dev/null 2>&1 || true
    fi
    ok "App-menu entry installed."
}

# -- Start -------------------------------------------------------------------
start_service() {
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
    printf "${c_bold}Voice Commander installer${c_reset}\n"
    printf "Repo:    %s\n" "${REPO_DIR}"
    printf "Install: %s\n" "${DATA_DIR}"
    echo

    prompt_whisper_backend
    check_prereqs
    check_python_version
    install_app
    install_venv
    install_icons
    install_kwin_script
    install_whisper_models
    install_readme
    install_config
    install_service
    install_launcher
    install_desktop_file
    start_service

    echo
    printf "${c_green}${c_bold}Voice Commander (Whisper) ${VC_VERSION} is installed and running.${c_reset}\n"
    if [[ "$USE_REMOTE_WHISPER" == "true" ]]; then
        printf "Whisper Mode:   ${c_bold}Remote (${REMOTE_WHISPER_HOST}:${REMOTE_WHISPER_PORT})${c_reset}\n"
    else
        printf "Whisper Mode:   ${c_bold}Local${c_reset}\n"
    fi
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
