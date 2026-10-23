#!/usr/bin/env bash
# ==============================================================================
# Lexi Copilot — One-Command Native Installer for Linux
# Architected & Created by Shakeel
# ==============================================================================

set -e

BOLD="\033[1m"
GREEN="\033[32m"
CYAN="\033[36m"
YELLOW="\033[33m"
RED="\033[31m"
RESET="\033[0m"

echo -e "${CYAN}${BOLD}"
cat << 'EOF'
  _               _   ____                 _ _       _   
 | |    _____  __(_) / ___|___  _ __  _ (_) | ___ | |_ 
 | |   / _ \ \/ /| | | |   / _ \| '_ \| | | |/ _ \| __|
 | |__|  __/>  < | | | |__| (_) | |_) | | | | (_) | |_ 
 |_____\___/_/\_\|_|  \____\___/| .__/|_|_|_|\___/ \__|
                                |_|                     
EOF
echo -e "${RESET}${BOLD}Native Dynamic Island AI Copilot & AirDrop for Linux${RESET}\n"

INSTALL_DIR="${HOME}/.local/share/mochi-copilot"
REPO_URL="https://github.com/shakeel-777/lexi-copilot.git"

# 1. Detect Package Manager and Install System Dependencies
echo -e "${CYAN}==>${RESET} Detecting system package manager..."
if command -v pacman >/dev/null 2>&1; then
    echo -e "${GREEN}==>${RESET} Arch Linux detected. Installing native dependencies via pacman..."
    sudo pacman -S --needed --noconfirm \
        gtk4 libadwaita python python-gobject python-cairo python-pillow \
        python-aiohttp python-qrcode xdotool scrcpy xdg-desktop-portal \
        libportal libportal-gtk4 libnotify 2>/dev/null || true
elif command -v dnf >/dev/null 2>&1; then
    echo -e "${GREEN}==>${RESET} Fedora detected. Installing dependencies via dnf..."
    sudo dnf install -y \
        gtk4 libadwaita python3-gobject python3-cairo python3-pillow \
        python3-aiohttp python3-qrcode xdotool scrcpy libnotify 2>/dev/null || true
elif command -v apt-get >/dev/null 2>&1; then
    echo -e "${GREEN}==>${RESET} Debian/Ubuntu detected. Installing dependencies via apt..."
    sudo apt-get update -qq
    sudo apt-get install -y \
        libgtk-4-dev libadwaita-1-dev python3-gi python3-gi-cairo python3-pil \
        python3-aiohttp python3-qrcode xdotool scrcpy libnotify-bin 2>/dev/null || true
else
    echo -e "${YELLOW}==>${RESET} Unrecognized package manager. Please ensure GTK4, Libadwaita, and Python 3.10+ are installed."
fi

# 2. Clone or Update Repository
if [ -d "${INSTALL_DIR}/.git" ]; then
    echo -e "${CYAN}==>${RESET} Updating existing installation at ${INSTALL_DIR}..."
    cd "${INSTALL_DIR}"
    git pull origin master || true
elif [ -f "lexi.py" ]; then
    echo -e "${CYAN}==>${RESET} Running directly from repository directory: $(pwd)"
    INSTALL_DIR="$(pwd)"
else
    echo -e "${CYAN}==>${RESET} Cloning Lexi Copilot into ${INSTALL_DIR}..."
    mkdir -p "$(dirname "${INSTALL_DIR}")"
    git clone "${REPO_URL}" "${INSTALL_DIR}"
    cd "${INSTALL_DIR}"
fi

# 3. Install Python Dependencies
if [ -f "${INSTALL_DIR}/requirements.txt" ]; then
    echo -e "${CYAN}==>${RESET} Verifying Python dependencies..."
    python3 -m pip install -r "${INSTALL_DIR}/requirements.txt" --break-system-packages --quiet 2>/dev/null || \
    python3 -m pip install -r "${INSTALL_DIR}/requirements.txt" --quiet 2>/dev/null || true
fi

# 4. Install Desktop Application Launcher
echo -e "${CYAN}==>${RESET} Registering desktop application..."
mkdir -p "${HOME}/.local/share/applications"
cat > "${HOME}/.local/share/applications/lexi.desktop" << DESKTOP_EOF
[Desktop Entry]
Name=Lexi Copilot
Comment=Autonomous Dynamic Island AI Desktop Companion & Screen Copilot
Exec=python3 ${INSTALL_DIR}/lexi.py
Icon=${INSTALL_DIR}/drop_web/icon-512.png
Terminal=false
Type=Application
Categories=Utility;Office;AI;
Keywords=AI;Copilot;Assistant;Screen;DynamicIsland;
DESKTOP_EOF
chmod +x "${HOME}/.local/share/applications/lexi.desktop"

# 5. Configure Systemd User Service (Permanent Dynamic Island Notch)
echo -e "${CYAN}==>${RESET} Configuring background systemd user service..."
mkdir -p "${HOME}/.config/systemd/user"
cat > "${HOME}/.config/systemd/user/lexi-island.service" << SERVICE_EOF
[Unit]
Description=Lexi Dynamic Island (Permanent Desktop Notch)
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 ${INSTALL_DIR}/lexi.py --island
Restart=on-failure
RestartSec=3s
Environment=PYTHONUNBUFFERED=1
Environment=DISPLAY=:0
Environment=GDK_BACKEND=x11,wayland

[Install]
WantedBy=graphical-session.target
SERVICE_EOF

systemctl --user daemon-reload
systemctl --user enable lexi-island.service
systemctl --user restart lexi-island.service || true

# 6. Success Output
echo -e "\n${GREEN}${BOLD}✓ Lexi Copilot installed and running!${RESET}\n"
echo -e "  🏝️  ${BOLD}Dynamic Island${RESET}: Active at the top of your display"
echo -e "  ⚡  ${BOLD}Lexi Beam (AirDrop)${RESET}: Open http://localhost:7890 on your browser or mobile"
echo -e "  💬  ${BOLD}Launch Copilot Window${RESET}: Press ${CYAN}Super + C${RESET} or run ${CYAN}lexi${RESET}"
echo -e "  ⌨️   ${BOLD}Interactive Snip${RESET}: Press ${CYAN}Super + Shift + C${RESET}\n"
echo -e "⭐ ${YELLOW}If you enjoy Lexi, please star the project on GitHub: https://github.com/shakeel-777/lexi-copilot${RESET}\n"
