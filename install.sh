#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

PKG_NAME="outervoid-dat"
EXTRAS="${DAT_EXTRAS:-}"

if [[ -n "$EXTRAS" ]]; then
    PKG_SPEC="${PKG_NAME}[${EXTRAS}]"
else
    PKG_SPEC="${PKG_NAME}"
fi

log() {
    printf '%s\n' "$*" >&2
}

ensure_on_path() {
    if command -v dat >/dev/null 2>&1; then
        return 0
    fi
    log "⚠️  'dat' not found on PATH. You may need to add ~/.local/bin or the pipx bin dir."
    return 0
}

install_with_uv() {
    log "→ Installing via uv tool install: ${PKG_SPEC}"
    uv tool install "${PKG_SPEC}"
    ensure_on_path
}

install_with_pipx() {
    log "→ Installing via pipx: ${PKG_SPEC}"
    pipx install "${PKG_SPEC}"
    if command -v pipx >/dev/null 2>&1; then
        pipx ensurepath >/dev/null 2>&1 || true
    fi
    ensure_on_path
}

install_with_pip() {
    local python_bin="${1}"
    log "→ Installing via pip --user: ${PKG_SPEC}"
    "${python_bin}" -m pip install --user "${PKG_SPEC}"
    ensure_on_path
}

main() {
    if command -v uv >/dev/null 2>&1; then
        install_with_uv
        return 0
    fi

    if command -v pipx >/dev/null 2>&1; then
        install_with_pipx
        return 0
    fi

    local python_bin=""
    python_bin="$(command -v python3 || command -v python || true)"
    if [[ -z "$python_bin" ]]; then
        log "❌ Python not found. Install Python 3.9+ and re-run."
        exit 1
    fi

    install_with_pip "$python_bin"
}

main "$@"
