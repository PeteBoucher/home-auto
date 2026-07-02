#!/usr/bin/env bash
# Raspberry Pi setup script for home-auto
# Run as your normal user (not root); sudo is used internally where needed.
# Safe to re-run — all steps are idempotent.
set -euo pipefail

APP_USER="${SUDO_USER:-$(whoami)}"
APP_DIR="/opt/home-auto"
Z2M_DIR="/opt/zigbee2mqtt"
REPO_URL="https://github.com/PeteBoucher/home-auto.git"
BRANCH="main"

info()  { echo "[+] $*"; }
check() { echo "[✓] $*"; }

# ── System packages ────────────────────────────────────────────────────────────
info "Updating apt..."
sudo apt-get update -qq

info "Installing system dependencies..."
sudo apt-get install -y -qq \
    git curl mosquitto mosquitto-clients \
    python3-pip python3-venv python3-dev \
    libffi-dev libssl-dev

check "System packages installed"

# ── Mosquitto ─────────────────────────────────────────────────────────────────
info "Configuring Mosquitto..."
sudo cp "$(dirname "$0")/mosquitto.conf" /etc/mosquitto/conf.d/home-auto.conf
sudo systemctl enable mosquitto
sudo systemctl restart mosquitto
check "Mosquitto running"

# ── Node.js (LTS via NodeSource) ──────────────────────────────────────────────
if ! command -v node &>/dev/null || [[ "$(node -e 'process.exit(+process.version.slice(1).split(".")[0] < 20)')" ]]; then
    info "Installing Node.js 20 LTS..."
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt-get install -y -qq nodejs
fi
check "Node.js $(node --version) ready"

# ── pnpm ──────────────────────────────────────────────────────────────────────
if ! command -v pnpm &>/dev/null; then
    info "Installing pnpm..."
    sudo npm install -g pnpm
fi
check "pnpm $(pnpm --version) ready"

# ── Zigbee2MQTT ───────────────────────────────────────────────────────────────
if [[ ! -d "$Z2M_DIR" ]]; then
    info "Cloning Zigbee2MQTT..."
    sudo git clone --depth 1 https://github.com/Koenkk/zigbee2mqtt.git "$Z2M_DIR"
    sudo chown -R "$APP_USER":"$APP_USER" "$Z2M_DIR"
fi

info "Installing Zigbee2MQTT dependencies..."
(cd "$Z2M_DIR" && pnpm install --frozen-lockfile)

# Write Z2M config only if one doesn't already exist
if [[ ! -f "$Z2M_DIR/data/configuration.yaml" ]]; then
    info "Writing Zigbee2MQTT configuration..."
    mkdir -p "$Z2M_DIR/data"
    cp "$(dirname "$0")/z2m-configuration.yaml" "$Z2M_DIR/data/configuration.yaml"
fi
check "Zigbee2MQTT ready at $Z2M_DIR"

# Add user to dialout group for serial port access
sudo usermod -aG dialout "$APP_USER"

# ── Home-auto app ─────────────────────────────────────────────────────────────
if [[ ! -d "$APP_DIR/.git" ]]; then
    info "Cloning home-auto..."
    sudo git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
    sudo chown -R "$APP_USER":"$APP_USER" "$APP_DIR"
else
    info "Pulling latest home-auto..."
    git -C "$APP_DIR" pull --ff-only
fi

info "Setting up Python virtualenv..."
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -e "$APP_DIR"
check "Python environment ready"

# Placeholder credential files — fill these in before starting the app
for f in .env tinytuya.json devices.json; do
    if [[ ! -f "$APP_DIR/$f" ]]; then
        echo "# Copy your $f here" | sudo tee "$APP_DIR/$f" > /dev/null
        sudo chown "$APP_USER":"$APP_USER" "$APP_DIR/$f"
        echo "    [!] Created placeholder $APP_DIR/$f — replace with real content"
    fi
done

# ── Systemd services ──────────────────────────────────────────────────────────
info "Installing systemd services..."
sudo sed "s/{{USER}}/$APP_USER/g" "$(dirname "$0")/home-auto.service" \
    | sudo tee /etc/systemd/system/home-auto.service > /dev/null

sudo sed "s/{{USER}}/$APP_USER/g" "$(dirname "$0")/zigbee2mqtt.service" \
    | sudo tee /etc/systemd/system/zigbee2mqtt.service > /dev/null

sudo systemctl daemon-reload
sudo systemctl enable home-auto zigbee2mqtt
check "Services installed and enabled"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " Setup complete. Before starting:"
echo ""
echo "  1. Copy your credential files to $APP_DIR:"
echo "       .env  tinytuya.json  devices.json  home_auto.db"
echo ""
echo "  2. Check the serial port in:"
echo "       $Z2M_DIR/data/configuration.yaml"
echo "     (run: ls /dev/ttyUSB* /dev/ttyACM* to find your dongle)"
echo ""
echo "  3. Log out and back in (dialout group takes effect), then:"
echo "       sudo systemctl start zigbee2mqtt home-auto"
echo ""
echo "  4. Dashboard: http://$(hostname -I | awk '{print $1}'):8000"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
