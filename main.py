"""CraftToken: free-only AI test with a local demo fallback; no payments."""
import asyncio
import logging
import os

from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.filters import Command, CommandStart

from database import Database
from ai_provider import AIConfig, AIError, generate

logging.basicConfig(level=logging.INFO)
router = Router()
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")
db = Database(os.getenv("DB_PATH", "bot_database.db"))
ai_config = None
busy = set()

CHAT = "💬 AI-диалоги"
STUDIO = "🎨 AI-студия"
HISTORY = "🗂 Моя история"
WALLET = "💎 Мой баланс"
HELP = "🛟 Помощь"
HOME = "🏠 Главное меню"
MODES = {"fast": "⚡ Быстрый", "creative": "✨ Творческий"}


def keyboard(*rows):
    return types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text=label) for label in row] for row in rows],
        resize_keyboard=True,
    )


def menu():
    return keyboard((CHAT, STUDIO), (HISTORY, WALLET), (HELP,))


def chat_buttons(mode):
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text=("✓ " if mode == key else "") + label, callback_data=f"mode:{key}") for key, label in MODES.items()],
        [types.InlineKeyboardButton(text="➕ Новый диалог", callback_data="dialog:new")],
    ])


def register(user):
    return db.ensure_user(user.id, user.username)


async def welcome(message):
    balance = register(message.from_user)
    await message.answer(
        f"⚡ CraftToken AI\n\nТвой персональный набор AI-инструментов.\n"
        f"Баланс: {balance} CT\n\n"
        + ("Тестовая версия. Для подключения реального AI используй /ai_on.\n" if ai_config and message.from_user.id in ai_config.testers else "Тестовая версия: демонстрационный режим.\n")
        + "Платежи отключены, CT не имеют денежной стоимости.\n"
        "Выбери раздел или отправь текст для теста.",
        reply_markup=menu(),
    )


@router.message(CommandStart())
@router.message(Command("menu"))
@router.message(F.text == HOME)
async def start(message: types.Message):
    await welcome(message)


@router.message(F.text == CHAT)
async def chat(message: types.Message):
    register(message.from_user)
    mode = db.mode(message.from_user.id)
    await message.answer(
        f"💬 AI-диалоги\nРежим: {MODES[mode]}\n\n"
        + ("Тестовый маршрут настроен. /ai_on — включить, /ai_off — выключить." if ai_config and message.from_user.id in ai_config.testers else "Демонстрационный режим: бесплатный тест интерфейса."),
        reply_markup=chat_buttons(mode),
    )


@router.callback_query(F.data.in_({"mode:fast", "mode:creative"}))
async def select_mode(callback: types.CallbackQuery):
    register(callback.from_user)
    mode = callback.data.split(":")[1]
    db.set_mode(callback.from_user.id, mode)
    await callback.answer(f"Выбран {MODES[mode]}")
    if callback.message.reply_markup != chat_buttons(mode):
        await callback.message.edit_reply_markup(reply_markup=chat_buttons(mode))


@router.callback_query(F.data == "dialog:new")
async def new_dialog(callback: types.CallbackQuery):
    register(callback.from_user)
    if callback.from_user.id in busy:
        await callback.answer("Дождись завершения запроса.", show_alert=True)
        return
    db.new_dialog(callback.from_user.id)
    await callback.answer("Новый диалог создан")
    await callback.message.answer("Новый диалог. Отправь текст для теста.", reply_markup=menu())


@router.message(F.text == STUDIO)
async def studio(message: types.Message):
    register(message.from_user)
    await message.answer(
        "🎨 AI-студия\n\nВ разработке: генерация изображений и редактирование фото. "
        "В текущей версии файлы не отправляются внешним сервисам.",
        reply_markup=menu(),
    )


@router.message(F.text == WALLET)
@router.message(Command("balance"))
async def wallet(message: types.Message):
    balance = register(message.from_user)
    entries = db.ledger(message.from_user.id)
    operations = "\n".join(f"{r['amount']:+d} CT — {r['reason']}" for r in entries)
    await message.answer(
        f"💎 Мой баланс\n\nДоступно: {balance} CT\nTelegram ID: {message.from_user.id}\n"
        "Тестовые кредиты. Пополнение за деньги отключено.\n\n"
        "Последние операции:\n" + (operations or "Нет новых операций."),
        reply_markup=menu(),
    )


@router.message(F.text == HISTORY)
async def history(message: types.Message):
    register(message.from_user)
    prompts = db.recent_prompts(message.from_user.id)
    lines = [f"• {r['content'][:180]}" for r in prompts]
    await message.answer(
        "🗂 Последние запросы\n\n" + ("\n".join(lines) if lines else "Пока пусто.") +
        "\n\nУдалить историю: /clear_history", reply_markup=menu(),
    )


@router.message(Command("clear_history"))
async def clear_history(message: types.Message):
    register(message.from_user)
    await message.answer("Удалить все сохранённые тексты твоих диалогов?", reply_markup=types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(text="Удалить", callback_data="history:delete"),
                          types.InlineKeyboardButton(text="Отмена", callback_data="history:cancel")]]
    ))


@router.callback_query(F.data.in_({"history:delete", "history:cancel"}))
async def confirm_clear(callback: types.CallbackQuery):
    register(callback.from_user)
    if callback.data == "history:delete":
        if callback.from_user.id in busy:
            await callback.answer("Дождись завершения запроса.", show_alert=True)
            return
        db.clear_history(callback.from_user.id)
        text = "История удалена. Баланс не изменён."
    else:
        text = "Удаление отменено."
    await callback.answer()
    await callback.message.edit_text(text)


