from __future__ import annotations
import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession

API_ID = int(input("API_ID: ").strip())
API_HASH = input("API_HASH: ").strip()

async def main():
    async with TelegramClient(StringSession(), API_ID, API_HASH) as client:
        await client.connect()
        if not await client.is_user_authorized():
            await client.send_code_request(input("Телефон (в международном формате \"+\"): ").strip())
            await client.sign_in(code=input("Код из Telegram: ").strip())
        print("\n----USERBOT_SESSION (сохранить в .env) ----\n")
        print(client.session.save())
        print("\n-----------------------------------------------\n")

if __name__ == "__main__":
    asyncio.run(main())
