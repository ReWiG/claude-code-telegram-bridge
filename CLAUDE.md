# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Проект

cctg — мост между Claude Code и Telegram на PTY-прокси. Запускает Claude Code внутри псевдо-терминала, пересылает вывод модели в Telegram, принимает команды и ответы на запросы разрешений из Telegram.

Работает с [Claude Code Switch](https://github.com/kaitranntt/ccs).

## Архитектура

Три процесса:
1. **Мост** (`cli.py cmd_launch`) — создаёт PTY, spawn'ит `ccs`, читает JSONL-транскрипты, мультиплексирует ввод/вывод между терминалом, сокетом демона и PTY
2. **Демон** (`daemon.py`) — asyncio-сервер на Unix-сокете, обрабатывает подключения мостов, работает с Telegram API через `python-telegram-bot`
3. **Хуки** (`hooks/`) — Claude Code запускает их на события SessionStart и Notification, пишут в файлы для моста

Данные модели читаются из JSONL-транскриптов (`~/.claude/projects/<project>/<session>.jsonl`), а не из вывода PTY. PTY используется только для ввода (отправка текста и ответов на разрешения).

Связь мост↔демон — line-based протокол через Unix-сокет: `REGISTER`, `OUTPUT`, `FLUSH`, `NOTIFY`, `UNREGISTER`, `INPUT`, `RESP`.

## Ключевые файлы

| Файл | Роль |
|------|------|
| `cctg/cli.py` | CLI (`start`, `stop`, `status`, `daemon`, `launch`, `install`) |
| `cctg/daemon.py` | Демон: Unix-сокет сервер, приём/отправка через TelegramHandler |
| `cctg/pty_bridge.py` | `PTYBridge`: создание PTY, fork+exec ccs, I/O |
| `cctg/transcript_watcher.py` | `TranscriptWatcher`: чтение JSONL, извлечение текста и tool_use |
| `cctg/telegram_handler.py` | `TelegramHandler`: команды бота, кнопки разрешений, callback'и |
| `cctg/session_manager.py` | `SessionManager`: attach/detach, хранение состояния в БД |
| `cctg/db.py` | SQLite через aiosqlite: сессии, состояние, live-сообщения |
| `cctg/tty_router.py` | `TTYRouter`: маппинг y/n/a, поиск TTY в /proc |
| `hooks/session.py` | Хук SessionStart — пишет session_id в events-файл |
| `hooks/notify.py` | Хук Notification — пишет permission_prompt в events-файл |

## Ключевые детали реализации

**PTY:** родительский терминал должен иметь `ICRNL` выключен — иначе Enter конвертируется в `\n` вместо `\r`, и ccs/Ink не распознаёт нажатие.

**Числовые клавиши для диалогов:** Permission-диалог: 1=Allow, 2=Allow all, 3=Deny. AskUserQuestion: 1/2/3... для вариантов. Стрелки не работают.

**Протокол:** `OUTPUT` и `NOTIFY` используют `len(data)` в **байтах** (не символах) для `readexactly()`. `INPUT` добавляет `\r`, `RESP` — нет.

**callback_data Telegram:** максимум 64 байта, поэтому используется короткий ключ `p|{N}` с хранением полных данных в `_pending_perms`.

## Установка и тестирование

```bash
install.sh    # установка (venv, зависимости, хуки, systemd)
uninstall.sh  # удаление
```

Ручная перезагрузка кода при отладке:
```bash
cp cctg/*.py ~/.cctg/cctg/ && systemctl --user restart cctg
```

Мост (`cctg launch`) нужно перезапускать вручную (Ctrl+C и заново) — systemctl только для демона.

## Запуск

```bash
cctg start       # запуск демона (systemd)
cctg stop        # остановка
cctg status      # статус
cctg launch <ccs-profile>  # запуск сессии Claude Code
```