@router.message(F.text == HELP)
@router.message(Command("help"))
async def help_message(message: types.Message):
    register(message.from_user)
    contact = os.getenv("SUPPORT_CONTACT", "").strip()
    await message.answer(
        "🛟 Помощь\n\n/menu — главное меню\n/balance — баланс\n"
        "/clear_history — удалить историю из базы бота\n\n"
        "/ai_on — включить реальный AI (для тестировщиков)\n/ai_off — выключить AI\n"
        "Запросы сохраняются в базе. При включённом AI текст и контекст отправляются GetAPI и его провайдеру. Не отправляй пароли и личные данные. "
        "Удаление в боте не удаляет сообщения из Telegram.\n" +
        (f"Поддержка: {contact}" if contact else "Контакт поддержки ещё не настроен."),
        reply_markup=menu(),
    )


@router.message(Command("add"))
async def admin_add(message: types.Message):
    admin_ids = {x.strip() for x in os.getenv("ADMIN_IDS", os.getenv("ADMIN_ID", "")).split(",")}
    if str(message.from_user.id) not in admin_ids:
        await message.answer("Команда недоступна.")
        return
    try:
        _, raw_id, raw_amount = message.text.split()
        user_id, amount = int(raw_id), int(raw_amount)
        if not (0 < user_id < 2**63 and 0 < amount <= 1_000_000):
            raise ValueError
    except ValueError:
        await message.answer("Формат: /add ID КОЛИЧЕСТВО (1–1000000).")
        return
    if db.change_balance(user_id, amount, "Начисление администратора"):
        await message.answer(f"Начислено {amount} CT пользователю {user_id}.")
    else:
        await message.answer("Пользователь не найден.")


@router.message(Command("ai_on"))
async def enable_ai(message: types.Message):
    user_id = message.from_user.id
    register(message.from_user)
    if not ai_config or user_id not in ai_config.testers:
        await message.answer("Реальный AI пока доступен только участникам закрытого теста.")
        return
    await message.answer(
        "Включить AI? Текст запроса и до 6 предыдущих AI-сообщений будут передаваться "
        "GetAPI и выбранному поставщику. Данные могут обрабатываться по их правилам. "
        "Не отправляй личные данные. Условия: https://getapi.ru/offer. Тест на ограниченном бюджете; CT не списываются.",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="Согласен, включить", callback_data="ai:enable")],
            [types.InlineKeyboardButton(text="Отмена", callback_data="ai:disable")],
        ]),
    )


@router.callback_query(F.data.in_({"ai:enable", "ai:disable"}))
async def toggle_ai(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    register(callback.from_user)
    if callback.data == "ai:enable" and (not ai_config or user_id not in ai_config.testers):
        await callback.answer("AI недоступен.", show_alert=True)
        return
    if user_id in busy:
        await callback.answer("Дождись завершения текущего запроса.", show_alert=True)
        return
    enabled = callback.data == "ai:enable"
    db.set_ai_enabled(user_id, enabled)
    await callback.answer()
    await callback.message.edit_text("AI включён. Отправь сообщение." if enabled else "AI выключен.")


@router.message(Command("ai_off"))
async def disable_ai(message: types.Message):
    user_id = message.from_user.id
    register(message.from_user)
    if user_id in busy:
        await message.answer("Дождись завершения текущего запроса.")
        return
    db.set_ai_enabled(user_id, False)
    await message.answer("AI выключен. Запросы больше не отправляются внешнему провайдеру.")


@router.message(F.text.startswith("/"))
async def unknown_command(message: types.Message):
    await message.answer("Неизвестная команда. Используй /menu.")


@router.message(F.text)
async def demo(message: types.Message):
    register(message.from_user)
    if len(message.text) > 3000:
        await message.answer("Для теста отправь не более 3000 символов.")
        return
    user_id = message.from_user.id
    mode = db.mode(user_id)
    if ai_config and user_id in ai_config.testers and db.ai_enabled(user_id):
        if user_id in busy:
            await message.answer("Подожди, предыдущий запрос ещё выполняется.")
            return
        if not db.reserve_ai_attempt(user_id, total_limit=4 if ai_config.trial else 40, personal_limit=2 if ai_config.trial else 10):
            await message.answer("Лимит тестовых запросов исчерпан. Попробуй позже.")
            return
        busy.add(user_id)
        try:
            await message.answer("⏳ Запрос отправлен в AI…")
            context = db.ai_context(user_id)
            if ai_config.trial:
                context = [{"role": x["role"], "content": x["content"][:1000]} for x in context]
            answer = await generate(ai_config, mode, context, message.text)
            # Telegram's 4096-unit message limit; split conservatively for emoji.
            for i in range(0, len(answer), 1800):
                await message.answer(answer[i:i+1800], reply_markup=menu())
            db.save_exchange(user_id, message.text, answer, kind="ai")
        except AIError as error:
            await message.answer(str(error), reply_markup=menu())
        finally:
            busy.discard(user_id)
        return
    reply = (
        f"🧪 Демо · {MODES[mode]}\n\nЗапрос принят. Это тест интерфейса, "
        "а не ответ нейросети. Настоящий AI пока не подключён. CT не списаны."
    )
    await message.answer(reply, reply_markup=menu())
    db.save_exchange(message.from_user.id, message.text, reply)


@router.message()
async def other_media(message: types.Message):
    await message.answer("В этой тестовой версии принимается только текст.", reply_markup=menu())


@router.callback_query()
async def old_button(callback: types.CallbackQuery):
    await callback.answer("Кнопка устарела. Открой /menu.", show_alert=True)


async def main():
    global ai_config
    ai_config = AIConfig.from_env()
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("Set BOT_TOKEN in the environment")
    db.init()
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    async with Bot(token=token) as bot:
        await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
