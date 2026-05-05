#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${CCTG_INSTALL_DIR:-$HOME/.cctg}"
SETTINGS_FILE="$HOME/.claude/settings.json"
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

# 3. Remove cctg hooks from settings.json
if [ -f "$SETTINGS_FILE" ]; then
    echo "→ Удаление хуков cctg из $SETTINGS_FILE ..."
    python3 -c "
import json

def has_cctg(hook_entry):
    '''Check if a hook entry (old or new format) references cctg.'''
    # New format: {'matcher': '', 'hooks': [{'type': 'command', 'command': '...'}]}
    for sub in hook_entry.get('hooks', []):
        cmd = sub.get('command', '')
        if 'cctg/hooks' in cmd:
            return True
    # Old format: {'type': 'command', 'command': '...'}
    cmd = hook_entry.get('command', '')
    return 'cctg/hooks' in cmd

with open('$SETTINGS_FILE') as f:
    settings = json.load(f)
hooks = settings.get('hooks', {})
for key in ('SessionStart', 'Notification', 'Stop'):
    if key in hooks:
        hooks[key] = [h for h in hooks[key] if not has_cctg(h)]
        if not hooks[key]:
            del hooks[key]
if not hooks:
    settings.pop('hooks', None)
with open('$SETTINGS_FILE', 'w') as f:
    json.dump(settings, f, indent=2)
    f.write('\n')
"
    echo "✓ Хуки cctg удалены из $SETTINGS_FILE"
fi

# 4. Remove install directory
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
