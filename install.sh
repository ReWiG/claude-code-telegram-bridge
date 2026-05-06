#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${CCTG_INSTALL_DIR:-$HOME/.cctg}"
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "╔════════════════════════════════════════╗"
echo "║     cctg — Claude Code Telegram Bridge ║"
echo "║              Установка                 ║"
echo "╚════════════════════════════════════════╝"
echo ""

read -r -p "▸ Каталог установки [$INSTALL_DIR]: " input
INSTALL_DIR="${input:-$INSTALL_DIR}"
INSTALL_DIR="${INSTALL_DIR/#~/$HOME}"
echo ""

read -r -p "▸ Telegram bot token: " TG_TOKEN
read -r -p "▸ Chat ID: " CHAT_ID
read -r -p "▸ SOCKS5 прокси (например socks5://127.0.0.1:10808, оставь пустым если не нужен): " PROXY
if [ -n "$PROXY" ] && [[ ! "$PROXY" =~ ^socks5?:// ]]; then
    echo "⚠ Предупреждение: прокси должен начинаться с socks5:// или socks://"
fi
echo ""

# 1. Copy files
echo "→ Копирование файлов в $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"/{data,hooks,bin,cctg}
cp -r "$REPO_DIR"/cctg/*.py "$INSTALL_DIR/cctg/" 2>/dev/null || true
cp -r "$REPO_DIR"/hooks/*.py "$INSTALL_DIR/hooks/" 2>/dev/null || true
chmod +x "$INSTALL_DIR/hooks/"*.py 2>/dev/null || true
cp "$REPO_DIR"/pyproject.toml "$INSTALL_DIR/pyproject.toml" 2>/dev/null || true
echo "✓ Файлы скопированы в $INSTALL_DIR"

# Register hooks in ~/.claude/settings.json
SETTINGS_FILE="$HOME/.claude/settings.json"
SESSION_CMD="python3 $INSTALL_DIR/hooks/session.py"
NOTIFY_CMD="python3 $INSTALL_DIR/hooks/notify.py"

if [ -f "$SETTINGS_FILE" ]; then
    cp "$SETTINGS_FILE" "${SETTINGS_FILE}.bak"
    echo "✓ Бекап сохранён в ${SETTINGS_FILE}.bak"
    python3 -c "
import json

cmds = {'SessionStart': '$SESSION_CMD', 'Notification': '$NOTIFY_CMD'}
f = '$SETTINGS_FILE'
with open(f) as fh:
    s = json.load(fh)

hooks = s.setdefault('hooks', {})

for hook_type, hook_cmd in cmds.items():
    entries = hooks.setdefault(hook_type, [])
    # Merge all entries with empty matcher into one
    merged_hooks = []
    remaining = []
    for e in entries:
        if e.get('matcher') == '':
            merged_hooks.extend(e.get('hooks', []))
        else:
            remaining.append(e)
    # Deduplicate
    seen = set()
    unique = []
    for h in merged_hooks:
        cmd = h.get('command', '')
        if cmd not in seen:
            seen.add(cmd)
            unique.append(h)
    if hook_cmd not in seen:
        unique.append({'type': 'command', 'command': hook_cmd})
    entries.clear()
    entries.append({'matcher': '', 'hooks': unique})
    entries.extend(remaining)

with open(f, 'w') as fh:
    json.dump(s, fh, indent=2, ensure_ascii=False)
"
    echo "✓ Хук SessionStart зарегистрирован в settings.json"
else
    echo "⚠ $SETTINGS_FILE не найден — хук не зарегистрирован"
fi

# 2. Create venv
echo "→ Создание виртуального окружения..."
VENV_DIR="$INSTALL_DIR/.venv"
if command -v uv &>/dev/null; then
    uv venv "$VENV_DIR" 2>/dev/null || uv venv --clear "$VENV_DIR"
    PIP_CMD="uv pip install --python $VENV_DIR/bin/python"
elif command -v python3 &>/dev/null; then
    python3 -m venv "$VENV_DIR" --clear 2>/dev/null || {
        echo "⚠ Не удалось создать venv. Установи python3-venv или uv."
        exit 1
    }
    PIP_CMD="$VENV_DIR/bin/pip install"
else
    echo "⚠ Python3 не найден."
    exit 1
fi
echo "✓ venv создан в $VENV_DIR"

# 3. Install dependencies
echo "→ Установка зависимостей..."
$PIP_CMD python-telegram-bot aiosqlite aiofiles watchdog tomli 2>&1 | tail -1
if [ -n "$PROXY" ]; then
    $PIP_CMD "httpx[socks]" 2>&1 | tail -1
fi
$PIP_CMD -e "$INSTALL_DIR" 2>&1 | tail -1
echo "✓ Зависимости установлены"

# 4. Write bin/cctg wrapper
cat > "$INSTALL_DIR/bin/cctg" << 'WRAPEOF'
#!/usr/bin/env bash
exec "$HOME/.cctg/.venv/bin/python" -m cctg "$@"
WRAPEOF
chmod +x "$INSTALL_DIR/bin/cctg"
echo "✓ bin/cctg создан"

# 5. Write config
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

# 6. Create systemd user service
mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/cctg.service" << UNITEOF
[Unit]
Description=cctg — Claude Code Telegram Bridge
After=network-online.target

[Service]
Type=simple
ExecStart=$VENV_DIR/bin/python -m cctg daemon
ExecReload=/bin/kill -HUP \$MAINPID
TimeoutStopSec=10
Restart=on-failure
RestartSec=5
Environment=CCTG_CONFIG=$INSTALL_DIR/config.toml

[Install]
WantedBy=default.target
UNITEOF

systemctl --user daemon-reload 2>/dev/null || true
echo "✓ Systemd-сервис создан"

# 7. Start
read -r -p "Запустить демон сейчас? [Y/n]: " START
if [ "$START" != "n" ] && [ "$START" != "N" ]; then
    systemctl --user enable --now cctg 2>/dev/null && echo "✓ Демон запущен" || {
        echo "⚠ Не удалось запустить через systemd. Попробуй вручную:"
        echo "  $INSTALL_DIR/bin/cctg start"
    }
fi

echo ""
echo "╔════════════════════════════════════════╗"
echo "║          Установка завершена!          ║"
echo "║   Открой Telegram и отправь /help боту ║"
echo "╚════════════════════════════════════════╝"
