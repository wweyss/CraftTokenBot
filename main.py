import os
import asyncio
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.utils.keyboard import InlineKeyboardBuilder

logging.basicConfig(level=logging.INFO)

# Бот автоматически возьмет токен из настроек BotHost
BOT_TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Временный "склад" токенов (позже заменим на базу данных)
TOKENS_STOCK = [
    "TOKEN-AAAA-1111-XXXX",
    "TOKEN-BBBB-2222-YYYY",
    "TOKEN-CCCC-3333-ZZZZ"
]

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    builder = InlineKeyboardBuilder()
    builder.button(text="Купить токен (100 ₽)", callback_data="buy_token")
    
    await message.answer(
        f"Привет, {message.from_user.first_name}!\n"
        f"В наличии токенов: {len(TOKENS_STOCK)} шт.\n"
        f"Нажмите кнопку ниже для покупки:",
        reply_markup=builder.as_markup()
    )

@dp.callback_query(F.data == "buy_token")
async def process_buy(callback: types.CallbackQuery):
    if not TOKENS_STOCK:
        await callback.answer("Токены закончились!", show_alert=True)
        return

    issued_token = TOKENS_STOCK.pop(0)
    
    await callback.message.answer(
        f"✅ Оплата прошла успешно (тестовый режим)!\n"
        f"Ваш токен: <code>{issued_token}</code>",
        parse_mode="HTML"
    )
    await callback.answer()

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
