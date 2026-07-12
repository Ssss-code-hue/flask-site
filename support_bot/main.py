"""IKK VPN — бот поддержки: FAQ по темам и тикеты.

Весь интерфейс живёт в одном сообщении (баннер + подпись + кнопки),
как в основном боте. Тикеты: пользователь пишет сообщение — владелец
получает его и отвечает реплаем, ответ уходит пользователю в чат.
"""
import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (BotCommand, CallbackQuery, FSInputFile,
                           MenuButtonCommands, Message)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from . import db, faq
from .config import BOT_TOKEN, MAIN_BOT_USERNAME, OWNER_ID, OWNER_USERNAME

logging.basicConfig(level=logging.INFO)
dp = Dispatcher()

BANNER = Path(__file__).parent / "assets" / "banner.png"
_banner_file_id = None

# пользователи, нажавшие «Создать тикет» и ещё не написавшие текст
_awaiting_ticket = set()


def fmt_date(ts):
    return datetime.fromtimestamp(ts).strftime("%d.%m.%Y")


# ============ Клавиатуры ============
def main_menu():
    kb = InlineKeyboardBuilder()
    for code, cat in faq.CATS.items():
        kb.button(text=cat["title"], callback_data=f"cat:{code}")
    kb.button(text="🎫 Создать тикет", callback_data="ticket_new")
    kb.button(text="🗂 Мои тикеты", callback_data="tickets")
    kb.button(text="🤖 Основной бот", url=f"https://t.me/{MAIN_BOT_USERNAME}")
    kb.adjust(2, 2, 2, 2, 1)
    return kb.as_markup()


def cat_kb(code):
    kb = InlineKeyboardBuilder()
    for i, item in enumerate(faq.CATS[code]["items"]):
        kb.button(text=item["q"], callback_data=f"q:{code}:{i}")
    kb.button(text="◀ Меню", callback_data="menu")
    kb.adjust(1)
    return kb.as_markup()


def answer_kb(code):
    kb = InlineKeyboardBuilder()
    kb.button(text="◀ К вопросам", callback_data=f"cat:{code}")
    kb.button(text="🎫 Создать тикет", callback_data="ticket_new")
    kb.adjust(1)
    return kb.as_markup()


def tickets_kb(user_id):
    kb = InlineKeyboardBuilder()
    for t in db.user_tickets(user_id):
        label = faq.STATUS_LABEL.get(t["status"], t["status"])
        kb.button(text=f"#{t['id']} · {label} · {fmt_date(t['updated_at'])}",
                  callback_data=f"t:{t['id']}")
    kb.button(text="🎫 Создать тикет", callback_data="ticket_new")
    kb.button(text="◀ Меню", callback_data="menu")
    kb.adjust(1)
    return kb.as_markup()


def ticket_kb(t):
    kb = InlineKeyboardBuilder()
    if t["status"] != "closed":
        kb.button(text="✅ Закрыть тикет", callback_data=f"tclose:{t['id']}")
    kb.button(text="◀ Мои тикеты", callback_data="tickets")
    kb.adjust(1)
    return kb.as_markup()


def back_kb(target="menu"):
    kb = InlineKeyboardBuilder()
    kb.button(text="◀ Назад", callback_data=target)
    return kb.as_markup()


# ============ Один живой экран: баннер + подпись ============
async def send_welcome(message: Message):
    global _banner_file_id
    if BANNER.exists():
        try:
            photo = _banner_file_id or FSInputFile(BANNER)
            sent = await message.answer_photo(
                photo, caption=faq.WELCOME, reply_markup=main_menu()
            )
            if not _banner_file_id:
                _banner_file_id = sent.photo[-1].file_id
            return
        except Exception:
            logging.exception("Баннер поддержки не отправился — шлём текстом")
    await message.answer(faq.WELCOME, reply_markup=main_menu())


async def show_screen(cq: CallbackQuery, text, reply_markup=None):
    m = cq.message
    if m.photo:
        await m.edit_caption(caption=text, reply_markup=reply_markup)
    else:
        await m.edit_text(text, reply_markup=reply_markup)


# ============ Меню и FAQ ============
@dp.message(CommandStart())
async def cmd_start(message: Message):
    await send_welcome(message)


