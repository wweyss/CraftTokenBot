"""CraftToken: product navigation and opt-in AI trials; no payments."""
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
from mini_app import parse_payload, safe_webapp_url

logging.basicConfig(level=logging.INFO)
router = Router()
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")
db = Database(os.getenv("DB_PATH") or str(Path(os.getenv("DATA_DIR", ".")) / "bot_database.db"))
ai_config = None
image_config = None
image_drafts = {}
preview_drafts = {}
busy = set()

CHAT = "💬 AI-диалоги"
STUDIO = "🎨 AI-студия"
HISTORY = "🗂 Моя история"
WALLET = "💎 Мой баланс"
HELP = "🛟 Помощь"
CATALOG = "🧠 Каталог моделей"
PROFILE = "👤 Мой профиль"
HOME = "🏠 Главное меню"
MODES = {"fast": "⚡ Быстрый", "creative": "✨ Творческий"}


def keyboard(*rows):
    return types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text=label) for label in row] for row in rows],
        resize_keyboard=True,
    )


def menu():
    markup = keyboard((CHAT, STUDIO), (CATALOG, HISTORY), (PROFILE, WALLET), (HELP,))
    app_url = safe_webapp_url(os.getenv("MINI_APP_URL", ""))
    if app_url:
        markup.keyboard.insert(0, [types.KeyboardButton(text="🚀 Открыть CraftToken", web_app=types.WebAppInfo(url=app_url))])
    return markup


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


def navigate(uid, section):
    db.set_section(uid, section)
    image_drafts.pop(uid, None)
    preview_drafts.pop(uid, None)


async def welcome(message):
    image_drafts.pop(message.from_user.id, None)
    preview_drafts.pop(message.from_user.id, None)
    balance = register(message.from_user)
    navigate(message.from_user.id, "home")
    await message.answer(
        f"⚡ CRAFTTOKEN AI\n\nОдна платформа для идей, диалогов и творчества.\n\n"
        f"💎 Твой баланс: {balance} CT\n"
        "\n💬 Диалоги — общение с AI\n🎨 Студия — творческие инструменты\n🧠 Каталог — модели и их возможности\n👤 Профиль — твоя история и баланс\n\n"
        + ("Закрытая бета · текстовый AI можно включить через /ai_on.\n" if available_routes(message.from_user.id) else "Закрытая бета · демонстрационный режим.\n")
        + "Тестовые CT не имеют денежной стоимости. Оплата отключена.",
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
    navigate(user_id, "chat")
    mode = db.mode(user_id)
    routes = available_routes(user_id)
    selected = routes.get(db.model_slot(user_id))
    await message.answer(
        f"💬 AI-диалоги\nРежим: {MODES[mode]}\n\n"
        + (f"Модель: {model_name(selected)}\nРеальный AI: {'включён' if db.ai_enabled(user_id) else 'выключен'}\n/ai_on — включить, /ai_off — выключить." if selected else "Выбери доступную модель." if routes else "Демонстрационный режим: бесплатный тест интерфейса."),
        reply_markup=chat_buttons(mode, user_id),
    )


def catalog_card(uid, category):
    routes = available_routes(uid)
    if category == "text":
        lines = ["🧠 Каталог · Текст\n", "Mistral Nemo — диалоги, краткие ответы, переводы. " + ("Закрытый тест." if any(r.model == "mistral-nemo" for r in routes.values()) else "Демонстрация."), "GPT-OSS-20B — задачи с логикой и программированием. " + ("Маршрут настроен, ожидает проверки." if any(r.model == "gpt-oss-20b" for r in routes.values()) else "Планируется.")]
        lines += ["\nВ разделе AI-диалоги можно выбрать подключённую модель."]
    elif category == "image":
        available = image_config and uid in image_config.testers and not db.image_trial_used(allow_retry=image_config.retry_once)
        lines = ["🧠 Каталог · Изображения\n", "FLUX 3 Image Klein — создание изображений. " + ("Разовый закрытый тест." if available else "Демонстрация; платная генерация приостановлена."), "Редактирование фото — разработка.", "\nОткрой AI-студию для бесплатной проверки интерфейса."]
    else:
        lines = ["🧠 Каталог · Видео и музыка\n", "Видео — в планах.", "Музыка — в планах.", "\nРеальная генерация ещё не подключена."]
    return "\n\n".join(lines)


def catalog_buttons(category):
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text=("✓ " if category == key else "") + label, callback_data=f"catalog:{key}") for key, label in (("text", "Текст"), ("image", "Фото"), ("media", "Видео / музыка"))],
    ])


