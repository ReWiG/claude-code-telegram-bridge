#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${CCTG_INSTALL_DIR:-$HOME/.cctg}"
SERVICE_FILE="$HOME/.config/systemd/user/cctg.service"

echo "╔════════════════════════════════════════╗"
echo "║     cctg — Деинсталляция                ║"
echo "╚════════════════════════════════════════╝"
echo ""

# 1. Stop and disable systemd service
if systemctl --user is-active cctg &>/dev/null; then
    systemctl --user stop cctg
    echo "✓ Демон остановлен"
fi
if systemctl --user is-enabled cctg &>/dev/null; then
    systemctl --user disable cctg
    echo "✓ Автозапуск отключён"
fi

# 2. Remove systemd service file
if [ -f "$SERVICE_FILE" ]; then
    rm "$SERVICE_FILE"
    systemctl --user daemon-reload 2>/dev/null || true
    echo "✓ Systemd-сервис удалён"
fi

# 3. Remove install directory
if [ -d "$INSTALL_DIR" ]; then
    read -r -p "▸ Удалить каталог $INSTALL_DIR? [Y/n]: " REMOVE
    if [ "$REMOVE" != "n" ] && [ "$REMOVE" != "N" ]; then
        rm -rf "$INSTALL_DIR"
        echo "✓ $INSTALL_DIR удалён"
    else
        echo "→ $INSTALL_DIR сохранён"
    fi
fi

echo ""
echo "╔════════════════════════════════════════╗"
echo "║         Деинсталляция завершена          ║"
echo "╚════════════════════════════════════════╝"