@dp.callback_query(F.data == "menu")
async def cb_menu(cq: CallbackQuery):
    _awaiting_ticket.discard(cq.from_user.id)
    await show_screen(cq, faq.WELCOME, reply_markup=main_menu())
    await cq.answer()


@dp.callback_query(F.data.startswith("cat:"))
async def cb_cat(cq: CallbackQuery):
    code = cq.data.split(":", 1)[1]
    cat = faq.CATS.get(code)
    if not cat:
        await cq.answer("Раздел не найден", show_alert=True)
        return
    await show_screen(cq, f"{cat['title']}\n\nВыберите вопрос:", reply_markup=cat_kb(code))
    await cq.answer()


@dp.callback_query(F.data.startswith("q:"))
async def cb_question(cq: CallbackQuery):
    _, code, idx = cq.data.split(":")
    try:
        item = faq.CATS[code]["items"][int(idx)]
    except (KeyError, IndexError, ValueError):
        await cq.answer("Вопрос не найден", show_alert=True)
        return
    await show_screen(cq, f"<b>{item['q']}</b>\n\n{item['a']}", reply_markup=answer_kb(code))
    await cq.answer()


# ============ Тикеты ============
def _who(user):
    return f"@{user.username}" if user.username else f"id{user.id}"


async def _notify_owner(bot, tid, user, text, appended=False):
    """Сообщение владельцу; ответ реплаем на него уйдёт пользователю."""
    if not OWNER_ID:
        return
    head = "дополнение к тикету" if appended else "новый тикет"
    try:
        await bot.send_message(
            OWNER_ID,
            f"🎫 <b>Тикет #{tid}</b> — {head}\n"
            f"от {_who(user)} (id{user.id})\n\n{text[:3500]}\n\n"
            f"<i>Ответьте реплаем на это сообщение — ответ уйдёт пользователю. "
            f"Реплай командой /close закроет тикет.</i>",
        )
    except Exception:
        logging.exception("Тикет #%s: не удалось уведомить владельца", tid)


@dp.callback_query(F.data == "ticket_new")
async def cb_ticket_new(cq: CallbackQuery):
    _awaiting_ticket.add(cq.from_user.id)
    await show_screen(cq, faq.TICKET_PROMPT, reply_markup=back_kb("menu"))
    await cq.answer()


@dp.callback_query(F.data == "tickets")
async def cb_tickets(cq: CallbackQuery):
    _awaiting_ticket.discard(cq.from_user.id)
    if db.user_tickets(cq.from_user.id):
        text = "🗂 <b>Мои тикеты</b>\n\nВыберите тикет, чтобы посмотреть переписку:"
    else:
        text = "🗂 <b>Мои тикеты</b>\n\nПока пусто. Создайте тикет — ответим быстро."
    await show_screen(cq, text, reply_markup=tickets_kb(cq.from_user.id))
    await cq.answer()


def _ticket_view(t):
    lines = [f"🎫 <b>Тикет #{t['id']}</b> · {faq.STATUS_LABEL.get(t['status'], t['status'])}",
             f"<i>создан {fmt_date(t['created_at'])}</i>", ""]
    for m in db.ticket_messages(t["id"]):
        author = "💬 Поддержка" if m["from_owner"] else "🧑 Вы"
        text = m["text"] if len(m["text"]) <= 180 else m["text"][:180] + "…"
        lines.append(f"{author}: {text}")
    lines.append("")
    lines.append("Дописать в тикет — просто отправьте сообщение в чат.")
    return "\n".join(lines)[:1024]


@dp.callback_query(F.data.startswith("t:"))
async def cb_ticket(cq: CallbackQuery):
    tid = int(cq.data.split(":", 1)[1])
    t = db.get_ticket(tid)
    if not t or (t["user_id"] != cq.from_user.id and cq.from_user.id != OWNER_ID):
        await cq.answer("Тикет не найден", show_alert=True)
        return
    await show_screen(cq, _ticket_view(t), reply_markup=ticket_kb(t))
    await cq.answer()


