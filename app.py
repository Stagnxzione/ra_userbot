# runner.py
import os
import asyncio

from userbot import build_chat_factory, Settings as UBSettings
from chat_factory_adapter import ChatFactoryAdapter
from regular_bot import build_application  # ваш файл regular_bot.py

async def main():
    # Настройки userbot из .env
    ub_settings = UBSettings(
        API_ID=int(os.environ["API_ID"]),
        API_HASH=os.environ["API_HASH"],
        USERBOT_SESSION=os.environ["USERBOT_SESSION"],
        MANAGED_BOT_USERNAME=os.environ["MANAGED_BOT_USERNAME"],  # например "@my_ptb_bot"
    )

    # 1) строим фабрику Telethon
    telethon_factory = await build_chat_factory(ub_settings)

    # 2) адаптируем интерфейс под regular_bot.py
    adapter = ChatFactoryAdapter(telethon_factory, ub_settings.MANAGED_BOT_USERNAME)

    # 3) собираем PTB-приложение, прокидывая фабрику ДО initialize()
    app = build_application(chat_factory=adapter)

    # 4) старт PTB v20-совместимый
    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    try:
        await asyncio.Event().wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await telethon_factory.aclose()

if __name__ == "__main__":
    asyncio.run(main())
