# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Проект

Мониторинг Claude Code с отправкой уведомлений в Telegram. Скрипт `monitor-claude.sh`:
- Отслеживает лог-файл `/tmp/claude-current.log`
- Отправляет Telegram-сообщения через SOCKS5-прокси при появлении запросов подтверждения
- Позволяет отвечать на запросы Claude Code прямо из Telegram

## Архитектура

- **monitor-claude.sh** — основной скрипт мониторинга
- Использует Python для парсинга JSON ответов Telegram API
- Состояние хранится в файлах `/tmp/cc-*`

## Запуск

```bash
./monitor-claude.sh
```

## Systemd-сервис

Для автозапуска мониторинга используется `cc-monitor.service`:

```bash
systemctl --user enable --now cc-monitor.service   # включить и запустить
systemctl --user status cc-monitor.service          # статус
systemctl --user stop cc-monitor.service             # остановить
systemctl --user restart cc-monitor.service          # перезапустить
```

**Текущий статус:** `disabled`, `inactive (dead)`

## Файлы состояния

| Файл | Назначение |
|------|------------|
| `/tmp/cc-monitor-watch` | Флаг активного мониторинга |
| `/tmp/cc-monitor-offset` | Offset для Telegram API polling |
| `/tmp/cc-monitor-logpos` | Позиция в лог-файле |
| `/tmp/cc-monitor-seen` | Кэш последнего состояния |

## Команды Telegram

- `🎯 Watch` — включить слежение
- `⏸ Stop` — выключить слежение
- `📋 Status` — показать статус
- `❓ Help` — показать help

## Переменные конфигурации

| Переменная | Описание |
|------------|----------|
| `TG_TOKEN` | Telegram bot token |
| `CHAT_ID` | Chat ID получателя |
| `PROXY` | SOCKS5 прокси |
| `LOG_FILE` | Лог-файл Claude Code |
| `INTERVAL` | Интервал проверки (сек) |

## Запуск Claude Code с логированием

| Команда | Описание |
|---------|----------|
| `clan` | Claude Code с профилем `lanit` + лог в `/tmp/claude-lanit-HHMM.log` |
| `cdeep` | Claude Code с профилем `deepseek` + лог в `/tmp/claude-deepseek-HHMM.log` |

Обе команды:
- Создают timestamped лог-файл
- Обновляют симлинк `/tmp/claude-current.log` на свежий лог
- Используют `script` для терминального логирования

**Содержимое `clan`:**
```bash
#!/usr/bin/env bash
ts=$(date '+%H%M')
logfile="/tmp/claude-lanit-${ts}.log"
ln -sf "$logfile" /tmp/claude-current.log 2>/dev/null
echo "→ Лог: $logfile"
exec script -q "$logfile" -c "ccs lanit"
```

**Содержимое `cdeep`:**
```bash
#!/usr/bin/env bash
ts=$(date '+%H%M')
logfile="/tmp/claude-deepseek-${ts}.log"
ln -sf "$logfile" /tmp/claude-current.log 2>/dev/null
echo "→ Лог: $logfile"
exec script -q "$logfile" -c "ccs deepseek"
```

## Примечания

- Скрипт сам не удаляет логи — управление через `clan`/`cdeep`
- Для перенаправления ответов в TTY используется `/dev/$tty`