@dp.callback_query(F.data.startswith("tclose:"))
async def cb_ticket_close(cq: CallbackQuery):
    tid = int(cq.data.split(":", 1)[1])
    t = db.get_ticket(tid)
    if not t or (t["user_id"] != cq.from_user.id and cq.from_user.id != OWNER_ID):
        await cq.answer("Тикет не найден", show_alert=True)
        return
    db.set_status(tid, "closed")
    await show_screen(cq, _ticket_view(db.get_ticket(tid)), reply_markup=ticket_kb(db.get_ticket(tid)))
    await cq.answer("Тикет закрыт ✅")
    if OWNER_ID and cq.from_user.id != OWNER_ID:
        try:
            await cq.bot.send_message(OWNER_ID, f"✅ Тикет #{tid} закрыт пользователем.")
        except Exception:
            pass


# список открытых тикетов — команда для владельца
@dp.message(Command("tickets"))
async def cmd_tickets(message: Message):
    if message.from_user.id != OWNER_ID:
        # пользователю показываем его тикеты обычным экраном
        await message.answer("🗂 <b>Мои тикеты</b>", reply_markup=tickets_kb(message.from_user.id))
        return
    rows = db.open_tickets()
    if not rows:
        await message.answer("Открытых тикетов нет 🎉")
        return
    lines = ["🟡 <b>Открытые тикеты:</b>", ""]
    for t in rows:
        who = f"@{t['username']}" if t["username"] else f"id{t['user_id']}"
        lines.append(f"#{t['id']} · {who} · {fmt_date(t['updated_at'])}")
    await message.answer("\n".join(lines))


# закрытие тикета владельцем: /close реплаем на сообщение тикета
@dp.message(Command("close"))
async def cmd_close(message: Message):
    if message.from_user.id != OWNER_ID or not message.reply_to_message:
        return
    m = re.search(r"#(\d+)", message.reply_to_message.text or "")
    t = db.get_ticket(int(m.group(1))) if m else None
    if not t:
        await message.answer("Не вижу номера тикета в сообщении, на которое ответ.")
        return
    db.set_status(t["id"], "closed")
    await message.answer(f"✅ Тикет #{t['id']} закрыт.")
    try:
        await message.bot.send_message(t["user_id"], faq.TICKET_CLOSED_USER.format(tid=t["id"]))
    except Exception:
        pass


# ============ Живые сообщения: создание тикетов и ответы ============
@dp.message(F.text & ~F.text.startswith("/"))
async def on_text(message: Message):
    # --- ответ владельца реплаем на уведомление о тикете ---
    if OWNER_ID and message.from_user.id == OWNER_ID and message.reply_to_message:
        m = re.search(r"#(\d+)", message.reply_to_message.text or "")
        t = db.get_ticket(int(m.group(1))) if m else None
        if not t:
            await message.answer("Не вижу номера тикета в сообщении, на которое ответ.")
            return
        db.add_message(t["id"], from_owner=True, text=message.text)
        try:
            await message.bot.send_message(
                t["user_id"], faq.OWNER_REPLY.format(tid=t["id"], text=message.text)
            )
            await message.answer(f"Отправлено в тикет #{t['id']} ✅")
        except Exception:
            await message.answer(f"⚠️ Не удалось доставить ответ по тикету #{t['id']} "
                                 f"(пользователь мог заблокировать бота).")
        return

    # --- сообщение пользователя: тикет ---
    user = message.from_user
    _awaiting_ticket.discard(user.id)
    existing = db.open_ticket_of(user.id)
    if existing:
        db.add_message(existing["id"], from_owner=False, text=message.text)
        await _notify_owner(message.bot, existing["id"], user, message.text, appended=True)
        await message.answer(faq.TICKET_APPENDED.format(tid=existing["id"]))
    else:
        tid = db.create_ticket(user.id, user.username, message.text)
        await _notify_owner(message.bot, tid, user, message.text)
        await message.answer(faq.TICKET_CREATED.format(tid=tid))


async def main():
    if not BOT_TOKEN:
        raise SystemExit("Ошибка: задайте SUPPORT_BOT_TOKEN (токен от @BotFather).")
    db.init_db()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await bot.set_my_commands([
        BotCommand(command="start", description="Меню поддержки"),
        BotCommand(command="tickets", description="Мои тикеты"),
    ])
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    logging.info("IKK support bot запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
