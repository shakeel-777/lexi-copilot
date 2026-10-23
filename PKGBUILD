# Maintainer: Shakeel <https://github.com/shakeel-777>
pkgname=lexi-copilot-git
pkgver=1.0.0.r0
pkgrel=1
pkgdesc="Autonomous Dynamic Island AI Desktop Companion & AirDrop for Linux (GNOME / Wayland)"
arch=('any')
url="https://github.com/shakeel-777/lexi-copilot"
license=('MIT')
depends=(
    'gtk4'
    'libadwaita'
    'python'
    'python-gobject'
    'python-cairo'
    'python-pillow'
    'python-aiohttp'
    'python-qrcode'
    'scrcpy'
    'xdotool'
    'xdg-desktop-portal'
    'libnotify'
)
makedepends=('git')
provides=('lexi-copilot')
conflicts=('lexi-copilot')
source=("git+https://github.com/shakeel-777/lexi-copilot.git")
sha256sums=('SKIP')

pkgver() {
    cd "${srcdir}/lexi-copilot"
    printf "1.0.0.r%s.%s" "$(git rev-list --count HEAD)" "$(git rev-parse --short HEAD)"
}

package() {
    cd "${srcdir}/lexi-copilot"
    
    # Install application files
    install -d "${pkgdir}/usr/share/lexi-copilot"
    cp -r * "${pkgdir}/usr/share/lexi-copilot/"
    
    # Symlink launcher
    install -d "${pkgdir}/usr/bin"
    ln -sf /usr/share/lexi-copilot/lexi.py "${pkgdir}/usr/bin/lexi-copilot"
    ln -sf /usr/share/lexi-copilot/lexi.py "${pkgdir}/usr/bin/lexi"

    # Install desktop launcher
    install -d "${pkgdir}/usr/share/applications"
    cat > "${pkgdir}/usr/share/applications/lexi.desktop" << 'EOF'
[Desktop Entry]
Name=Lexi Copilot
Comment=Autonomous Dynamic Island AI Desktop Companion & Screen Copilot
Exec=/usr/bin/python3 /usr/share/lexi-copilot/lexi.py
Icon=/usr/share/lexi-copilot/drop_web/icon-512.png
Terminal=false
Type=Application
Categories=Utility;Office;AI;
Keywords=AI;Copilot;Assistant;Screen;DynamicIsland;
EOF

    # Install systemd user service
    install -d "${pkgdir}/usr/lib/systemd/user"
    cat > "${pkgdir}/usr/lib/systemd/user/lexi-island.service" << 'EOF'
[Unit]
Description=Lexi Dynamic Island (Permanent Desktop Notch)
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /usr/share/lexi-copilot/lexi.py --island
Restart=on-failure
RestartSec=3s
Environment=PYTHONUNBUFFERED=1
Environment=DISPLAY=:0
Environment=GDK_BACKEND=x11,wayland

[Install]
WantedBy=graphical-session.target
EOF
}
