#!/bin/bash
# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  forgeIso32.sh – ISO live 32-bit e-Broyeur                                 ║
# ║                                                                             ║
# ║  Mode unique : Live OpenBox kiosque  (code/)                               ║
# ║  Architecture : i386 / Debian bullseye                                     ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

set -e

# ── Variables ──────────────────────────────────────────────────────────────────
ISO_NAME="$(pwd)/e-Broyeur-v7.2-32bits.iso"
WORK_DIR="$(pwd)/debian-live-build"
CODE_DIR="$(pwd)/../../code"

echo "=== Installation des dépendances ==="
sudo apt update
sudo apt install -y live-build python3 syslinux isolinux

echo "=== Mise en place du workspace live-build ==="
rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR"
cd "$WORK_DIR"

sudo lb clean --purge || true

echo "=== Configuration live-build (Debian bullseye i386) ==="
# NOTE : --debian-installer=none évite le bug live-build i386/bullseye :
#   "flAbsPath on localArchive/aptdir/.../dpkg/status failed (realpath: ...)"
lb config \
  --distribution=bullseye \
  --architectures=i386 \
  --linux-packages=linux-image \
  --linux-flavours=686 \
  --debian-installer=none \
  --bootappend-live="boot=live components config hostname=secure-eraser username=user locales=fr_FR.UTF-8 keyboard-layouts=fr" \
  --bootloaders="syslinux" \
  --binary-images=iso-hybrid

# ── Dépôts ─────────────────────────────────────────────────────────────────────
mkdir -p config/archives
cat << 'EOF' > config/archives/debian.list.chroot
deb http://deb.debian.org/debian bullseye main contrib non-free
deb-src http://deb.debian.org/debian bullseye main contrib non-free
deb http://security.debian.org/debian-security bullseye-security main contrib non-free
deb-src http://security.debian.org/debian-security bullseye-security main contrib non-free
EOF

# ── Paquets ────────────────────────────────────────────────────────────────────
echo "=== Déclaration des paquets ==="
mkdir -p config/package-lists/
cat << 'EOF' > config/package-lists/custom.list.chroot
coreutils
parted
ntfs-3g
python3
python3-tk
dosfstools
firmware-linux-free
firmware-linux-nonfree
squashfs-tools
xorg
xserver-xorg-video-all
xserver-xorg-video-intel
xserver-xorg-video-ati
xserver-xorg-video-nouveau
xserver-xorg-video-vesa
xserver-xorg-video-fbdev
xserver-xorg-input-all
openbox
lightdm
xterm
network-manager
sudo
evince
live-boot
live-config
live-tools
console-setup
keyboard-configuration
systemd
pciutils
usbutils
acpi
EOF

# ── Locale française AZERTY ────────────────────────────────────────────────────
echo "=== Configuration AZERTY fr_FR ==="
mkdir -p config/includes.chroot/etc/default/

cat << 'EOF' > config/includes.chroot/etc/default/locale
LANG=fr_FR.UTF-8
LC_ALL=fr_FR.UTF-8
EOF

cat << 'EOF' > config/includes.chroot/etc/default/keyboard
XKBMODEL="pc105"
XKBLAYOUT="fr"
XKBVARIANT="azerty"
XKBOPTIONS=""
EOF

cat << 'EOF' > config/includes.chroot/etc/default/console-setup
ACTIVE_CONSOLES="/dev/tty[1-6]"
CHARMAP="UTF-8"
CODESET="Lat15"
XKBLAYOUT="fr"
XKBVARIANT="azerty"
EOF

# ── Anti-veille ────────────────────────────────────────────────────────────────
echo "=== Désactivation de la mise en veille ==="
mkdir -p config/includes.chroot/etc/systemd/logind.conf.d/
cat << 'EOF' > config/includes.chroot/etc/systemd/logind.conf.d/no-suspend.conf
[Login]
HandleSuspendKey=ignore
HandleHibernateKey=ignore
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
IdleAction=ignore
EOF

mkdir -p config/includes.chroot/etc/systemd/sleep.conf.d/
cat << 'EOF' > config/includes.chroot/etc/systemd/sleep.conf.d/no-sleep.conf
[Sleep]
AllowSuspend=no
AllowHibernation=no
AllowSuspendThenHibernate=no
AllowHybridSleep=no
EOF

for target in sleep suspend hibernate hybrid-sleep; do
  mkdir -p "config/includes.chroot/etc/systemd/system/${target}.target.d/"
  cat << EOF > "config/includes.chroot/etc/systemd/system/${target}.target.d/override.conf"
[Unit]
ConditionPathExists=/dev/null
EOF
done

# ── Anti-écran noir ────────────────────────────────────────────────────────────
echo "=== Désactivation du blanking écran ==="
mkdir -p config/includes.chroot/etc/X11/xorg.conf.d/
cat << 'EOF' > config/includes.chroot/etc/X11/xorg.conf.d/10-monitor.conf
Section "ServerFlags"
  Option "BlankTime" "0"
  Option "StandbyTime" "0"
  Option "SuspendTime" "0"
  Option "OffTime" "0"
EndSection
Section "Monitor"
  Identifier "LVDS0"
  Option "DPMS" "false"
EndSection
EOF

