#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

PKG_NAME="outervoid-dat"
EXTRAS="${DAT_EXTRAS:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

MODE=""
USE_VENV="false"
USE_PIP_USER="false"
PREFIX=""
DEBUG="false"
DRY_RUN="false"

if [[ -n "${EXTRAS}" ]]; then
    PKG_SPEC="${PKG_NAME}[${EXTRAS}]"
else
    PKG_SPEC="${PKG_NAME}"
fi

log() {
    printf '%s\n' "$*" >&2
}

log_info() {
    log "[INFO] $*"
}

log_warn() {
    log "[WARN] $*"
}

log_error() {
    log "[ERROR] $*"
}

on_error() {
    local exit_code=$?
    local line_no=$1
    local cmd=$2
    log_error "Command failed (exit ${exit_code}) at line ${line_no}: ${cmd}"
    exit "$exit_code"
}

trap 'on_error ${LINENO} "$BASH_COMMAND"' ERR

is_tty() {
    [[ -t 0 && -t 1 ]]
}

is_termux() {
    [[ -n "${TERMUX_VERSION:-}" || -d "/data/data/com.termux/files/usr" ]]
}

is_proot() {
    [[ -n "${PROOT_DISTRIBUTION_ID:-}" ]] || ( [[ -r /proc/1/cmdline ]] && grep -qa "proot" /proc/1/cmdline )
}

is_wsl() {
    [[ -r /proc/version ]] && grep -qiE "microsoft|wsl" /proc/version
}

is_macos() {
    [[ "$(uname -s)" == "Darwin" ]]
}

is_windows() {
    case "$(uname -s)" in
        MINGW*|MSYS*|CYGWIN*) return 0 ;;
    esac
    [[ "${OS:-}" == "Windows_NT" ]]
}

is_linux() {
    [[ "$(uname -s)" == "Linux" ]]
}

detect_distro() {
    if [[ -r /etc/os-release ]]; then
        # shellcheck disable=SC1091
        . /etc/os-release
        printf '%s' "${ID:-}"
    else
        printf ''
    fi
}

ensure_python() {
    local python_bin
    python_bin="$(command -v python3 || command -v python || true)"
    if [[ -z "$python_bin" ]]; then
        log_error "Python 3.9+ not found. Install Python and re-run."
        exit 1
    fi
    local version
    version="$($python_bin -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo '0.0')"
    local major minor
    major="${version%%.*}"
    minor="${version##*.}"
    if [[ "$major" -ne 3 || "$minor" -lt 9 ]]; then
        log_error "Python 3.9+ required; found ${version}."
        exit 1
    fi
    printf '%s' "$python_bin"
}

maybe_prompt() {
    local prompt="$1"
    local default_no="${2:-true}"
    if ! is_tty; then
        return 1
    fi
    local reply
    if [[ "$default_no" == "true" ]]; then
        read -r -p "${prompt} [y/N] " reply
        [[ "$reply" =~ ^[Yy]$ ]]
    else
        read -r -p "${prompt} [Y/n] " reply
        [[ -z "$reply" || "$reply" =~ ^[Yy]$ ]]
    fi
}

install_system_deps() {
    log_info "Installing system dependencies (best effort)..."

    if is_windows; then
        log_warn "Windows detected. System dependency installation is not supported in this shell."
        log_warn "Use Python + pipx/uv, or PowerShell:"
        log_warn "  py -m pip install --user pipx"
        log_warn "  py -m pipx ensurepath"
        log_warn "  py -m pipx install ${PKG_SPEC}"
        return 0
    fi

    if is_macos; then
        if command -v brew >/dev/null 2>&1; then
            log_info "Using Homebrew to install libmagic (optional)."
            if ! brew install libmagic; then
                log_warn "Homebrew failed to install libmagic."
            fi
        else
            log_warn "Homebrew not found. Install from https://brew.sh then run: brew install libmagic"
        fi
        log_warn "Ensure python3 is installed (brew install python@3.11 or python.org installer)."
        return 0
    fi

    if is_termux && ! is_proot; then
        log_info "Termux detected. Installing dependencies via pkg."
        pkg update -y || log_warn "pkg update failed"
        pkg install -y python3 git curl wget ca-certificates file libmagic jq || log_warn "pkg install failed"
        return 0
    fi

    if is_linux; then
        local distro
        distro="$(detect_distro)"
        local sudo_cmd=""
        if [[ $EUID -ne 0 ]]; then
            if command -v sudo >/dev/null 2>&1; then
                sudo_cmd="sudo"
            else
                log_warn "sudo not available; install dependencies manually."
                return 0
            fi
        fi

        case "$distro" in
            debian|ubuntu|linuxmint)
                log_info "Using apt-get for Debian/Ubuntu."
                ${sudo_cmd} apt-get update || log_warn "apt-get update failed"
                ${sudo_cmd} apt-get install -y python3 python3-venv python3-pip python3-dev build-essential pkg-config libssl-dev libffi-dev file libmagic1 git curl wget ca-certificates jq || log_warn "apt-get install failed"
                ;;
            fedora|rhel|centos|rocky|almalinux)
                log_info "Using dnf for Fedora/RHEL."
                ${sudo_cmd} dnf install -y python3 python3-pip python3-devel gcc gcc-c++ make pkgconf-pkg-config openssl-devel libffi-devel file file-libs git curl wget ca-certificates jq || log_warn "dnf install failed"
                ;;
            arch|manjaro)
                log_info "Using pacman for Arch."
                ${sudo_cmd} pacman -S --noconfirm python python-pip base-devel pkgconf openssl libffi file git curl wget ca-certificates jq || log_warn "pacman install failed"
                ;;
            *)
                log_warn "Unsupported Linux distro (${distro}). Install dependencies manually."
                ;;
        esac
        return 0
    fi

    log_warn "Unsupported platform for dependency installation."
}

