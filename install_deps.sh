#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

# Colors for better output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Logging functions (all to stderr)
log_info() { echo -e "${BLUE}[INFO]${NC} $1" >&2; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1" >&2; }
log_warning() { echo -e "${YELLOW}[WARNING]${NC} $1" >&2; }
log_error() { echo -e "${RED}[ERROR]${NC} $1" >&2; }

# Print banner
print_banner() {
    echo -e "${CYAN}"
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║                   DAT Installer v3.0.0                       ║"
    echo "║         Enterprise Security Scanning Tool                    ║"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
}

# Check if running as root
check_root() {
    if [[ $EUID -eq 0 ]]; then
        log_warning "Running as root user - this is not recommended"
        read -p "Continue anyway? [y/N] " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi
}

# Robust Python detection
detect_python() {
    log_info "Detecting Python..."
    
    # Try different Python commands in order of preference
    for py_cmd in python3.12 python3.11 python3.10 python3.9 python3 python; do
        if command -v "$py_cmd" >/dev/null 2>&1; then
            # Check Python version
            version=$("$py_cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
            
            # Extract major and minor version
            major=$(echo "$version" | cut -d. -f1)
            minor=$(echo "$version" | cut -d. -f2)
            
            if [[ $major -eq 3 ]] && [[ $minor -ge 9 ]]; then
                log_success "Found Python $version using $py_cmd"
                printf '%s\n' "$py_cmd"
                return 0
            else
                log_warning "Python $version found but 3.9+ required"
            fi
        fi
    done
    
    log_error "No suitable Python 3.9+ installation found"
    log_info "Please install Python 3.9 or newer from: https://python.org/downloads/"
    return 1
}

# Install DAT package
install_dat_package() {
    local python_cmd="$1"
    local install_mode="$2"
    
    log_info "Installing DAT package ($install_mode)..."
    
    case "$install_mode" in
        "dev")
            # Development mode - install from current directory
            if [[ -f "pyproject.toml" ]]; then
                log_info "Installing in development mode from pyproject.toml"
                if "$python_cmd" -m pip install -e .[dev]; then
                    log_success "DAT installed in development mode"
                else
                    log_error "Failed to install DAT in development mode"
                    return 1
                fi
            else
                log_error "pyproject.toml not found - cannot install in development mode"
                return 1
            fi
            ;;
        "prod")
            # Production mode - install from current directory
            if [[ -f "pyproject.toml" ]]; then
                log_info "Installing in production mode from pyproject.toml"
                if "$python_cmd" -m pip install .; then
                    log_success "DAT installed in production mode"
                else
                    log_error "Failed to install DAT in production mode"
                    return 1
                fi
            elif [[ -f "requirements.txt" ]]; then
                log_info "Installing from requirements.txt"
                if "$python_cmd" -m pip install -r requirements.txt; then
                    log_success "DAT installed from requirements.txt"
                else
                    log_error "Failed to install from requirements.txt"
                    return 1
                fi
            else
                log_error "No installation files found (pyproject.toml or requirements.txt)"
                return 1
            fi
            ;;
        "pypi")
            # Install from PyPI
            log_info "Installing from PyPI..."
            if "$python_cmd" -m pip install outervoid-dat; then
                log_success "DAT installed from PyPI"
            else
                log_error "Failed to install from PyPI"
                log_info "Trying with --user flag..."
                if "$python_cmd" -m pip install --user outervoid-dat; then
                    log_success "DAT installed from PyPI with --user flag"
                else
                    return 1
                fi
            fi
            ;;
    esac
    
    return 0
}

# Install system dependencies
install_system_deps() {
    local os=$(uname -s | tr '[:upper:]' '[:lower:]')
    
    log_info "Detected platform: $os"
    
    case "$os" in
        linux*)
            install_linux_deps
            ;;
        darwin*)
            install_macos_deps
            ;;
        mingw*|cygwin*|msys*)
            install_windows_deps
            ;;
        *)
            log_warning "Unsupported platform: $os"
            show_manual_instructions
            ;;
    esac
}

# Linux dependency installation
install_linux_deps() {
    log_info "Installing Linux dependencies..."
    
    # Detect distribution
    if [[ -f /etc/os-release ]]; then
        source /etc/os-release
        distro=$ID
    else
        log_warning "Cannot detect Linux distribution"
        return 1
    fi
    
    case "$distro" in
        ubuntu|debian|linuxmint)
            log_info "Detected Ubuntu/Debian-based system"
            # Install libmagic for file type detection
            if command -v sudo >/dev/null 2>&1; then
                sudo apt-get update
                if sudo apt-get install -y libmagic1 file; then
                    log_success "Linux dependencies installed"
                else
                    log_warning "Failed to install some dependencies, continuing..."
                fi
            else
                log_warning "sudo not available, please install manually: apt-get install libmagic1 file"
            fi
            ;;
        fedora|rhel|centos)
            log_info "Detected Fedora/RHEL-based system"
            if command -v sudo >/dev/null 2>&1; then
                sudo dnf install -y file-libs file
            else
                log_warning "sudo not available, please install manually: dnf install file-libs file"
            fi
            ;;
        arch|manjaro)
            log_info "Detected Arch Linux-based system"
            if command -v sudo >/dev/null 2>&1; then
                sudo pacman -S --noconfirm file
            else
                log_warning "sudo not available, please install manually: pacman -S file"
            fi
            ;;
        *)
            log_warning "Unsupported Linux distribution: $distro"
            ;;
    esac
}