# ── Code de l'application ──────────────────────────────────────────────────────
echo "=== Copie du code applicatif ==="
mkdir -p config/includes.chroot/usr/local/bin/
cp -r "${CODE_DIR}"/* config/includes.chroot/usr/local/bin/ 2>/dev/null || true
chmod +x config/includes.chroot/usr/local/bin/*.py 2>/dev/null || true

cat << 'WRAPPER' > config/includes.chroot/usr/local/bin/broyeur
#!/bin/bash
exec python3 /usr/local/bin/main.py "$@"
WRAPPER
chmod +x config/includes.chroot/usr/local/bin/broyeur

# ── Sudo sans mot de passe ─────────────────────────────────────────────────────
mkdir -p config/includes.chroot/etc/sudoers.d/
echo "user ALL=(ALL) NOPASSWD: ALL" > config/includes.chroot/etc/sudoers.d/passwordless
chmod 0440 config/includes.chroot/etc/sudoers.d/passwordless

# ── Règle udev USB ─────────────────────────────────────────────────────────────
mkdir -p config/includes.chroot/etc/udev/rules.d/
cat << 'EOF' > config/includes.chroot/etc/udev/rules.d/usb-flash.rules
ATTR{queue/rotational}=="0", GOTO="skip"
ATTRS{queue_type}!="none", GOTO="skip"
ATTR{removable}=="1", SUBSYSTEM=="block", SUBSYSTEMS=="usb", ACTION=="add", ATTR{queue/rotational}="0"
LABEL="skip"
EOF

# ════════════════════════════════════════════════════════════════════════════════
# SESSION OPENBOX KIOSQUE (live uniquement)
# ════════════════════════════════════════════════════════════════════════════════
echo "=== Configuration OpenBox kiosque ==="

mkdir -p config/includes.chroot/etc/xdg/openbox/
cat << 'EOF' > config/includes.chroot/etc/xdg/openbox/rc.xml
<?xml version="1.0" encoding="UTF-8"?>
<openbox_config xmlns="http://openbox.org/3.4/rc"
                xmlns:xi="http://www.w3.org/2001/XInclude">
  <applications>
    <!-- Pas de fullscreen/maximized global : la fenetre principale gere
         elle-meme son plein ecran ; les fenetres secondaires (pop-ups)
         conservent ainsi leur taille naturelle. -->
  </applications>
</openbox_config>
EOF

# Script de session : live uniquement, pas de branche installer
cat << 'EOF' > config/includes.chroot/usr/local/bin/broyeur-session.sh
#!/bin/bash
xset s off -dpms 2>/dev/null || true
xset s noblank   2>/dev/null || true
openbox &
WM_PID=$!
sleep 1
sudo /usr/local/bin/broyeur
kill "$WM_PID" 2>/dev/null || true
EOF
chmod +x config/includes.chroot/usr/local/bin/broyeur-session.sh

mkdir -p config/includes.chroot/usr/share/xsessions/
cat << 'EOF' > config/includes.chroot/usr/share/xsessions/e-Broyeur-kiosk.desktop
[Desktop Entry]
Name=e-Broyeur (Kiosk)
Comment=Borne de blanchiment plein écran
Exec=/usr/local/bin/broyeur-session.sh
Type=Application
EOF

mkdir -p config/includes.chroot/etc/lightdm/lightdm.conf.d/
cat << 'EOF' > config/includes.chroot/etc/lightdm/lightdm.conf.d/50-autologin.conf
[Seat:*]
autologin-user=user
autologin-session=e-Broyeur-kiosk
autologin-user-timeout=0
EOF

mkdir -p config/includes.chroot/etc/skel/
cat << 'EOF' > config/includes.chroot/etc/skel/.dmrc
[Desktop]
Session=e-Broyeur-kiosk
EOF

cat << 'EOF' > config/includes.chroot/etc/skel/.bashrc
if [ -f /etc/bashrc ]; then . /etc/bashrc; fi
echo "Borne de blanchiment e-Broyeur (32-bit)"
echo "Type 'sudo broyeur' to use the program"
EOF

# ════════════════════════════════════════════════════════════════════════════════
# MENU DE DÉMARRAGE – injection AVANT lb build via config/includes.binary/
# ════════════════════════════════════════════════════════════════════════════════
echo "=== Injection du menu de démarrage (avant lb build) ==="
mkdir -p config/includes.binary/isolinux/

cat > config/includes.binary/isolinux/isolinux.cfg << 'MENU'
UI vesamenu.c32
DEFAULT live
TIMEOUT 100
PROMPT 0

MENU TITLE e-Broyeur v7.2 (32-bit) - Mode Live

LABEL live
  MENU LABEL > Demarrer (mode live)
  MENU DEFAULT
  KERNEL /live/vmlinuz
  APPEND initrd=/live/initrd.img boot=live components config hostname=secure-eraser username=user locales=fr_FR.UTF-8 keyboard-layouts=fr

LABEL live-safe
  MENU LABEL > Demarrer - Sans echec (nomodeset)
  KERNEL /live/vmlinuz
  APPEND initrd=/live/initrd.img boot=live components config hostname=secure-eraser username=user locales=fr_FR.UTF-8 keyboard-layouts=fr nomodeset
MENU

echo "  → isolinux.cfg injecté dans config/includes.binary/isolinux/"

# ════════════════════════════════════════════════════════════════════════════════
# BUILD ISO
# ════════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== Construction de l'ISO (plusieurs minutes)… ==="
sudo lb build

# ── Renommage final ────────────────────────────────────────────────────────────
if   [ -f "live-image-i386.hybrid.iso" ]; then mv "live-image-i386.hybrid.iso" "$ISO_NAME"
elif [ -f "live-image-i386.iso" ];        then mv "live-image-i386.iso"        "$ISO_NAME"
else echo "ERREUR : ISO introuvable après lb build"; exit 1
fi

sudo lb clean
echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║  ISO créée : $ISO_NAME"
echo "║"
echo "║  Menu de démarrage :"
echo "║    1. Live        → OpenBox kiosque  (code/)"
echo "║    2. Live Safe   → Live + nomodeset"
echo "╚══════════════════════════════════════════════════════════╝"