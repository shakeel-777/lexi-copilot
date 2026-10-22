#!/usr/bin/env bash
# =============================================================================
# Lexi YouTube Bridge — User-Level Native Messaging Installer (Arch Linux)
# =============================================================================
# - Does NOT require root / sudo access.
# - Marks lexi_browser_host.py executable in the user's Lexi project directory.
# - Installs com.lexi.browser_bridge.json under current user's browser config
#   directories (~/.config/google-chrome/NativeMessagingHosts,
#   ~/.config/chromium/NativeMessagingHosts, and Brave if present).
# - Requires the 32-character Chrome Extension ID from chrome://extensions
#   after loading chrome_extension/ unpacked in Developer Mode.
# =============================================================================

set -euo pipefail

if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    echo "Error: Do not run this script as root/sudo. It installs strictly under your current user." >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXT_DIR="${SCRIPT_DIR}/chrome_extension"
HOST_SCRIPT="${SCRIPT_DIR}/lexi_browser_host.py"
TEMPLATE_FILE="${SCRIPT_DIR}/com.lexi.browser_bridge.json.template"
MANIFEST_NAME="com.lexi.browser_bridge.json"

if [[ ! -f "${HOST_SCRIPT}" ]]; then
    echo "Error: Native host script not found at ${HOST_SCRIPT}" >&2
    exit 1
fi

if [[ ! -f "${TEMPLATE_FILE}" ]]; then
    echo "Error: Manifest template not found at ${TEMPLATE_FILE}" >&2
    exit 1
fi

chmod +x "${HOST_SCRIPT}"

EXT_ID="${1:-}"

if [[ -z "${EXT_ID}" ]]; then
    cat <<EOF
===============================================================================
Step 1 — Load the Unpacked Extension in Chrome / Chromium (Developer Mode)
===============================================================================
1. Open Google Chrome or Chromium and navigate to:
     chrome://extensions
2. Enable the "Developer mode" toggle in the top-right corner.
3. Click "Load unpacked" and select this directory:
     ${EXT_DIR}
4. Copy the 32-character Extension ID shown on the "Lexi YouTube Bridge" card
   (for example: abcdefghijklmnopqrstuvwxyzabcdef).

EOF
    if [[ -t 0 ]]; then
        read -r -p "Enter your 32-character Chrome Extension ID (or press Enter to exit and re-run later): " EXT_ID
        EXT_ID="$(echo "${EXT_ID}" | tr -d '[:space:]')"
    fi
fi

if [[ -z "${EXT_ID}" ]]; then
    cat <<EOF

No Extension ID provided yet. Once you have loaded the unpacked extension from:
  ${EXT_DIR}
re-run this script with your Extension ID:
  ./install_chrome_bridge.sh <YOUR_32_CHAR_EXTENSION_ID>
EOF
    exit 1
fi

if [[ ! "${EXT_ID}" =~ ^[a-p]{32}$ ]]; then
    echo "Error: '${EXT_ID}' is not a valid 32-character Chrome Extension ID (expected 32 lowercase letters a-p)." >&2
    exit 1
fi

CONFIG_HOME="${XDG_CONFIG_HOME:-${HOME}/.config}"
TARGET_DIRS=(
    "${CONFIG_HOME}/google-chrome/NativeMessagingHosts"
    "${CONFIG_HOME}/chromium/NativeMessagingHosts"
)

if [[ -d "${CONFIG_HOME}/BraveSoftware/Brave-Browser" ]]; then
    TARGET_DIRS+=("${CONFIG_HOME}/BraveSoftware/Brave-Browser/NativeMessagingHosts")
fi

RENDERED_JSON="$(sed \
    -e "s|__LEXI_HOST_PATH__|${HOST_SCRIPT}|g" \
    -e "s|__EXTENSION_ID__|${EXT_ID}|g" \
    "${TEMPLATE_FILE}")"

echo "Installing Native Messaging manifest (${MANIFEST_NAME})..."
for dir in "${TARGET_DIRS[@]}"; do
    mkdir -p "${dir}"
    printf "%s\n" "${RENDERED_JSON}" > "${dir}/${MANIFEST_NAME}"
    chmod 600 "${dir}/${MANIFEST_NAME}"
    echo "  -> Installed: ${dir}/${MANIFEST_NAME}"
done

cat <<EOF

===============================================================================
Installation Complete (User-Level, No Root Required)
===============================================================================
- Native Host Script : ${HOST_SCRIPT}
- Extension Source   : ${EXT_DIR}
- Allowed Origin     : chrome-extension://${EXT_ID}/

Next steps:
1. In chrome://extensions, click the Reload icon on "Lexi YouTube Bridge".
2. Open or refresh https://www.youtube.com in Chrome so the extension connects
   to the native host.
3. Restart Lexi if needed:
     systemctl --user restart lexi-island.service
EOF
