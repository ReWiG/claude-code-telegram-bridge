#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${CCTG_INSTALL_DIR:-$HOME/.cctg}"
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "╔════════════════════════════════════════╗"
echo "║     cctg — Claude Code Telegram Bridge ║"
echo "║              Установка                  ║"
echo "╚════════════════════════════════════════╝"
echo ""

read -r -p "▸ Каталог установки [$INSTALL_DIR]: " input
INSTALL_DIR="${input:-$INSTALL_DIR}"
INSTALL_DIR="${INSTALL_DIR/#~/$HOME}"
echo ""

read -r -p "▸ Telegram bot token: " TG_TOKEN
read -r -p "▸ Chat ID: " CHAT_ID
read -r -p "▸ SOCKS5 прокси (оставь пустым если не нужен): " PROXY
echo ""

echo "→ Копирование файлов в $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"/{data,hooks,bin,cctg}
cp -r "$REPO_DIR"/cctg/*.py "$INSTALL_DIR/cctg/" 2>/dev/null || true
cp -r "$REPO_DIR"/hooks/*.py "$INSTALL_DIR/hooks/" 2>/dev/null || true
cp "$REPO_DIR"/bin/cctg "$INSTALL_DIR/bin/cctg" 2>/dev/null || true
cp "$REPO_DIR"/pyproject.toml "$INSTALL_DIR/pyproject.toml" 2>/dev/null || true
chmod +x "$INSTALL_DIR/bin/cctg" "$INSTALL_DIR/hooks/"*.py
echo "✓ Файлы скопированы в $INSTALL_DIR"

echo "→ Установка зависимостей..."
pip install -e "$INSTALL_DIR" 2>/dev/null || pip install --user -e "$INSTALL_DIR" 2>/dev/null || {
    echo "⚠ Не удалось установить через pip. Установи зависимости вручную:"
    echo "  pip install python-telegram-bot aiosqlite aiofiles watchdog tomli"
}
echo "✓ Зависимости установлены"

cat > "$INSTALL_DIR/config.toml" << TOMLEOF
[telegram]
token = "$TG_TOKEN"
chat_id = "$CHAT_ID"
proxy = "${PROXY:-}"

[paths]
install_dir = "$INSTALL_DIR"
transcript_base = "~/.claude/projects"

[timing]
session_cleanup_seconds = 30
TOMLEOF
echo "✓ $INSTALL_DIR/config.toml записан"

SETTINGS_FILE="$HOME/.claude/settings.json"
HOOKS_JSON=$(cat << 'HOOKSEOF'
{
  "hooks": {
    "SessionStart": [
      {"type": "command", "command": "HOOKS_DIR/session.py"}
    ],
    "Notification": [
      {"type": "command", "command": "HOOKS_DIR/notify.py"}
    ],
    "Stop": [
      {"type": "command", "command": "HOOKS_DIR/stop.py"}
    ]
  }
}
HOOKSEOF
)
HOOKS_JSON="${HOOKS_JSON//HOOKS_DIR/$INSTALL_DIR/hooks}"

if [ -f "$SETTINGS_FILE" ]; then
    # Backup before modifying
    cp "$SETTINGS_FILE" "${SETTINGS_FILE}.bak"
    echo "✓ Бекап сохранён в ${SETTINGS_FILE}.bak"

    python3 -c "
import json
with open('$SETTINGS_FILE') as f:
    settings = json.load(f)
new_hooks = json.loads('''$HOOKS_JSON''')['hooks']
hooks = settings.setdefault('hooks', {})
for key, value in new_hooks.items():
    hooks.setdefault(key, []).extend(value)
with open('$SETTINGS_FILE', 'w') as f:
    json.dump(settings, f, indent=2)
    f.write('\n')
"
else
    echo "$HOOKS_JSON" > "$SETTINGS_FILE"
fi
echo "✓ Хуки прописаны в $SETTINGS_FILE"

mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/cctg.service" << UNITEOF
[Unit]
Description=cctg — Claude Code Telegram Bridge
After=network-online.target

[Service]
Type=simple
ExecStart=$INSTALL_DIR/bin/cctg daemon
ExecReload=/bin/kill -HUP \$MAINPID
Restart=on-failure
RestartSec=5
Environment=CCTG_CONFIG=$INSTALL_DIR/config.toml

[Install]
WantedBy=default.target
UNITEOF

systemctl --user daemon-reload 2>/dev/null || true
echo "✓ Systemd-сервис создан"

read -r -p "Запустить демон сейчас? [Y/n]: " START
if [ "$START" != "n" ] && [ "$START" != "N" ]; then
    systemctl --user enable --now cctg 2>/dev/null && echo "✓ Демон запущен" || {
        echo "⚠ Не удалось запустить через systemd. Попробуй вручную:"
        echo "  $INSTALL_DIR/bin/cctg start"
    }
fi

echo ""
echo "╔════════════════════════════════════════╗"
echo "║          Установка завершена!           ║"
echo "║   Открой Telegram и отправь /help боту  ║"
echo "╚════════════════════════════════════════╝"
