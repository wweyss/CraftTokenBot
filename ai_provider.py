            raise ValueError("GETAPI_PROVIDERS must name approved model providers")
        try:
            testers = frozenset(int(x) for x in os.getenv("TEST_USER_IDS", "").split(",") if x.strip())
        except ValueError:
            raise ValueError("TEST_USER_IDS must be comma-separated Telegram IDs") from None
        if not 1 <= len(testers) <= 4 or any(x <= 0 or x >= 2**63 for x in testers):
            raise ValueError("TEST_USER_IDS must contain 1–4 valid Telegram IDs")
        # Nonzero upstream pricing is deliberately disabled by default.
        trial_value = os.getenv("GETAPI_TRIAL_ONLY", "0")
        if trial_value not in {"0", "1"}:
            raise ValueError("GETAPI_TRIAL_ONLY must be 0 or 1")
        return cls(key, model, providers, testers, trial=trial_value == "1")


def request_body(config: AIConfig, mode: str, context: list[dict], prompt: str):
    identity = (
        "Ты универсальный текстовый AI-ассистент в Telegram-сервисе CraftToken AI. "
        "CraftToken — название сервиса, а не задача по созданию токенов или криптовалют. "
        "Помогай с вопросами, текстами, идеями, переводом и объяснениями. "
        "Не утверждай, что можешь выполнять внешние действия, создавать файлы, "
        "генерировать изображения или пользоваться инструментами, которых у тебя нет. "
        "Отвечай на языке пользователя. "
    )
    instruction = (
        identity + "Отвечай ясно, кратко и по существу."
        if mode == "fast" else
        identity + "Предлагай оригинальные идеи и варианты, когда это полезно."
    )
    ceiling = 0.05 if config.trial else 0
    return {
        "model": config.model,
        "messages": [{"role": "system", "content": instruction}, *context, {"role": "user", "content": prompt}],
        "max_tokens": 400,
        "stream": False,
        "provider": {
            "only": list(config.providers),
            "allow_fallbacks": False,
            "max_price": {"prompt": ceiling, "completion": ceiling},
        },
    }


async def generate(config: AIConfig, mode: str, context: list[dict], prompt: str) -> str:
    timeout = aiohttp.ClientTimeout(total=60, connect=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                URL,
                headers={"Authorization": f"Bearer {config.api_key}"},
                json=request_body(config, mode, context, prompt),
                allow_redirects=False,
            ) as response:
                if response.status == 429:
                    raise AIError("Лимит провайдера временно исчерпан. Попробуй позже.")
                if response.status in (401, 402, 403):
                    raise AIError("AI недоступен: проверь ключ, условия доступа и тестовый баланс.")
                if response.status != 200:
                    raise AIError("Выбранная модель или ценовой маршрут недоступны. Попробуй позже.")
                data = await response.json()
                choice = data["choices"][0]
                if data.get("error") or choice.get("error") or choice.get("finish_reason") == "error":
                    raise AIError("Провайдер не смог выполнить запрос.")
                result = choice["message"]["content"]
                if not isinstance(result, str) or not result.strip():
                    raise AIError("Модель вернула пустой ответ.")
                return result.strip()
    except (aiohttp.ClientError, TimeoutError, ValueError, KeyError, IndexError, TypeError):
        raise AIError("Не удалось получить ответ от AI. Попробуй позже.") from None
