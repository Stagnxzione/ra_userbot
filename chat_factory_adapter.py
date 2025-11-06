# chat_factory_adapter.py
class ChatFactoryAdapter:
    """
    Приводит вашу userbot-фабрику к интерфейсу, которого ждёт regular_bot.py:
    ожидается метод: create_group_with_bot(title: str) -> int
    """
    def __init__(self, telethon_factory, bot_username: str):
        self._factory = telethon_factory
        self._bot_username = bot_username

    async def create_group_with_bot(self, title: str) -> int:
        # делегируем в userbot.ChatFactory.create_chat(...)
        return await self._factory.create_chat(title=title, bot_username=self._bot_username)
