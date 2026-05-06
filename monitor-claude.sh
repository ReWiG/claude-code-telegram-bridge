#!/usr/bin/env bash
# Monitor for Claude Code — watches log file, sends Telegram notifications
# Uses a separate bot via SOCKS5 proxy

TG_TOKEN="REDACTED_TELEGRAM_TOKEN"
CHAT_ID="740542766"
PROXY="socks5://127.0.0.1:10808"
LOG_FILE="/tmp/claude-current.log"
WATCH_FILE="/tmp/cc-monitor-watch"
OFFSET_FILE="/tmp/cc-monitor-offset"
POS_FILE="/tmp/cc-monitor-logpos"
SEEN_FILE="/tmp/cc-monitor-seen"
PID_FILE="/tmp/cc-monitor.pid"
INTERVAL=5

echo "$$" > "$PID_FILE"

QUESTION_PATTERNS='Allow\?|Proceed|Continue\?|\(Y/n\)|\[y/N\]|Choose|Select|confirm|approve|permission|Which|How to|Select an option|Claude wants to run|Accept\?|Reject|Proceed\?|This command requires approval|Do you want to proceed|Yes, and don.t ask again|requires approval|Select an option|\[exit\]|Esc to cancel'

log() { echo "[$(date '+%H:%M:%S')] $*"; }

send_msg() {
  local msg="$1"; local kb="$2"
  msg=$(printf '%s' "$msg" | sed 's/%0A/\n/g')
  if [ -n "$kb" ]; then
    curl -s --max-time 10 --proxy "$PROXY" \
      --data-urlencode "chat_id=$CHAT_ID" \
      --data-urlencode "parse_mode=HTML" \
      --data-urlencode "text=$msg" \
      --data-urlencode "reply_markup=$kb" \
      "https://api.telegram.org/bot$TG_TOKEN/sendMessage" > /dev/null 2>&1
  else
    curl -s --max-time 10 --proxy "$PROXY" \
      --data-urlencode "chat_id=$CHAT_ID" \
      --data-urlencode "parse_mode=HTML" \
      --data-urlencode "text=$msg" \
      "https://api.telegram.org/bot$TG_TOKEN/sendMessage" > /dev/null 2>&1
  fi
}

KB='{"keyboard":[[{"text":"🎯 Watch"},{"text":"⏸ Stop"}],[{"text":"📋 Status"},{"text":"❓ Help"}]],"resize_keyboard":true,"is_persistent":true}'

handle_cmd() {
  case "$1" in
    watch)
      touch "$WATCH_FILE"; rm -f "$POS_FILE" "$SEEN_FILE"
      send_msg "🔍 Мониторинг включён" "$KB"; log "Watch ON" ;;
    stop)
      rm -f "$WATCH_FILE"
      curl -s --max-time 10 --proxy "$PROXY" \
        --data-urlencode "chat_id=$CHAT_ID" \
        --data-urlencode "text=⏸ Мониторинг выключен" \
        --data-urlencode 'reply_markup={"keyboard":[[{"text":"🎯 Watch"},{"text":"⏸ Stop"}],[{"text":"📋 Status"},{"text":"❓ Help"}]],"resize_keyboard":true}' \
        "https://api.telegram.org/bot$TG_TOKEN/sendMessage" > /dev/null 2>&1
      log "Watch OFF" ;;
    help)
      send_msg '🤖 <b>Claude Code Monitor</b>%0A%0A🎯 Watch — включить слежение%0A⏸ Stop — выключить%0A📋 Status — статус%0A❓ Help — это меню%0A%0A<i>Лог: /tmp/claude-current.log</i>' "$KB" ;;
    status)
      local w="off"; [ -f "$WATCH_FILE" ] && w="on"
      local r="off"; [ -f "$LOG_FILE" ] && r="on"
      send_msg "🟢 Монитор: <b>$w</b>%0A📂 Claude: <b>$r</b>%0A⏱ Интервал: ${INTERVAL}с" "$KB" ;;
  esac
}

