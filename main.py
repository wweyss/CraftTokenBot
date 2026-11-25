import os
import asyncio
import logging
import aiosqlite
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
# Укажите ваш цифровой Telegram ID (можно узнать в боте @userinfobot)
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

DB_NAME = "bot_database.db"

# --- РАБОТА С БАЗОЙ ДАННЫХ ---
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                tokens INTEGER DEFAULT 100
            )
        """)
        await db.commit()

async def get_or_create_user(user_id: int, username: str):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT tokens FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if row is None:
            # Даем 100 бесплатных токенов при первом входе
            await db.execute(
                "INSERT INTO users (user_id, username, tokens) VALUES (?, ?, ?)",
                (user_id, username, 100)
            )
            await db.commit()
            return 100
        return row[0]

async def update_tokens(user_id: int, amount: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE users SET tokens = tokens + ? WHERE user_id = ?",
            (amount, user_id)
        )
        await db.commit()

# --- КЛАВИАТУРЫ ---
def main_menu_kb():
    builder = InlineKeyboardBuilder()
    builder.button(text="💳 Купить токены", callback_data="buy_menu")
    builder.button(text="👤 Мой баланс", callback_data="profile")
    builder.adjust(1)
    return builder.as_markup()

# --- ХЕНДЛЕРЫ (РЕАКЦИИ БОТА) ---
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    tokens = await get_or_create_user(message.from_user.id, message.from_user.username)
    await message.answer(
        f"👋 Привет, {message.from_user.first_name}!\n\n"
        f"Я AI-бот. Просто напиши мне любой вопрос, и я отвечу с помощью нейросети.\n"
        f"🎁 Ваш текущий баланс: <b>{tokens} токенов</b>.",
        reply_markup=main_menu_kb(),
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "profile")
async def show_profile(callback: types.CallbackQuery):
    tokens = await get_or_create_user(callback.from_user.id, callback.from_user.username)
    await callback.message.edit_text(
        f"👤 <b>Личный кабинет</b>\n\n"
        f"🆔 Ваш ID: <code>{callback.from_user.id}</code>\n"
        f"💎 Баланс: <b>{tokens} токенов</b>",
        reply_markup=main_menu_kb(),
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "buy_menu")
async def show_buy_menu(callback: types.CallbackQuery):
    builder = InlineKeyboardBuilder()
    builder.button(text="1 000 токенов — 100 ₽", callback_data="test_pay_1000")
    builder.button(text="5 000 токенов — 400 ₽", callback_data="test_pay_5000")
    builder.button(text="⬅️ Назад", callback_data="back_main")
    builder.adjust(1)

    await callback.message.edit_text(
        "💳 <b>Выберите пакет токенов:</b>\n\n"
        "<i>(Пока работает в тестовом режиме — начисляет без оплаты)</i>",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "back_main")
async def back_to_main(callback: types.CallbackQuery):
    tokens = await get_or_create_user(callback.from_user.id, callback.from_user.username)
    await callback.message.edit_text(
        f"Главное меню.\n💎 Ваш баланс: <b>{tokens} токенов</b>.",
        reply_markup=main_menu_kb(),
        parse_mode="HTML"
    )
    await callback.answer()

# Тестовое пополнение баланса
@dp.callback_query(F.data.startswith("test_pay_"))
async def process_test_pay(callback: types.CallbackQuery):
    amount = int(callback.data.split("_")[2])
    await update_tokens(callback.from_user.id, amount)
    new_balance = await get_or_create_user(callback.from_user.id, callback.from_user.username)
    
    await callback.message.edit_text(
        f"✅ Успешно начислено <b>{amount} токенов</b>!\n"
        f"💎 Текущий баланс: <b>{new_balance} токенов</b>.",
        reply_markup=main_menu_kb(),
        parse_mode="HTML"
    )
    await callback.answer()

# Админ-команда для ручной выдачи: /add ID КОЛИЧЕСТВО
@dp.message(Command("add"))
async def admin_add_tokens(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        _, user_id, amount = message.text.split()
        await update_tokens(int(user_id), int(amount))
        await message.answer(f"✅ Пользователю {user_id} начислено {amount} токенов.")
    except Exception:
        await message.answer("❌ Ошибка формата. Используйте: `/add 123456789 500`", parse_mode="Markdown")

# Заглушка для будущего чата с нейросетью
@dp.message(F.text)
async def handle_ai_request(message: types.Message):
    tokens = await get_or_create_user(message.from_user.id, message.from_user.username)
    if tokens < 10:
        await message.answer("❌ Недостаточно токенов! Пополните баланс в меню.", reply_markup=main_menu_kb())
        return
    
    # Пока просто списываем 10 токенов за сообщение
    await update_tokens(message.from_user.id, -10)
    await message.answer(
        f"🤖 <i>[Тут скоро будет ответ нейросети на ваш запрос: «{message.text}»]</i>\n\n"
        f"Списано: 10 токенов. Остаток: <b>{tokens - 10}</b>",
        parse_mode="HTML"
    )

async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
