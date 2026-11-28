"""CraftToken: free-only AI test with a local demo fallback; no payments."""
import asyncio
import logging
import os
import secrets
import time
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.filters import Command, CommandStart
from aiogram.exceptions import TelegramAPIError

from database import Database
from ai_provider import AIConfig, AIError, generate
from image_provider import ImageConfig, generate_image

logging.basicConfig(level=logging.INFO)
router = Router()
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")
db = Database(os.getenv("DB_PATH") or str(Path(os.getenv("DATA_DIR", ".")) / "bot_database.db"))
ai_config = None
image_config = None
image_drafts = {}
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


def available_routes(user_id):
    return ai_config.routes() if ai_config and user_id in ai_config.testers else {}


def model_name(config):
    return {"mistral-nemo": "Mistral Nemo", "gpt-oss-20b": "GPT-OSS-20B"}.get(config.model, config.model)


def chat_buttons(mode, user_id):
    routes = available_routes(user_id)
    slot = db.model_slot(user_id)
    rows = [[types.InlineKeyboardButton(text=("✓ " if slot == key else "") + model_name(config), callback_data=f"model:{key}")] for key, config in routes.items()]
    return types.InlineKeyboardMarkup(inline_keyboard=rows + [
        [types.InlineKeyboardButton(text=("✓ " if mode == key else "") + label, callback_data=f"mode:{key}") for key, label in MODES.items()],
        [types.InlineKeyboardButton(text="➕ Новый диалог", callback_data="dialog:new")],
    ])


def register(user):
    return db.ensure_user(user.id, user.username)


async def welcome(message):
    image_drafts.pop(message.from_user.id, None)
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
    user_id = message.from_user.id
    image_drafts.pop(user_id, None)
    mode = db.mode(user_id)
    routes = available_routes(user_id)
    selected = routes.get(db.model_slot(user_id))
    await message.answer(
        f"💬 AI-диалоги\nРежим: {MODES[mode]}\n\n"
        + (f"Модель: {model_name(selected)}\n/ai_on — включить, /ai_off — выключить." if selected else "Выбери доступную модель." if routes else "Демонстрационный режим: бесплатный тест интерфейса."),
        reply_markup=chat_buttons(mode, user_id),
    )


@router.callback_query(F.data.in_({"mode:fast", "mode:creative"}))
async def select_mode(callback: types.CallbackQuery):
    register(callback.from_user)
    mode = callback.data.split(":")[1]
    db.set_mode(callback.from_user.id, mode)
    await callback.answer(f"Выбран {MODES[mode]}")
    buttons = chat_buttons(mode, callback.from_user.id)
    if callback.message.reply_markup != buttons:
        await callback.message.edit_reply_markup(reply_markup=buttons)


@router.callback_query(F.data.in_({"model:primary", "model:gpt"}))
async def select_model(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    register(callback.from_user)
    slot = callback.data.split(":")[1]
    config = available_routes(user_id).get(slot)
    if not config or user_id in busy:
        await callback.answer("Модель недоступна или выполняется запрос.", show_alert=True)
        return
    changed = db.select_model(user_id, slot)
    await callback.answer(f"Выбрана {model_name(config)}")
    if not changed:
        return
    await callback.message.edit_text(
        f"💬 AI-диалоги\nМодель: {model_name(config)}\nРежим: {MODES[db.mode(user_id)]}\n\n"
        + "Новый диалог. Подтверди отправку данных командой /ai_on.",
        reply_markup=chat_buttons(db.mode(user_id), user_id),
    )


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
    uid = message.from_user.id
    image_drafts.pop(uid, None)
    if not image_config or uid not in image_config.testers:
        await message.answer("🎨 AI-студия\n\nГенерация изображений готовится к закрытому тесту. Редактирование фото пока недоступно.", reply_markup=menu())
        return
    if db.image_trial_used():
        await message.answer("🎨 AI-студия\n\nРазовый тест генерации уже использован. Проверяем расходы перед следующим запуском.", reply_markup=menu())
        return
    await message.answer("🎨 AI-студия\n\nFLUX 3 Image Klein · текст в изображение\n768 · 1:1 · одна картинка\n\nНапиши /image и описание картинки одним сообщением.\nНапример: /image Кот-космонавт на Луне\n\nЗатем подтверди платный тест. Редактирование фото пока недоступно.", reply_markup=menu())


@router.message(Command("image"))
async def image_prompt(message: types.Message):
    uid = message.from_user.id
    register(message.from_user)
    image_drafts.pop(uid, None)
    if not image_config or uid not in image_config.testers:
        await message.answer("Генерация пока доступна только участникам закрытого теста.")
        return
    if db.image_trial_used() or uid in busy:
        await message.answer("Тест уже использован или выполняется запрос. Проверь AI-студию.")
        return
    parts = (message.text or "").split(maxsplit=1)
    prompt = parts[1].strip() if len(parts) == 2 else ""
    if not 1 <= len(prompt) <= 1000:
        await message.answer("Напиши /image и описание из 1–1000 символов одним сообщением.")
        return
    nonce = secrets.token_hex(8)
    image_drafts[uid] = (nonce, prompt, time.monotonic() + 300)
    await message.answer(
        "Создать 1 изображение FLUX 3 Image Klein (768, 1:1)?\n\n"
        f"Описание: {prompt}\n\n"
        "Платный разовый тест: цена зависит от GetAPI; ориентир от 0,95 ₽. CT не списываются. "
        "Текст будет отправлен GetAPI и Black Forest Labs. Не включай личные данные. "
        "Даже неудачная попытка расходует тестовый лимит; автоматического повтора нет.",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
            types.InlineKeyboardButton(text="Создать (1 попытка)", callback_data=f"image:run:{nonce}"),
            types.InlineKeyboardButton(text="Отмена", callback_data=f"image:cancel:{nonce}"),
        ]]),
    )