# macOS dependency installation
install_macos_deps() {
    log_info "Installing macOS dependencies..."
    
    if command -v brew >/dev/null 2>&1; then
        if brew install libmagic; then
            log_success "macOS dependencies installed"
        else
            log_warning "Failed to install libmagic with Homebrew"
        fi
    else
        log_warning "Homebrew not installed. Install from: https://brew.sh"
    fi
}

# Windows dependency installation
install_windows_deps() {
    log_info "Windows detected - using python-magic-bin for file detection"
    # python-magic-bin will be installed via pip
    return 0
}

# Show manual instructions
show_manual_instructions() {
    log_info "Manual installation instructions:"
    log_info "Ubuntu/Debian: sudo apt-get install libmagic1 file"
    log_info "Fedora/RHEL:   sudo dnf install file-libs file"
    log_info "Arch Linux:    sudo pacman -S file"
    log_info "macOS:         brew install libmagic"
}

# Verify installation
verify_installation() {
    local python_cmd="$1"
    
    log_info "Verifying installation..."
    
    "$python_cmd" -c "
import sys
print('Verifying DAT installation...')

# Test core imports
tests = [
    ('rich', 'from rich.console import Console'),
    ('colorama', 'import colorama'),
    ('typer', 'import typer'),
    ('yaml', 'import yaml'),
    ('packaging', 'import packaging.version')
]

all_ok = True
for name, test_code in tests:
    try:
        exec(test_code)
        print(f'✅ {name}: OK')
    except ImportError as e:
        print(f'❌ {name}: FAILED - {e}')
        all_ok = False

# Test DAT package
try:
    import dat
    print(f'✅ dat: OK (version {dat.__version__})')
except ImportError as e:
    print(f'❌ dat: FAILED - {e}')
    all_ok = False

# Test DAT CLI
try:
    from dat.cli import main
    print('✅ dat.cli: OK')
except ImportError as e:
    print(f'❌ dat.cli: FAILED - {e}')
    all_ok = False

if all_ok:
    print('All core dependencies verified!')
else:
    print('Some dependencies failed - DAT may have limited functionality')
    sys.exit(1)
"
}

# Setup DAT executable
setup_dat() {
    local python_cmd="$1"
    
    log_info "Testing DAT installation..."
    
    # Test DAT functionality
    if command -v dat >/dev/null 2>&1; then
        log_success "DAT command available in PATH"
        if dat --version >/dev/null 2>&1 || dat --help >/dev/null 2>&1; then
            log_success "DAT is functional"
            return 0
        fi
    fi
    
    # Fallback: try python module
    log_info "Trying Python module execution..."
    if "$python_cmd" -m dat --version >/dev/null 2>&1 || "$python_cmd" -m dat --help >/dev/null 2>&1; then
        log_success "DAT works via Python module"
        log_info "You can run DAT using: $python_cmd -m dat"
        return 0
    fi
    
    log_warning "DAT command not found in PATH"
    log_info "You may need to add Python user base bin to your PATH:"
    log_info "  export PATH=\"\$($python_cmd -m site --user-base)/bin:\$PATH\""
    return 1
}

# Ask for installation mode
choose_installation_mode() {
    echo
    log_info "Choose installation mode:"
    echo "1) Development - Install from current source with dev dependencies"
    echo "2) Production - Install from current source (production only)"
    echo "3) PyPI - Install latest release from PyPI"
    echo
    read -p "Enter choice [1-3] (default: 1): " choice
    
    case "${choice:-1}" in
        1|"") echo "dev" ;;
        2) echo "prod" ;;
        3) echo "pypi" ;;
        *) 
            log_error "Invalid choice"
            choose_installation_mode
            ;;
    esac
}

# Main installation function
main() {
    print_banner
    check_root
    
    log_info "Starting DAT installation..."
    
    # Detect Python
    PYTHON_CMD=$(detect_python) || exit 1
    
    # Choose installation mode
    INSTALL_MODE=$(choose_installation_mode)
    
    # Upgrade pip
    log_info "Upgrading pip..."
    if ! "$PYTHON_CMD" -m pip install --upgrade pip; then
        log_warning "pip upgrade failed, continuing anyway..."
    fi
    
    # Install system dependencies (non-critical)
    log_info "Installing system dependencies..."
    if install_system_deps; then
        log_success "System dependencies handled"
    else
        log_warning "System dependencies may need manual installation"
    fi
    
    # Install DAT package
    log_info "Installing DAT package..."
    install_dat_package "$PYTHON_CMD" "$INSTALL_MODE" || exit 1
    
    # Verify installation
    verify_installation "$PYTHON_CMD" || exit 1
    
    # Setup DAT
    log_info "Setting up DAT..."
    setup_dat "$PYTHON_CMD"
    
    # Success message
    echo
    log_success "🎉 DAT installation completed successfully!"
    echo
    log_info "🚀 Quick Start:"
    
    if command -v dat >/dev/null 2>&1; then
        log_info "  dat --version    # Check version"
        log_info "  dat --help       # Show help"
        log_info "  dat scan         # Scan current directory"
    else
        log_info "  $PYTHON_CMD -m dat --version    # Check version"
        log_info "  $PYTHON_CMD -m dat --help       # Show help"
        log_info "  $PYTHON_CMD -m dat scan         # Scan current directory"
    fi
    
    echo
    log_info "📚 Documentation: https://github.com/Outer-Void/dat"
    log_info "🐛 Issues: https://github.com/Outer-Void/dat/issues"
    echo
}

# Run main function
main "$@"