warn_termux_proot_prefix() {
    if ! is_proot; then
        return 0
    fi
    if ! command -v dat >/dev/null 2>&1; then
        return 0
    fi
    local dat_path
    dat_path="$(command -v dat || true)"
    if [[ "$dat_path" == /data/data/com.termux/files/usr/* ]]; then
        log_warn "Detected DAT from Termux prefix while running in proot-distro."
        log_warn "This can cause prefix hijack. Consider uninstalling from Termux:"
        log_warn "  pkg uninstall python3 && pkg uninstall pipx"
        log_warn "  pipx uninstall ${PKG_NAME}"
    fi
}

ensure_on_path() {
    if command -v dat >/dev/null 2>&1; then
        return 0
    fi
    log_warn "'dat' not found on PATH. Ensure ~/.local/bin or pipx bin dir is in PATH."
}

pipx_env() {
    if [[ -n "$PREFIX" ]]; then
        export PIPX_HOME="${PREFIX}/pipx"
        export PIPX_BIN_DIR="${PREFIX}/bin"
    fi
}

install_with_pipx() {
    pipx_env
    local spec="$1"
    local editable="$2"
    log_info "Installing via pipx: ${spec}"
    if [[ "$editable" == "true" ]]; then
        pipx install --editable "$spec"
    else
        pipx install "$spec"
    fi
    if command -v pipx >/dev/null 2>&1; then
        pipx ensurepath >/dev/null 2>&1 || true
    fi
    ensure_on_path
}

install_with_uv() {
    local spec="$1"
    local editable="$2"
    if [[ -n "$PREFIX" ]]; then
        log_warn "--prefix is not supported for uv tool installs. Ignoring prefix."
    fi
    log_info "Installing via uv tool install: ${spec}"
    if [[ "$editable" == "true" ]]; then
        uv tool install --editable "$spec"
    else
        uv tool install "$spec"
    fi
    ensure_on_path
}

install_with_venv() {
    local python_bin="$1"
    local spec="$2"
    local editable="$3"
    local venv_path="${PREFIX:-${REPO_ROOT}/.venv}"
    log_info "Installing into venv at ${venv_path}"
    "$python_bin" -m venv "$venv_path"
    local venv_python="${venv_path}/bin/python"
    if [[ ! -x "$venv_python" ]]; then
        log_error "Venv python not found at ${venv_python}"
        exit 1
    fi
    "$venv_python" -m pip install --upgrade pip
    if [[ "$editable" == "true" ]]; then
        "$venv_python" -m pip install --editable "$spec"
    else
        "$venv_python" -m pip install "$spec"
    fi
    log_info "Activate with: source ${venv_path}/bin/activate"
    ensure_on_path
}

install_with_pip_user() {
    local python_bin="$1"
    local spec="$2"
    local editable="$3"
    if [[ -n "$PREFIX" ]]; then
        log_warn "--prefix is not supported for --pip-user installs. Ignoring prefix."
    fi
    log_info "Installing via pip --user: ${spec}"
    if [[ "$editable" == "true" ]]; then
        "$python_bin" -m pip install --user --editable "$spec"
    else
        "$python_bin" -m pip install --user "$spec"
    fi
    ensure_on_path
}

print_env_summary() {
    local distro
    distro="$(detect_distro)"
    log_info "Detected environment summary:"
    log_info "  OS: $(uname -s)"
    log_info "  Termux: $(is_termux && echo yes || echo no)"
    log_info "  proot-distro: $(is_proot && echo yes || echo no)"
    log_info "  WSL: $(is_wsl && echo yes || echo no)"
    log_info "  macOS: $(is_macos && echo yes || echo no)"
    log_info "  Windows shell: $(is_windows && echo yes || echo no)"
    log_info "  Distro: ${distro:-unknown}"
}

usage() {
    cat <<'USAGE'
Usage: scripts/install.sh [options]

Options:
  --mode <pypi|prod|dev>   Install mode (default: dev inside repo, else pypi).
  --venv                   Install into local .venv (fallback when pipx/uv unavailable).
  --pip-user               Install via pip --user (fallback when pipx/uv unavailable).
  --prefix <path>          Advanced: override prefix (pipx home/bin or venv path).
  --dry-run                Print detected environment and planned actions, then exit.
  --debug                  Enable bash debug output.
  --help                   Show help.
USAGE
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --mode)
                MODE="${2:-}"
                shift 2
                ;;
            --venv)
                USE_VENV="true"
                shift
                ;;
            --pip-user)
                USE_PIP_USER="true"
                shift
                ;;
            --prefix)
                PREFIX="${2:-}"
                shift 2
                ;;
            --debug)
                DEBUG="true"
                shift
                ;;
            --dry-run)
                DRY_RUN="true"
                shift
                ;;
            --help|-h)
                usage
                exit 0
                ;;
            *)
                log_error "Unknown argument: $1"
                usage
                exit 1
                ;;
        esac
    done
}

resolve_mode() {
    if [[ -n "$MODE" ]]; then
        return 0
    fi
    if [[ -f "${REPO_ROOT}/pyproject.toml" ]]; then
        MODE="dev"
    else
        MODE="pypi"
    fi
}

build_spec() {
    local mode="$1"
    local spec
    case "$mode" in
        pypi)
            spec="$PKG_SPEC"
            ;;
        prod|dev)
            if [[ ! -f "${REPO_ROOT}/pyproject.toml" ]]; then
                log_error "Local repo install requested but pyproject.toml not found at ${REPO_ROOT}."
                exit 1
            fi
            if [[ -n "$EXTRAS" ]]; then
                spec="${REPO_ROOT}[${EXTRAS}]"
            else
                spec="${REPO_ROOT}"
            fi
            ;;
        *)
            log_error "Unsupported mode: ${mode}"
            exit 1
            ;;
    esac
    printf '%s' "$spec"
}

main() {
    parse_args "$@"

    if [[ "$DEBUG" == "true" ]]; then
        set -x
    fi

    resolve_mode

    print_env_summary
    warn_termux_proot_prefix

    local spec
    spec="$(build_spec "$MODE")"

    local editable="false"
    if [[ "$MODE" == "dev" ]]; then
        editable="true"
    fi

    if [[ "$USE_VENV" == "true" && "$USE_PIP_USER" == "true" ]]; then
        log_error "--venv and --pip-user are mutually exclusive."
        exit 1
    fi

    if [[ "$DRY_RUN" == "true" ]]; then
        local method
        if [[ "$USE_VENV" == "true" ]]; then
            method="venv"
        elif [[ "$USE_PIP_USER" == "true" ]]; then
            method="pip --user"
        elif command -v pipx >/dev/null 2>&1; then
            method="pipx"
        elif command -v uv >/dev/null 2>&1; then
            method="uv tool install"
        else
            method="none (pipx/uv not found)"
        fi
        log_info "Dry run: mode=${MODE}, spec=${spec}, editable=${editable}, method=${method}"
        return 0
    fi

    install_system_deps

    if [[ "$USE_VENV" == "true" ]]; then
        local python_bin
        python_bin="$(ensure_python)"
        install_with_venv "$python_bin" "$spec" "$editable"
        return 0
    fi

    if [[ "$USE_PIP_USER" == "true" ]]; then
        local python_bin
        python_bin="$(ensure_python)"
        install_with_pip_user "$python_bin" "$spec" "$editable"
        return 0
    fi

    if command -v pipx >/dev/null 2>&1; then
        install_with_pipx "$spec" "$editable"
        return 0
    fi

    if command -v uv >/dev/null 2>&1; then
        install_with_uv "$spec" "$editable"
        return 0
    fi

    log_warn "Neither pipx nor uv is available."
    if maybe_prompt "Install using a local venv instead?"; then
        local python_bin
        python_bin="$(ensure_python)"
        install_with_venv "$python_bin" "$spec" "$editable"
        return 0
    fi

    log_error "No supported install method found. Install pipx or uv, or re-run with --venv or --pip-user."
    exit 1
}

main "$@"