@router.message(F.text == CATALOG)
@router.message(Command("models"))
async def catalog(message: types.Message):
    register(message.from_user)
    uid = message.from_user.id
    navigate(uid, "catalog")
    await message.answer(catalog_card(uid, "text"), reply_markup=catalog_buttons("text"))


@router.callback_query(F.data.in_({"catalog:text", "catalog:image", "catalog:media"}))
async def catalog_tab(callback: types.CallbackQuery):
    register(callback.from_user)
    category = callback.data.split(":")[1]
    navigate(callback.from_user.id, "catalog")
    text = catalog_card(callback.from_user.id, category)
    buttons = catalog_buttons(category)
    await callback.answer()
    if callback.message.text != text or callback.message.reply_markup != buttons:
        await callback.message.edit_text(text, reply_markup=buttons)


@router.message(F.text == PROFILE)
@router.message(Command("profile"))
async def profile(message: types.Message):
    user = message.from_user
    balance = register(user)
    navigate(user.id, "profile")
    stats = db.profile_stats(user.id)
    selected = available_routes(user.id).get(db.model_slot(user.id))
    live = bool(selected and db.ai_enabled(user.id))
    await message.answer(
        f"👤 Профиль CraftToken\n\n{user.full_name}\nID: {user.id}\n\n💎 Баланс: {balance} CT\n💬 Диалогов: {stats['dialogs']}\n🗂 Сохранённых запросов: {stats['requests']}\n\n"
        + (f"Модель: {model_name(selected)}\n" if selected else "Доступ: демонстрационный\n")
        + f"Текстовый AI: {'включён (закрытый тест)' if live else 'выключен'}\n\nCT — тестовые кредиты. Оплата ещё не подключена.",
        reply_markup=menu(),
    )


@router.callback_query(F.data.in_({"mode:fast", "mode:creative"}))
async def select_mode(callback: types.CallbackQuery):
    register(callback.from_user)
    navigate(callback.from_user.id, "chat")
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
    navigate(user_id, "chat")
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
    navigate(callback.from_user.id, "chat")
    db.new_dialog(callback.from_user.id)
    await callback.answer("Новый диалог создан")
    await callback.message.answer("Новый диалог. Отправь текст для теста.", reply_markup=menu())


@router.message(F.text == STUDIO)
async def studio(message: types.Message):
    register(message.from_user)
    uid = message.from_user.id
    navigate(uid, "studio")
    live = image_config and uid in image_config.testers and not db.image_trial_used(allow_retry=image_config.retry_once)
    await message.answer(
        "🎨 CRAFTTOKEN STUDIO\n\nСоздавай, экспериментируй, собирай свои идеи.\n\n"
        + ("Генерация: доступен отдельный платный тест.\n" if live else "Режим демонстрации · внешние запросы отключены.\n")
        + "Выбери инструмент:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="🖼 Создать изображение", callback_data="studio:draw")],
            [types.InlineKeyboardButton(text="🪄 Редактировать фото", callback_data="studio:edit")],
            [types.InlineKeyboardButton(text="🎬 Видео", callback_data="studio:video"), types.InlineKeyboardButton(text="🎵 Музыка", callback_data="studio:music")],
        ]),
    )


@router.callback_query(F.data.in_({"studio:draw", "studio:edit", "studio:video", "studio:music"}))
async def studio_tool(callback: types.CallbackQuery):
    uid = callback.from_user.id
    register(callback.from_user)
    key = callback.data.split(":")[1]
    navigate(uid, "studio")
    await callback.answer()
    if key == "draw":
        await callback.message.answer("🖼 Создание изображения\n\nПросто напиши идею одним сообщением.\nНапример: Город будущего на берегу моря\n\nВыберешь формат и увидишь параметры будущей генерации. Это бесплатная демонстрация, без отправки данных в AI.", reply_markup=menu())
    else:
        name = {"edit": "Редактирование фото", "video": "Генерация видео", "music": "Создание музыки"}[key]
        await callback.message.answer(f"{name}\n\nЭтот инструмент в разработке. Файлы и запросы пока не отправляются внешним сервисам.", reply_markup=menu())


@router.message(Command("preview"))
async def preview_prompt(message: types.Message):
    parts = (message.text or "").split(maxsplit=1)
    await preview_text(message, parts[1] if len(parts) == 2 else "")