@router.callback_query(F.data.startswith("image:"))
async def image_confirm(callback: types.CallbackQuery):
    uid = callback.from_user.id
    draft = image_drafts.get(uid)
    parts = callback.data.split(":")
    if len(parts) != 3 or not draft or draft[0] != parts[2] or time.monotonic() > draft[2]:
        await callback.answer("Подтверждение устарело. Открой AI-студию.", show_alert=True)
        return
    if parts[1] not in ("run", "cancel"):
        await callback.answer("Кнопка недоступна.", show_alert=True)
        return
    image_drafts.pop(uid, None)
    if parts[1] == "cancel":
        await callback.answer("Отменено")
        await callback.message.edit_text("Генерация отменена. Запрос не отправлен.")
        return
    if not image_config or uid not in image_config.testers or uid in busy or not db.reserve_image_trial(uid):
        await callback.answer("Тест недоступен или уже использован.", show_alert=True)
        return
    busy.add(uid)
    try:
        await callback.answer()
        await callback.message.edit_text("⏳ FLUX создаёт изображение. Повторных платных запросов не будет.")
        result = await generate_image(image_config, draft[1])
        photo = types.BufferedInputFile(result.data, filename=result.filename) if result.data is not None else result.url
        await callback.message.answer_photo(photo=photo, caption="🎨 FLUX 3 Image Klein · тестовая генерация", reply_markup=menu())
    except AIError as error:
        await callback.message.answer(str(error), reply_markup=menu())
    except TelegramAPIError:
        await callback.message.answer("Не удалось доставить картинку. Проверь журнал GetAPI; повторного запроса не будет.", reply_markup=menu())
    finally:
        busy.discard(uid)


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
    slot = db.model_slot(user_id)
    config = available_routes(user_id).get(slot)
    if not config:
        await message.answer("Реальный AI пока доступен только участникам закрытого теста.")
        return
    await message.answer(
        "Включить AI? Текст запроса и до 6 предыдущих AI-сообщений будут передаваться "
        f"GetAPI и поставщику {', '.join(config.providers)} (модель {model_name(config)}). Данные могут обрабатываться по их правилам. "
        "Не отправляй личные данные. Условия: https://getapi.ru/offer. Тест на ограниченном бюджете; CT не списываются.",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="Согласен, включить", callback_data=f"ai:enable:{slot}")],
            [types.InlineKeyboardButton(text="Отмена", callback_data="ai:disable")],
        ]),
    )


@router.callback_query(F.data.in_({"ai:enable", "ai:enable:primary", "ai:enable:gpt", "ai:disable"}))
async def toggle_ai(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    register(callback.from_user)
    enabled = callback.data != "ai:disable"
    slot = db.model_slot(user_id)
    expected = "primary" if callback.data == "ai:enable" else callback.data.rsplit(":", 1)[-1]
    if enabled and (expected != slot or slot not in available_routes(user_id)):
        await callback.answer("AI недоступен.", show_alert=True)
        return
    if user_id in busy:
        await callback.answer("Дождись завершения текущего запроса.", show_alert=True)
        return
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
    config = available_routes(user_id).get(db.model_slot(user_id))
    if config and db.ai_enabled(user_id):
        if user_id in busy:
            await message.answer("Подожди, предыдущий запрос ещё выполняется.")
            return
        if not db.reserve_ai_attempt(user_id, total_limit=4 if config.trial else 40, personal_limit=2 if config.trial else 10):
            await message.answer("Лимит тестовых запросов исчерпан. Попробуй позже.")
            return
        busy.add(user_id)
        try:
            await message.answer(f"⏳ {model_name(config)} готовит ответ…")
            context = db.ai_context(user_id)
            if config.trial:
                context = [{"role": x["role"], "content": x["content"][:1000]} for x in context]
            answer = await generate(config, mode, context, message.text)
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
    global ai_config, image_config
    ai_config = AIConfig.from_env()
    image_config = ImageConfig.from_env(ai_config)
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
