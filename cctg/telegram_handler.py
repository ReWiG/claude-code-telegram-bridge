"""Telegram bot handler — commands, callbacks, message forwarding."""
from __future__ import annotations

import json
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

from cctg.db import Database
from cctg.session_manager import SessionManager
from cctg.tty_router import TTYRouter

logger = logging.getLogger(__name__)


class TelegramHandler:
    def __init__(
        self,
        token: str,
        chat_id: str,
        db: Database,
        session_manager: SessionManager,
        tty_router: TTYRouter,
        proxy: str | None = None,
    ):
        self.token = token
        self.chat_id = chat_id
        self.db = db
        self.sm = session_manager
        self.tty = tty_router
        self.app: Application | None = None
        self._proxy = proxy
        self._input_callback = None
        self._response_callback = None
        self._pending_perms: dict[str, dict] = {}
        self._perm_counter = 0
        self._active_perm_msg_id: int | None = None  # current permission prompt msg

    def set_input_callback(self, cb):
        self._input_callback = cb

    def set_response_callback(self, cb):
        self._response_callback = cb

    async def start(self) -> None:
        builder = Application.builder().token(self.token)
        if self._proxy:
            builder.proxy(self._proxy)
            builder.get_updates_proxy(self._proxy)
        self.app = builder.build()

        self.app.add_handler(CommandHandler("list", self._cmd_list))
        self.app.add_handler(CommandHandler("attach", self._cmd_attach))
        self.app.add_handler(CommandHandler("status", self._cmd_status))
        self.app.add_handler(CommandHandler("detach", self._cmd_detach))
        self.app.add_handler(CommandHandler("help", self._cmd_help))
        self.app.add_handler(CommandHandler("start", self._cmd_help))
        self.app.add_handler(CallbackQueryHandler(self._on_callback))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text))

        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(allowed_updates=["message", "callback_query"], timeout=30, poll_interval=1.0)
        logger.info("Updater running: %s", self.app.updater.running)

    async def stop(self) -> None:
        if self.app:
            try:
                await self.app.updater.stop()
            except RuntimeError:
                pass
            try:
                await self.app.stop()
            except RuntimeError:
                pass
            try:
                await self.app.shutdown()
            except RuntimeError:
                pass

    async def send_message(self, text: str, reply_markup=None) -> int | None:
        # Auto-attach reply keyboard when attached and no inline keyboard is used
        if reply_markup is None:
            attached_id = await self.db.get_state("attached_session")
            if attached_id:
                reply_markup = self._build_kb(attached=True)
        try:
            msg = await self.app.bot.send_message(
                chat_id=self.chat_id, text=text, reply_markup=reply_markup, parse_mode="HTML",
            )
            return msg.message_id
        except Exception as e:
            logger.error(f"send_message failed: {e}")
            return None

    def clear_active_perm(self) -> None:
        """Forget the current permission prompt message (on detach / session change)."""
        self._active_perm_msg_id = None

    async def send_last_output(self, session_id: str) -> None:
        """Send current live message buffer for a session (used on attach)."""
        live = await self.db.get_live_message(session_id)
        if not live or not live.get("buffer", "").strip():
            return
        s = await self.db.get_session(session_id)
        cwd = s["cwd"] if s else "?"
        await self.send_message(
            f"📌 <b>Текущий контекст (#{session_id[:8]} {cwd})</b>\n\n{live['buffer']}"
        )

    async def edit_message(self, message_id: int, text: str, reply_markup=None) -> bool:
        try:
            await self.app.bot.edit_message_text(
                chat_id=self.chat_id, message_id=message_id, text=text,
                parse_mode="HTML", reply_markup=reply_markup,
            )
            return True
        except Exception as e:
            logger.error(f"edit_message failed: {e}")
            return False

    def _store_perm(self, sid: str, tu_id: str, action: str, idx: int = 0) -> str:
        self._perm_counter += 1
        key = str(self._perm_counter)
        self._pending_perms[key] = {"sid": sid, "tu_id": tu_id, "action": action, "idx": idx}
        return key

    async def send_permission_prompt(self, session_info: dict, message: str, tool_use: dict | None = None, pty_options: list[str] | None = None) -> int | None:
        sid = session_info["session_id"]
        short_id = sid[:8]
        cwd = session_info.get("cwd", "")
        text = f"⚠️ <b>Claude Code (#{short_id} {cwd}) запрашивает разрешение:</b>"
        if message:
            text += f"\n\n<code>{message}</code>"

        tu = tool_use or {}
        tu_name = tu.get("name", "")
        tu_input = tu.get("input", {})
        tu_id = tu.get("id", "")

        if tu_name == "Agent":
            # Only show agent description if PTY dialog text is unavailable
            if not message or message == "Claude needs your permission":
                desc = tu_input.get("description", "")
                if desc:
                    text += f"\n\n🤖 <b>Субагент:</b> {desc}"
            kb = self._perm_kb_from_options(sid, tu_id, pty_options)
        elif tu_name == "AskUserQuestion":
            questions = tu_input.get("questions", [])
            if questions:
                q = questions[0]
                text += f"\n\n<b>{q.get('question', '')}</b>"
                rows = []
                for idx, opt in enumerate(q.get("options", [])):
                    key = self._store_perm(sid, tu_id, "answer", idx)
                    rows.append([InlineKeyboardButton(
                        opt.get("label", f"Option {idx+1}"),
                        callback_data=f"p|{key}",
                    )])
                kb = InlineKeyboardMarkup(rows)
            else:
                kb = self._perm_kb_from_options(sid, tu_id, pty_options)
        elif tu_name == "Bash":
            cmd = tu_input.get("command", "")
            desc = tu_input.get("description", "")
            if desc:
                text += f"\n\n<pre>{desc}</pre>"
            if cmd:
                text += f"\n\n<pre>$ {cmd}</pre>"
            kb = self._perm_kb_from_options(sid, tu_id, pty_options)
        elif tu_name in ("Edit", "Write", "Read"):
            file_path = tu_input.get("file_path", "")
            if file_path:
                text += f"\n\n📄 <code>{file_path}</code>"
            if tu_name == "Edit":
                old = tu_input.get("old_string", "")
                new = tu_input.get("new_string", "")
                diff = ""
                for line in old.split("\n"):
                    diff += f"🟥 {line}\n" if line else "\n"
                for line in new.split("\n"):
                    diff += f"🟩 {line}\n" if line else "\n"
                text += f"\n\n<pre>{diff.strip()}</pre>"
            elif tu_name == "Write":
                content = tu_input.get("content", "")
                text += f"\n\n<pre>{content[:1500]}</pre>"
            kb = self._perm_kb_from_options(sid, tu_id, pty_options)
        elif tu_name in ("WebSearch", "WebFetch"):
            query = tu_input.get("query", "") or tu_input.get("url", "")
            if query:
                text += f"\n\n🔗 <code>{query}</code>"
            kb = self._perm_kb_from_options(sid, tu_id, pty_options)
        else:
            if tu_input:
                summary = tu_input.get("description", "") or tu_input.get("command", "") or tu_input.get("query", "")
                if summary:
                    text += f"\n\n<code>{str(summary)[:500]}</code>"
                else:
                    text += f"\n\n<pre>{json.dumps(tu_input, ensure_ascii=False)[:500]}</pre>"
            kb = self._perm_kb_from_options(sid, tu_id, pty_options)

        # Edit existing permission message instead of sending a new one.
        # Matches terminal behaviour where Ink replaces the active dialog.
        if self._active_perm_msg_id is not None:
            ok = await self.edit_message(self._active_perm_msg_id, text, reply_markup=kb)
            if ok:
                return self._active_perm_msg_id
            self._active_perm_msg_id = None  # edit failed, fall through to send new

        msg_id = await self.send_message(text, kb)
        if msg_id:
            self._active_perm_msg_id = msg_id
        return msg_id

    def _default_perm_kb(self, sid: str, tu_id: str = "") -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Allow", callback_data=f"p|{self._store_perm(sid, tu_id, 'y')}"),
                InlineKeyboardButton("❌ Deny", callback_data=f"p|{self._store_perm(sid, tu_id, 'n')}"),
            ],
            [InlineKeyboardButton("\U0001f513 Allow all", callback_data=f"p|{self._store_perm(sid, tu_id, 'a')}")],
        ])

    def _perm_kb_from_options(self, sid: str, tu_id: str = "", pty_options: list[str] | None = None) -> InlineKeyboardMarkup:
        """Build permission keyboard from parsed PTY options, falling back to defaults."""
        if pty_options and len(pty_options) >= 2:
            rows = []
            for idx, label in enumerate(pty_options):
                # Store action as str(idx+1) so callback sends "1\r", "2\r", etc.
                key = self._store_perm(sid, tu_id, str(idx + 1))
                # Truncate long labels for Telegram (max ~64 chars for callback_data, label can be longer)
                short_label = label if len(label) <= 80 else label[:77] + "..."
                rows.append([InlineKeyboardButton(short_label, callback_data=f"p|{key}")])
            return InlineKeyboardMarkup(rows)
        return self._default_perm_kb(sid, tu_id)

    async def handle_command(self, command_text: str) -> tuple[str, dict | None]:
        parts = command_text.split()
        cmd = parts[0].lower().lstrip("/")
        args = parts[1:] if len(parts) > 1 else []
        if cmd == "list":
            return await self._handle_list()
        elif cmd == "attach":
            return await self._handle_attach(args)
        elif cmd == "status":
            return await self._handle_status()
        elif cmd == "detach":
            return await self._handle_detach()
        elif cmd in ("help", "start"):
            return await self._handle_help()
        else:
            return "Неизвестная команда. /help", None

    async def handle_message(self, text: str) -> tuple[str, dict | None]:
        attached_id = await self.db.get_state("attached_session")
        if not attached_id:
            return None, None
        if self._input_callback:
            await self._input_callback(attached_id, text)
        return None, None

    async def handle_callback(self, callback_data: str) -> None:
        parts = callback_data.split("|", 1)
        if len(parts) != 2:
            return
        action, session_id = parts
        session = await self.db.get_session(session_id)
        tty_path = session.get("tty") if session else None
        if tty_path:
            self.tty.write_response(action, tty_path)
        elif self._input_callback:
            mapped = self.tty.RESPONSE_MAP.get(action, action)
            await self._input_callback(session_id, mapped)

    async def _handle_list(self) -> tuple[str, dict]:
        sessions = await self.db.list_active_sessions()
        attached_id = await self.db.get_state("attached_session")

        if not sessions:
            return "\U0001f7e2 Нет активных сессий Claude Code.", self._build_kb()

        lines = ["\U0001f7e2 <b>Активные сессии Claude Code:</b>"]
        for i, s in enumerate(sessions, 1):
            sid = s["session_id"][:8]
            cwd = s["cwd"]
            tty = s.get("tty") or "—"
            lines.append("")
            lines.append(f"<b>#{i}</b>  \U0001f4c1 {cwd}  \U0001f5a5 {tty}  \U0001f194 {sid}")

        lines.append("")
        if attached_id:
            try:
                idx = next(i for i, s in enumerate(sessions) if s["session_id"] == attached_id) + 1
                attached_str = f"#{idx} ({sessions[idx-1]['cwd']})"
            except StopIteration:
                attached_str = attached_id[:8]
        else:
            attached_str = "нет"
        lines.append(f"\U0001f4ce Прикреплён: {attached_str}")

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"🔗 #{i}", callback_data=f"attach|{s['session_id']}")]
            for i, s in enumerate(sessions, 1)
        ])
        return "\n".join(lines), kb

    async def _handle_attach(self, args: list[str]) -> tuple[str, dict]:
        if not args:
            return "Укажи номер сессии: /attach 1", self._build_kb()
        try:
            num = int(args[0]) - 1
        except ValueError:
            return f"Неверный номер: {args[0]}. Используй /attach 1", self._build_kb()
        sessions = await self.db.list_active_sessions()
        if num < 0 or num >= len(sessions):
            return f"Сессия #{args[0]} не найдена. /list чтобы посмотреть.", self._build_kb()
        sid = sessions[num]["session_id"]
        await self.sm.attach(sid)
        cwd = sessions[num]["cwd"]
        return (
            f"✅ Прикреплён к #{num + 1} ({cwd})\n\n"
            "Пиши текст для отправки в терминал Claude Code."
        ), self._build_kb(attached=True)

    async def _handle_status(self) -> tuple[str, dict]:
        attached_id = await self.db.get_state("attached_session")
        sessions = await self.db.list_active_sessions()
        lines = [f"\U0001f7e2 Активных сессий: {len(sessions)}"]
        if attached_id:
            s = await self.db.get_session(attached_id)
            if s:
                lines.append(f"\U0001f4c1 Прикреплён: {s['cwd']}")
        else:
            lines.append("\U0001f4c1 Не прикреплён")
        return "\n".join(lines), self._build_kb(attached=bool(attached_id))

    async def _handle_detach(self) -> tuple[str, dict]:
        await self.sm.detach()
        self.clear_active_perm()
        return "❌ Откреплён. Используй /list и /attach чтобы выбрать сессию.", self._build_kb()

    async def _handle_help(self) -> tuple[str, dict]:
        text = (
            "\U0001f916 <b>cctg — Claude Code Telegram Bridge</b>\n\n"
            "/list — список активных сессий\n"
            "/attach &lt;номер&gt; — прикрепиться к сессии\n"
            "/status — статус демона и привязки\n"
            "/detach — открепиться от сессии\n"
            "/help — это меню\n\n"
            "<i>При прикреплении к сессии в /list появятся инлайн-кнопки для быстрого выбора.</i>\n"
            "<i>После прикрепления просто пиши текст — он отправится в терминал Claude Code.</i>"
        )
        return text, self._build_kb()

    async def _cmd_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text, kb = await self._handle_list()
        await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")

    async def _cmd_attach(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        args = context.args or []
        text, kb = await self._handle_attach(args)
        await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")
        if text.startswith("✅"):
            sid = await self.db.get_state("attached_session")
            if sid:
                await self.send_last_output(sid)

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text, kb = await self._handle_status()
        await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")

    async def _cmd_detach(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text, kb = await self._handle_detach()
        await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text, kb = await self._handle_help()
        await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")

    async def _on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        data = query.data or ""

        if data.startswith("attach|"):
            sid = data.split("|", 1)[1]
            try:
                await self.sm.attach(sid)
                s = await self.db.get_session(sid)
                cwd = s["cwd"] if s else "?"
                await query.edit_message_text(
                    text=query.message.text + f"\n\n✅ Прикреплён к {cwd}",
                    parse_mode="HTML",
                )
                await query.message.reply_text(
                    "✅ Готово. Пиши текст для отправки в терминал.",
                    reply_markup=self._build_kb(attached=True),
                )
                await self.send_last_output(sid)
            except ValueError as e:
                await query.edit_message_text(
                    text=query.message.text + f"\n\n❌ {e}",
                    parse_mode="HTML",
                )
            return

        if data.startswith("p|"):
            key = data.split("|", 1)[1]
            info = self._pending_perms.pop(key, None)
            if info and self._response_callback:
                sid = info["sid"]
                action = info["action"]
                if action == "answer":
                    idx = info.get("idx", 0)
                    response = str(idx + 1) + "\r"
                elif action.isdigit():
                    response = action + "\r"
                else:
                    NUM_MAP = {"y": "1", "a": "2", "n": "3"}
                    response = NUM_MAP.get(action, "2") + "\r"
                # Find the button label that was clicked
                answer_text = ""
                try:
                    for row in query.message.reply_markup.inline_keyboard:
                        for btn in row:
                            if btn.callback_data == query.data:
                                answer_text = btn.text
                                break
                except Exception:
                    pass
                # Clear active perm BEFORE answering — next perm creates new msg
                self._active_perm_msg_id = None
                await self._response_callback(sid, response)
                confirm = f"\n\n✅ Ответ '{answer_text}' отправлен." if answer_text else "\n\n✅ Ответ отправлен."
                await query.edit_message_text(
                    text=query.message.text + confirm, parse_mode="HTML",
                )
            return

        if data.startswith("allow|") or data.startswith("deny|") or data.startswith("allow_all|"):
            await self.handle_callback(data)
            await query.edit_message_text(
                text=query.message.text + "\n\n✅ Ответ отправлен.", parse_mode="HTML",
            )
            return

    async def _on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = update.message.text or ""

        # "Stop execution" button — send Escape to interrupt the agent
        if text == "⏹ Остановить":
            sid = await self.db.get_state("attached_session")
            if sid and self._response_callback:
                await self._response_callback(sid, "\x1b")
                await update.message.reply_text("⏹ Команда прерывания отправлена.")
            else:
                await update.message.reply_text("Нет прикреплённой сессии.")
            return

        command = None
        if text.startswith("/"):
            command = text
        else:
            button_map = {
                "📋 Список сессий": "/list",
                "ℹ️ Статус": "/status",
                "❌ Открепить": "/detach",
                "❓ Помощь": "/help",
            }
            command = button_map.get(text)

        if command:
            resp_text, kb = await self.handle_command(command)
            if resp_text:
                await update.message.reply_text(resp_text, reply_markup=kb, parse_mode="HTML")
            return

        resp, kb = await self.handle_message(text)
        if resp:
            await update.message.reply_text(resp, reply_markup=kb, parse_mode="HTML")

    def _build_kb(self, attached: bool = False) -> ReplyKeyboardMarkup:
        if attached:
            buttons = [
                [KeyboardButton("⏹ Остановить")],
                [KeyboardButton("ℹ️ Статус"), KeyboardButton("📋 Список сессий")],
                [KeyboardButton("❌ Открепить")],
            ]
        else:
            buttons = [
                [KeyboardButton("📋 Список сессий"), KeyboardButton("ℹ️ Статус")],
                [KeyboardButton("❓ Помощь")],
            ]
        return ReplyKeyboardMarkup(buttons, resize_keyboard=True, is_persistent=True)

    def build_keyboard(self, attached: bool = False):
        kb = self._build_kb(attached)
        return kb.keyboard