async def preview_text(message: types.Message, prompt: str):
    uid = message.from_user.id
    register(message.from_user)
    navigate(uid, "studio")
    prompt = prompt.strip()
    if not 1 <= len(prompt) <= 1000:
        await message.answer("Напиши /preview и описание из 1–1000 символов одним сообщением.")
        return
    nonce = secrets.token_hex(8)
    preview_drafts[uid] = (nonce, prompt, time.monotonic() + 300)
    await message.answer("🖼 Настройка изображения · ДЕМО\n\nВыбери формат:", reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text=label, callback_data=f"preview:{key}:{nonce}") for key, label in (("square", "1:1"), ("wide", "16:9"), ("tall", "9:16"))
    ]] + [[types.InlineKeyboardButton(text="Отмена", callback_data=f"preview:cancel:{nonce}")]]))


@router.callback_query(F.data.startswith("preview:"))
async def preview_format(callback: types.CallbackQuery):
    uid = callback.from_user.id
    draft = preview_drafts.get(uid)
    parts = callback.data.split(":")
    ratios = {"square": "1:1", "wide": "16:9", "tall": "9:16"}
    if len(parts) != 3 or not draft or draft[0] != parts[2] or time.monotonic() > draft[2]:
        await callback.answer("Демонстрация устарела.", show_alert=True)
        return
    if parts[1] not in {*ratios, "cancel"}:
        await callback.answer("Кнопка недоступна.", show_alert=True)
        return
    preview_drafts.pop(uid, None)
    await callback.answer()
    if parts[1] == "cancel":
        await callback.message.edit_text("Демонстрация отменена.")
        return
    await callback.message.edit_text(f"✅ Параметры подготовлены · ДЕМО\n\nИдея: {draft[1]}\nФормат: {ratios[parts[1]]}\nМодель: FLUX 3 Image Klein (запланирована)\n\nИзображение не создано. Данные не отправлялись, CT не списаны.")


@router.message(Command("image"))
async def image_prompt(message: types.Message):
    uid = message.from_user.id
    register(message.from_user)
    image_drafts.pop(uid, None)
    if not image_config or uid not in image_config.testers:
        await preview_prompt(message)
        return
    if db.image_trial_used(allow_retry=image_config.retry_once) or uid in busy:
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
    if not image_config or uid not in image_config.testers or uid in busy or not db.reserve_image_trial(uid, allow_retry=image_config.retry_once):
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
    navigate(message.from_user.id, "wallet")
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
    navigate(message.from_user.id, "history")
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
    navigate(message.from_user.id, "help")
    contact = os.getenv("SUPPORT_CONTACT", "").strip()
    await message.answer(
        "🛟 Помощь\n\n/menu — главное меню\n/balance — баланс\n"
        "/clear_history — удалить историю из базы бота\n\n"
        "/ai_on — включить реальный AI (для тестировщиков)\n/ai_off — выключить AI\n"
        "/models — каталог\n/profile — профиль\n/preview — тест интерфейса изображений\n"
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
    navigate(user_id, "chat")
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


@router.message(F.web_app_data)
async def mini_app_data(message: types.Message):
    """Accept only navigation/demo data. Never trust client-side balance or prices."""
    try:
        payload = parse_payload(message.web_app_data.data)
        action = payload["action"]
        if action == "open_chat":
            await chat(message)
            return
        if action == "open_profile":
            await profile(message)
            return
        if action == "preview":
            prompt, ratio = payload["prompt"], payload["ratio"]
            register(message.from_user)
            navigate(message.from_user.id, "studio")
            await message.answer(f"🎨 Черновик из CraftToken Mini App\n\nИдея: {prompt.strip()}\nФормат: {ratio}\n\nДЕМО: изображение не создано, внешних запросов и списаний нет.", reply_markup=menu())
            return
    except (ValueError, TypeError, UnicodeError):
        pass
    await message.answer("Данные приложения не приняты. Открой CraftToken снова.", reply_markup=menu())


@router.message(F.text)
async def demo(message: types.Message):
    register(message.from_user)
    user_id = message.from_user.id
    section = db.section(user_id)
    if section == "studio":
        await preview_text(message, message.text)
        return
    if section != "chat":
        await message.answer("Выбери 💬 AI-диалоги для общения или 🎨 AI-студию для творчества.", reply_markup=menu())
        return
    if len(message.text) > 3000:
        await message.answer("Для теста отправь не более 3000 символов.")
        return
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
    # Product development: pause paid images until explicitly enabled again.
    image_config = ImageConfig.from_env(ai_config) if os.getenv("GETAPI_IMAGE_LIVE_ENABLED") == "1" else None
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
