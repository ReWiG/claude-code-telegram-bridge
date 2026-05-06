# cctg — Claude Code Telegram Bridge

Мост между Claude Code и Telegram. Позволяет управлять сессиями Claude Code из Telegram: видеть вывод модели, отправлять промты, отвечать на запросы разрешений и интерактивные вопросы.

Работает с [Claude Code Switch](https://github.com/kaitranntt/ccs) — обёрткой для запуска Claude Code с разными API-провайдерами.

## Архитектура

```
┌──────────────┐     Unix socket      ┌──────────────┐     Telegram API      ┌──────────┐
│  cctg launch │ ◄──────────────────► │    daemon    │ ◄───────────────────► │ Telegram │
│  (PTY мост)  │   REGISTER/OUTPUT/   │  (сервер)    │   polling/sendMessage │   бот    │
│              │   FLUSH/NOTIFY/      │              │                       │          │
│    stdin ────┤   INPUT/RESP         │              │                       │          │
│    stdout ◄──┤                      │              │                       │          │
└──────┬───────┘                      └──────────────┘                       └──────────┘
       │ PTY
┌──────▼───────┐
│   ccs        │
│  (Claude)    │
│              │
│  ~/.claude/  │──► JSONL транскрипты (чтение вывода модели)
│  projects/   │
└──────────────┘
```

### Компоненты

| Компонент | Назначение |
|-----------|-----------|
| `cctg launch <profile>` | Запускает Claude Code внутри PTY, подключается к демону |
| `cctg daemon` | Сервер: принимает соединения мостов, обрабатывает Telegram |
| `hooks/session.py` | Хук SessionStart — передаёт ID сессии Claude мосту |
| `hooks/notify.py` | Хук Notification — сообщает о запросах разрешений |
| `transcript_watcher` | Читает JSONL-транскрипты, извлекает вывод модели |
| `pty_bridge` | Создаёт PTY, spawn'ит ccs, мультиплексирует I/O |

### Потоки данных

1. **Вывод модели → Telegram**: JSONL-транскрипт → `TranscriptWatcher` → мост → демон → Telegram (чистый текст, без ANSI)
2. **Terminal → Claude**: stdin → мост → PTY master → ccs
3. **Telegram → Claude**: бот → демон → мост → PTY master → ccs
4. **Запросы разрешений**: хук Notification → events-файл → мост → демон → Telegram (с кнопками Allow/Deny/Allow all)

## Установка

```bash
cd cctg
bash install.sh
```

Инсталлятор:
- Копирует файлы в `~/.cctg/`
- Создаёт виртуальное окружение и устанавливает зависимости
- Регистрирует хуки (`SessionStart`, `Notification`) в `~/.claude/settings.json` (с бекапом)
- Создаёт systemd-сервис для демона
- Записывает конфиг в `~/.cctg/config.toml`

### Зависимости

- `python-telegram-bot` — Telegram Bot API
- `aiosqlite` — асинхронная работа с SQLite
- `httpx[socks]` — SOCKS5 прокси (опционально)

### Удаление

```bash
bash uninstall.sh
```

Удаляет хук из `settings.json`, останавливает и удаляет systemd-сервис, удаляет `~/.cctg/`.

## Использование

### Запуск сессии

```bash
cctg launch <profile>
```

Где `<profile>` — имя CCS-профиля. Запускает Claude Code в PTY, регистрирует сессию в демоне, начинает пересылку вывода в Telegram.

### Команды бота

| Команда | Действие |
|---------|----------|
| `/list` | Список активных сессий с инлайн-кнопками для быстрого прикрепления |
| `/attach <N>` | Прикрепиться к сессии — начать отслеживание |
| `/detach` | Открепиться — остановить отслеживание |
| `/status` | Статус: количество сессий, привязка |
| `/help` | Справка по командам |

### Управление демоном

```bash
systemctl --user start cctg     # запустить демон
systemctl --user stop cctg      # остановить
systemctl --user status cctg    # статус
systemctl --user enable cctg    # автозапуск
cctg status                     # альтернативный статус
```

## Возможности

### Пересылка вывода модели

Вывод Claude Code читается из JSONL-транскриптов (`~/.claude/projects/<project>/<session>.jsonl`). В Telegram приходит чистый текст без ANSI-кодов. Каждый новый обмен (промт → ответ) создаёт новое сообщение, которое обновляется в реальном времени пока модель генерирует.

### Запросы разрешений

Когда Claude Code запрашивает разрешение (Bash, Write, Edit, WebSearch, WebFetch и т.д.), в Telegram приходит сообщение с:
- Типом операции и путём к файлу (для Edit/Write)
- Командой и описанием (для Bash)
- Кнопками: **Allow** / **Deny** / **Allow all**

Кнопка Deny отправляет отказ, Allow all — разрешает все последующие однотипные операции в сессии.

### Интерактивные вопросы (AskUserQuestion)

Когда Claude Code задаёт вопрос с вариантами ответа, в Telegram приходят кнопки — по одной на каждый вариант.

### Авто-открепление

При завершении сессии (закрытие терминала, Ctrl+C) сессия автоматически помечается как exited, привязка снимается, в Telegram приходит уведомление.

### Множественные сессии

Демон отслеживает все запущенные сессии. Можно переключаться между ними через `/list` и `/attach`.

## Конфигурация

`~/.cctg/config.toml`:

```toml
[telegram]
token = "123:abc"
chat_id = "456"
proxy = "socks5://127.0.0.1:10808"   # опционально

[paths]
install_dir = "~/.cctg"
transcript_base = "~/.claude/projects"

[timing]
session_cleanup_seconds = 30
```

### Переменные окружения

- `CCTG_CONFIG` — путь к конфигу (по умолчанию `~/.cctg/config.toml`)
- `CCTG_INSTALL_DIR` — каталог установки (по умолчанию `~/.cctg`)

## Протокол мост↔демон

Unix-сокет `~/.cctg/data/cctg.sock`, line-based протокол:

| Сообщение | Направление | Описание |
|-----------|-------------|----------|
| `REGISTER\|sid\|cwd\|pid` | мост → демон | Регистрация сессии |
| `OUTPUT\|sid\|byte_len\n<data>` | мост → демон | Вывод модели для Telegram |
| `FLUSH\|sid` | мост → демон | Финализировать текущее live-сообщение |
| `NOTIFY\|sid\|byte_len\n<json>` | мост → демон | Запрос разрешения |
| `UNREGISTER\|sid` | мост → демон | Закрытие сессии |
| `INPUT\|text` | демон → мост | Текст от пользователя (добавляется `\r`) |
| `RESP\|text` | демон → мост | Одноклавишный ответ (`\r` не добавляется) |
