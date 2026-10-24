#!/usr/bin/env bash
# ==============================================================================
# 🗑️ Lexi Copilot — Clean Uninstaller for Linux
# Completely stops and removes Lexi Copilot, background service, desktop launcher,
# cache, and application files from your system.
# ==============================================================================

set -e

BOLD="\033[1m"
GREEN="\033[32m"
CYAN="\033[36m"
YELLOW="\033[33m"
RED="\033[31m"
RESET="\033[0m"

echo -e "${RED}${BOLD}"
cat << 'EOF'
  _               _   _   _       _           _        _ _ 
 | |    _____  __(_) | | | |_ __ (_)_ __  ___| |_ __ _| | |
 | |   / _ \ \/ /| | | | | | '_ \| | '_ \/ __| __/ _` | | |
 | |__|  __/>  < | | | |_| | | | | | | | \__ \ || (_| | | |
 |_____\___/_/\_\|_|  \___/|_| |_|_|_| |_|___/\__\__,_|_|_|
EOF
echo -e "${RESET}${BOLD}Clean Uninstaller for Linux (GNOME / Wayland)${RESET}\n"

INSTALL_DIR="${HOME}/.local/share/mochi-copilot"
SERVICE_FILE="${HOME}/.config/systemd/user/lexi-island.service"
DESKTOP_FILE="${HOME}/.local/share/applications/lexi.desktop"
CONFIG_FILE="${HOME}/.lexi_config.json"
BIN_FILE="${HOME}/.local/bin/lexi"

# 1. Stop and Disable Systemd Background Service
echo -e "${CYAN}==>${RESET} Stopping and disabling background systemd service..."
if systemctl --user is-active --quiet lexi-island.service 2>/dev/null; then
    systemctl --user stop lexi-island.service || true
    echo -e "    ${GREEN}✓${RESET} Stopped lexi-island.service"
fi

if systemctl --user is-enabled --quiet lexi-island.service 2>/dev/null; then
    systemctl --user disable lexi-island.service || true
    echo -e "    ${GREEN}✓${RESET} Disabled lexi-island.service"
fi

if [ -f "${SERVICE_FILE}" ]; then
    rm -f "${SERVICE_FILE}"
    systemctl --user daemon-reload || true
    echo -e "    ${GREEN}✓${RESET} Removed ${SERVICE_FILE}"
fi

# 2. Terminate Any Running Lexi Processes
echo -e "${CYAN}==>${RESET} Terminating any active Lexi processes..."
pkill -f "python.*lexi\.py" 2>/dev/null || true
pkill -f "scrcpy.*Lexi" 2>/dev/null || true

# 3. Remove Desktop Launcher
if [ -f "${DESKTOP_FILE}" ]; then
    echo -e "${CYAN}==>${RESET} Removing desktop application launcher..."
    rm -f "${DESKTOP_FILE}"
    update-desktop-database "${HOME}/.local/share/applications" 2>/dev/null || true
    echo -e "    ${GREEN}✓${RESET} Removed ${DESKTOP_FILE}"
fi

if [ -f "${BIN_FILE}" ]; then
    rm -f "${BIN_FILE}"
    echo -e "    ${GREEN}✓${RESET} Removed ${BIN_FILE}"
fi

# 4. Remove Configuration File (Prompt or check argument)
PURGE=false
for arg in "$@"; do
    if [ "$arg" == "--purge" ] || [ "$arg" == "-y" ]; then
        PURGE=true
    fi
done

if [ -f "${CONFIG_FILE}" ]; then
    if [ "$PURGE" = true ]; then
        rm -f "${CONFIG_FILE}"
        echo -e "    ${GREEN}✓${RESET} Removed configuration file (${CONFIG_FILE})"
    else
        echo -e "${YELLOW}==>${RESET} Found API key & settings at ${CONFIG_FILE}"
        read -p "Do you want to delete your API keys and configuration? (y/N): " -n 1 -r || true
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            rm -f "${CONFIG_FILE}"
            echo -e "    ${GREEN}✓${RESET} Removed configuration file"
        else
            echo -e "    ${YELLOW}ℹ${RESET} Preserved ${CONFIG_FILE}"
        fi
    fi
fi

# 5. Remove Application Source & Runtime Directory
if [ -d "${INSTALL_DIR}" ] && [ "$(pwd)" != "${INSTALL_DIR}" ]; then
    echo -e "${CYAN}==>${RESET} Removing application files at ${INSTALL_DIR}..."
    rm -rf "${INSTALL_DIR}"
    echo -e "    ${GREEN}✓${RESET} Removed ${INSTALL_DIR}"
fi

echo -e "\n${GREEN}${BOLD}✓ Lexi Copilot has been completely uninstalled from your Linux system.${RESET}\n"
