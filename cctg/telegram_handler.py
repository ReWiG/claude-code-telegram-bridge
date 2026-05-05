"""Telegram bot handler — commands, callbacks, message forwarding."""
from __future__ import annotations

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

    async def start(self) -> None:
        builder = Application.builder().token(self.token)
        if self._proxy:
            builder.proxy(self._proxy)
            builder.get_updates_proxy(self._proxy)
        self.app = builder.build()

        self.app.add_handler(CommandHandler("list", self._cmd_list))
        self.app.add_handler(CommandHandler("attach", self._cmd_attach))
        self.app.add_handler(CommandHandler("start_track", self._cmd_start_track))
        self.app.add_handler(CommandHandler("stop_track", self._cmd_stop_track))
        self.app.add_handler(CommandHandler("status", self._cmd_status))
        self.app.add_handler(CommandHandler("detach", self._cmd_detach))
        self.app.add_handler(CommandHandler("help", self._cmd_help))
        self.app.add_handler(CommandHandler("start", self._cmd_help))
        self.app.add_handler(CallbackQueryHandler(self._on_callback))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text))

        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(allowed_updates=["message", "callback_query"], timeout=2, poll_interval=0.5)
        logger.info("Updater running: %s", self.app.updater.running)

    async def stop(self) -> None:
        if self.app:
            try:
                await self.app.updater.stop()
            except RuntimeError:
                pass
            await self.app.stop()
            await self.app.shutdown()

    async def send_message(self, text: str, reply_markup=None) -> int | None:
        try:
            msg = await self.app.bot.send_message(
                chat_id=self.chat_id, text=text, reply_markup=reply_markup, parse_mode="HTML",
            )
            return msg.message_id
        except Exception as e:
            logger.error(f"send_message failed: {e}")
            return None

    async def edit_message(self, message_id: int, text: str) -> bool:
        try:
            await self.app.bot.edit_message_text(
                chat_id=self.chat_id, message_id=message_id, text=text, parse_mode="HTML",
            )
            return True
        except Exception as e:
            logger.error(f"edit_message failed: {e}")
            return False

    async def send_permission_prompt(self, session_info: dict, message: str) -> int | None:
        sid = session_info["session_id"]
        short_id = sid[:8]
        cwd = session_info.get("cwd", "")
        text = f"⚠️ <b>Claude Code (#{short_id} {cwd}) запрашивает разрешение:</b>\n\n<code>{message}</code>"
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Allow", callback_data=f"allow|{sid}"),
                InlineKeyboardButton("❌ Deny", callback_data=f"deny|{sid}"),
            ],
            [InlineKeyboardButton("\U0001f513 Allow all", callback_data=f"allow_all|{sid}")],
        ])
        return await self.send_message(text, kb)

    async def handle_command(self, command_text: str) -> tuple[str, dict | None]:
        parts = command_text.split()
        cmd = parts[0].lower().lstrip("/")
        args = parts[1:] if len(parts) > 1 else []
        if cmd == "list":
            return await self._handle_list()
        elif cmd == "attach":
            return await self._handle_attach(args)
        elif cmd == "start_track":
            return await self._handle_start_track()
        elif cmd == "stop_track":
            return await self._handle_stop_track()
        elif cmd == "status":
            return await self._handle_status()
        elif cmd == "detach":
            return await self._handle_detach()
        elif cmd in ("help", "start"):
            return await self._handle_help()
        else:
            return "Неизвестная команда. /help", None

    async def handle_message(self, text: str) -> tuple[str, dict | None]:
        watch = await self.db.get_state("watch_active")
        attached_id = await self.db.get_state("attached_session")
        if not watch or not attached_id:
            return "", None
        session = await self.db.get_session(attached_id)
        if not session or not session.get("tty"):
            return "", None
        self.tty.write_text(text, session["tty"])
        return "", None

    async def handle_callback(self, callback_data: str) -> None:
        parts = callback_data.split("|", 1)
        if len(parts) != 2:
            return
        action, session_id = parts
        session = await self.db.get_session(session_id)
        tty_path = session.get("tty") if session else None
        if tty_path:
            self.tty.write_response(action, tty_path)

    async def _handle_list(self) -> tuple[str, dict]:
        sessions = await self.db.list_active_sessions()
        attached_id = await self.db.get_state("attached_session")
        watch = await self.db.get_state("watch_active") == "1"

        if not sessions:
            return "\U0001f7e2 Нет активных сессий Claude Code.", self._build_kb()

        lines = ["\U0001f7e2 <b>Активные сессии Claude Code:</b>\n"]
        for i, s in enumerate(sessions, 1):
            sid = s["session_id"][:8]
            cwd = s["cwd"]
            branch = s.get("branch") or "—"
            tty = s.get("tty") or "—"
            pname = s.get("project_name") or ""
            lines.append(f"<b>#{i}</b>  {pname}  \U0001f4c1 {cwd}  \U0001f330 {branch}  \U0001f5a5 {tty}  \U0001f194 {sid}")

        lines.append("")
        if attached_id:
            try:
                idx = next(i for i, s in enumerate(sessions) if s["session_id"] == attached_id) + 1
                attached_str = f"#{idx}"
            except StopIteration:
                attached_str = attached_id[:8]
        else:
            attached_str = "нет"
        watch_str = "вкл" if watch else "выкл"
        lines.append(f"Прикреплён: {attached_str}  \U0001f441 Отслеживание: {watch_str}")
        lines.append("\nВыбери сессию: /attach &lt;номер&gt;")
        return "\n".join(lines), self._build_kb(attached=bool(attached_id), tracking=watch)

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
            f"Используй /start_track чтобы начать отслеживание или пиши текст для отправки в терминал."
        ), self._build_kb(attached=True, tracking=False)

    async def _handle_start_track(self) -> tuple[str, dict]:
        try:
            await self.sm.start_tracking()
            s = await self.sm.get_attached_session()
            cwd = s["cwd"] if s else "?"
            return f"▶ Отслеживание включено для {cwd}\n\nВесь вывод Claude Code будет пересылаться сюда.", self._build_kb(attached=True, tracking=True)
        except ValueError as e:
            return f"❌ {e}", self._build_kb()

    async def _handle_stop_track(self) -> tuple[str, dict]:
        try:
            await self.sm.stop_tracking()
            return "⏸ Отслеживание остановлено.", self._build_kb(attached=True, tracking=False)
        except ValueError as e:
            return f"❌ {e}", self._build_kb()

    async def _handle_status(self) -> tuple[str, dict]:
        attached_id = await self.db.get_state("attached_session")
        watch = await self.db.get_state("watch_active") == "1"
        sessions = await self.db.list_active_sessions()
        lines = [f"\U0001f7e2 Активных сессий: {len(sessions)}"]
        if attached_id:
            s = await self.db.get_session(attached_id)
            if s:
                lines.append(f"\U0001f4c1 Прикреплён: {s['cwd']}")
        else:
            lines.append("\U0001f4c1 Не прикреплён")
        lines.append(f"\U0001f441 Отслеживание: {'вкл' if watch else 'выкл'}")
        return "\n".join(lines), self._build_kb(attached=bool(attached_id), tracking=watch)

    async def _handle_detach(self) -> tuple[str, dict]:
        await self.sm.detach()
        return "❌ Откреплён. Используй /list и /attach чтобы выбрать сессию.", self._build_kb()

    async def _handle_help(self) -> tuple[str, dict]:
        text = (
            "\U0001f916 <b>cctg — Claude Code Telegram Bridge</b>\n\n"
            "/list — показать активные сессии\n"
            "/attach &lt;номер&gt; — выбрать сессию\n"
            "/start_track — начать отслеживание\n"
            "/stop_track — остановить отслеживание\n"
            "/status — статус\n"
            "/detach — открепиться\n"
            "/help — это меню"
        )
        return text, self._build_kb()

    async def _cmd_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text, kb = await self._handle_list()
        await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")

    async def _cmd_attach(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        args = context.args or []
        text, kb = await self._handle_attach(args)
        await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")

    async def _cmd_start_track(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text, kb = await self._handle_start_track()
        await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")

    async def _cmd_stop_track(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text, kb = await self._handle_stop_track()
        await update.message.reply_text(text, reply_markup=kb, parse_mode="HTML")

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
        await self.handle_callback(query.data)
        await query.edit_message_text(
            text=query.message.text + "\n\n✅ Ответ отправлен.", parse_mode="HTML",
        )

    async def _on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = update.message.text or ""
        # Route button presses to commands
        command = None
        if text.startswith("/"):
            command = text
        else:
            button_map = {
                "📋 Список сессий": "/list",
                "▶ Начать отслеживание": "/start_track",
                "⏸ Остановить": "/stop_track",
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

        await self.handle_message(text)

    def _build_kb(self, attached: bool = False, tracking: bool = False) -> ReplyKeyboardMarkup:
        buttons = []
        if tracking:
            buttons.append([KeyboardButton("⏸ Остановить"), KeyboardButton("ℹ️ Статус")])
            buttons.append([KeyboardButton("📋 Список сессий"), KeyboardButton("❌ Открепить")])
        elif attached:
            buttons.append([KeyboardButton("▶ Начать отслеживание"), KeyboardButton("ℹ️ Статус")])
            buttons.append([KeyboardButton("📋 Список сессий"), KeyboardButton("❌ Открепить")])
        else:
            buttons.append([KeyboardButton("📋 Список сессий"), KeyboardButton("ℹ️ Статус")])
            buttons.append([KeyboardButton("❓ Помощь")])
        return ReplyKeyboardMarkup(buttons, resize_keyboard=True, is_persistent=True)

    def build_keyboard(self, attached: bool = False, tracking: bool = False):
        kb = self._build_kb(attached, tracking)
        return kb.keyboard
