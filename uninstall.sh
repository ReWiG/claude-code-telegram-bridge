#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${CCTG_INSTALL_DIR:-$HOME/.cctg}"
SERVICE_FILE="$HOME/.config/systemd/user/cctg.service"

echo "╔════════════════════════════════════════╗"
echo "║     cctg — Деинсталляция               ║"
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

# 2. Remove hook from settings.json
SETTINGS_FILE="$HOME/.claude/settings.json"
if [ -f "$SETTINGS_FILE" ]; then
    echo "→ Удаление хуков cctg из $SETTINGS_FILE ..."
    python3 -c "
import json

def strip_cctg(entry):
    '''Remove cctg hooks from an entry. Returns None if entry should be removed entirely.'''
    # New format: entry has hooks array with sub-hooks
    if 'hooks' in entry:
        new_hooks = [h for h in entry['hooks']
                     if 'cctg/hooks' not in h.get('command', '')]
        if new_hooks:
            entry['hooks'] = new_hooks
            return entry
        return None
    # Old format: entry itself is a hook command
    if 'cctg/hooks' in entry.get('command', ''):
        return None
    return entry

with open('$SETTINGS_FILE') as f:
    settings = json.load(f)
hooks = settings.get('hooks', {})
for key in list(hooks.keys()):
    cleaned = []
    for entry in hooks[key]:
        result = strip_cctg(entry)
        if result is not None:
            cleaned.append(result)
    if cleaned:
        hooks[key] = cleaned
    else:
        del hooks[key]
if hooks:
    settings['hooks'] = hooks
else:
    settings.pop('hooks', None)
with open('$SETTINGS_FILE', 'w') as f:
    json.dump(settings, f, indent=2, ensure_ascii=False)
"
    echo "✓ Хук удалён из settings.json"
fi

# 3. Remove systemd service file
if [ -f "$SERVICE_FILE" ]; then
    rm "$SERVICE_FILE"
    systemctl --user daemon-reload 2>/dev/null || true
    echo "✓ Systemd-сервис удалён"
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
echo "║         Деинсталляция завершена        ║"
echo "╚════════════════════════════════════════╝"
