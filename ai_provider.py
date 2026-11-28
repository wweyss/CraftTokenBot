"""GetAPI integration with explicitly opted-in, capped trial routes."""
import os
import re
from dataclasses import dataclass, field, replace

import aiohttp

URL = "https://api.getapi.ru/v1/chat/completions"


class AIError(Exception):
    """A safe, user-facing error; never include credentials or raw API body."""


@dataclass(frozen=True)
class AIConfig:
    api_key: str = field(repr=False)
    model: str
    providers: tuple[str, ...]
    testers: frozenset[int]
    trial: bool = False
    gpt_provider: str = ""

    def routes(self):
        routes = {"primary": self}
        if self.gpt_provider and self.trial:
            routes["gpt"] = replace(self, model="gpt-oss-20b", providers=(self.gpt_provider,), gpt_provider="")
        return routes

    @classmethod
    def from_env(cls):
        key = os.getenv("GETAPI_API_KEY", "").strip()
        if not key:
            return None
        model = os.getenv("GETAPI_MODEL", "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,150}", model):
            raise ValueError("GETAPI_MODEL must be an explicit catalog model ID")
        providers = tuple(x.strip() for x in os.getenv("GETAPI_PROVIDERS", "").split(",") if x.strip())
        if not providers:
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
        gpt_provider = os.getenv("GETAPI_GPT_OSS_PROVIDER", "").strip()
        if gpt_provider and (gpt_provider != "darkbloom" or trial_value != "1"):
            raise ValueError("GPT-OSS trial requires darkbloom and GETAPI_TRIAL_ONLY=1")
        return cls(key, model, providers, testers, trial=trial_value == "1", gpt_provider=gpt_provider)


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
    is_gpt = config.model == "gpt-oss-20b"
    body = {
        "model": config.model,
        "messages": [{"role": "system", "content": instruction}, *context, {"role": "user", "content": prompt}],
        "max_tokens": 600 if is_gpt else 400,
        "stream": False,
        "provider": {
            "only": list(config.providers),
            "allow_fallbacks": False,
            "max_price": {"prompt": ceiling, "completion": 0.10 if is_gpt and config.trial else ceiling},
        },
    }
    if is_gpt:
        body["reasoning"] = {"effort": "low"}
    return body


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