check_replies() {
  local offset=0; [ -f "$OFFSET_FILE" ] && offset=$(cat "$OFFSET_FILE")
  local raw; raw=$(curl -s --max-time 8 --proxy "$PROXY" \
    -d "offset=$offset" -d "timeout=3" -d 'allowed_updates=["message"]' \
    "https://api.telegram.org/bot$TG_TOKEN/getUpdates")

  # Save offset and process messages in one Python call
  local result
  result=$(echo "$raw" | OFFSET_FILE="$OFFSET_FILE" python3 -c "
import json, sys, os
data = json.load(sys.stdin)
results = data.get('result', [])
if not results: sys.exit(0)
with open(os.environ['OFFSET_FILE'], 'w') as f:
    f.write(str(results[-1]['update_id'] + 1))
for r in results:
    msg = r.get('message', {})
    cid = str(msg.get('chat', {}).get('id', ''))
    txt = msg.get('text', '').strip()
    print(f'{cid}|{txt}')
" 2>/dev/null)

  [ -z "$result" ] && return

  echo "$result" | while IFS='|' read -r chat text; do
    [ "$chat" != "$CHAT_ID" ] && continue
    [ -z "$text" ] && continue
    log "Reply: $text"

    case "$text" in
      /watch|🎯*) handle_cmd watch; continue ;;
      /stop|⏸*) handle_cmd stop; continue ;;
      /start|/help|/menu|❓*) handle_cmd help; continue ;;
      /status|📋*) handle_cmd status; continue ;;
    esac

    # Forward to Claude Code TTY
    local tty; tty=$(ps -o tty= -C claude 2>/dev/null | head -1 | tr -d ' ')
    if [ -n "$tty" ]; then
      echo "$text" > "/dev/$tty" 2>/dev/null && log "→ sent to $tty" || log "FAILED to write"
    else
      send_msg "❌ Claude Code не запущен. Открой терминал и введи clan."
    fi
  done
}

# --- Main ---
# Не удаляем логи — пользователь сам управляет ими через clan/cdeep
log "Monitor starting"
send_msg '👋 <b>Claude Code Monitor</b> запущен%0A%0AНажми 🎯 Watch когда отходишь от компа' "$KB"

while true; do
  check_replies

  if [ -f "$WATCH_FILE" ] && [ -f "$LOG_FILE" ]; then
    inode=$(stat -c%i "$LOG_FILE" 2>/dev/null || echo 0)
    saved_inode=$(cat "${POS_FILE}.ino" 2>/dev/null || echo 0)
    pos=0
    if [ "$inode" = "$saved_inode" ] && [ -f "$POS_FILE" ]; then
      pos=$(cat "$POS_FILE")
    fi
    size=$(stat -c%s "$LOG_FILE" 2>/dev/null || echo 0)
    if [ "$size" -gt "$pos" ]; then
      new=$(dd if="$LOG_FILE" bs=1 skip="$pos" count=$((size - pos)) 2>/dev/null)
      echo "$size" > "$POS_FILE"
      echo "$inode" > "${POS_FILE}.ino"
      [ -z "$new" ] && { sleep "$INTERVAL"; continue; }

      # Clean ANSI codes
      clean=$(echo "$new" | sed 's/\x1b\[[0-9;]*[a-zA-Z]//g')

      # Detect permission prompts
      if echo "$clean" | grep -qiE "$QUESTION_PATTERNS"; then
        prev=$(cat "$SEEN_FILE" 2>/dev/null || echo "")
        if [ "$prev" != "QUESTION" ]; then
          snip=$(echo "$clean" | grep -E "$QUESTION_PATTERNS|❯|Esc" | tail -4 | sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g' | grep -v '^\s*$')
          send_msg "🤖 <b>Claude Code ждёт ответа</b>%0A%0A<code>$snip</code>%0A%0A<i>Напиши ответ сюда</i>" "$KB"
          echo "QUESTION" > "$SEEN_FILE"
          log "QUESTION"
        fi

      # Если видим ● — запоминаем последний ответ
      if echo "$clean" | grep -q '●'; then
        last_resp=$(echo "$clean" | grep '●' | tail -1 | sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g' | grep -v '^\s*$')
        [ -n "$last_resp" ] && echo "$last_resp" > "$SEEN_FILE"
      fi
        
      # Если видим ✻ после ● — Claude завершил задачу
      if echo "$clean" | grep -q '✻'; then
        saved=$(cat "$SEEN_FILE" 2>/dev/null || echo "")
        if echo "$saved" | grep -q '●'; then
          send_msg "✅ <b>Claude Code завершил</b>%0A%0A<code>$saved</code>" "$KB"
          log "Task completed"
          echo "" > "$SEEN_FILE"
        fi
      fi
      fi
    fi
  fi

  sleep "$INTERVAL"
done
