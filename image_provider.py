"""One explicitly enabled, single-image FLUX trial via GetAPI."""
import base64
import binascii
import ipaddress
import json
import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import aiohttp

from ai_provider import AIConfig, AIError

URL = "https://api.getapi.ru/v1/images/generations"
MODEL = "flux-3-image-klein"
PROVIDER = "black-forest-labs"
MAX_IMAGE = 10 * 1024 * 1024
MAX_JSON = 14 * 1024 * 1024


@dataclass(frozen=True)
class ImageConfig:
    key: str = field(repr=False)
    testers: frozenset[int]
    retry_once: bool = False

    @classmethod
    def from_env(cls, text_config: AIConfig | None):
        key = os.getenv("GETAPI_IMAGE_API_KEY", "").strip()
        if not key:
            return None
        if not text_config or not text_config.trial or key == text_config.api_key:
            raise ValueError("Image trial requires trial mode and its own limited GetAPI key")
        return cls(key, text_config.testers, os.getenv("GETAPI_IMAGE_RETRY_ONCE", "0") == "1")


@dataclass(frozen=True)
class ImageResult:
    data: bytes | None = None
    url: str | None = None
    filename: str = "crafttoken.png"


def request_body(prompt: str):
    if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 1000:
        raise AIError("Описание должно содержать от 1 до 1000 символов.")
    return {
        "model": MODEL,
        "prompt": prompt.strip(),
        "resolution": "768",
        "aspect_ratio": "1:1",
        "n": 1,
        "provider": {"only": [PROVIDER], "allow_fallbacks": False},
    }


def parse_result(payload):
    if payload.get("error") or len(payload.get("data", [])) != 1:
        raise AIError("Сервис не вернул одно готовое изображение. Проверь историю расходов.")
    item = payload["data"][0]
    raw = item.get("b64_json")
    if isinstance(raw, str) and raw:
        if len(raw) > (MAX_IMAGE * 4 // 3 + 8):
            raise AIError("Изображение слишком большое для теста.")
        try:
            data = base64.b64decode(raw, validate=True)
        except (ValueError, binascii.Error):
            raise AIError("Некорректный формат изображения.") from None
        if not data or len(data) > MAX_IMAGE:
            raise AIError("Некорректный размер изображения.")
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return ImageResult(data=data)
        if data.startswith(b"\xff\xd8\xff"):
            return ImageResult(data=data, filename="crafttoken.jpg")
        raise AIError("Сервис вернул неподдерживаемый формат изображения.")
    url = item.get("url")
    if isinstance(url, str) and len(url) <= 4096:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        # Telegram fetches the HTTPS URL; do not fetch arbitrary URLs on our server.
        if parsed.scheme == "https" and host and "." in host and not parsed.username and not parsed.password and parsed.port in (None, 443) and not host.endswith((".local", ".internal", ".localhost")):
            try:
                ipaddress.ip_address(host)
            except ValueError:
                return ImageResult(url=url)
    raise AIError("Сервис не вернул доступное изображение. Проверь историю расходов.")


async def generate_image(config: ImageConfig, prompt: str):
    body = request_body(prompt)
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120, connect=10)) as session:
            async with session.post(URL, headers={"Authorization": f"Bearer {config.key}"}, json=body, allow_redirects=False) as response:
                if response.status != 200:
                    raise AIError(await error_details(response))
                content = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    content.extend(chunk)
                    if len(content) > MAX_JSON:
                        raise AIError("Ответ сервиса слишком большой.")
                return parse_result(json.loads(content))
    except (aiohttp.ClientError, TimeoutError, ValueError, TypeError, KeyError, IndexError, AttributeError):
        raise AIError("Не удалось получить изображение. Проверь расходы в GetAPI; автоматического повтора нет.") from None


async def error_details(response):
    """Expose safe diagnostics only, not raw responses, credentials or prompts."""
    status = response.status
    hints = {
        400: "Запрос или цена маршрута отклонены.",
        401: "API-ключ не принят.",
        402: "Недостаточный баланс или лимит расходов.",
        403: "Нет доступа к модели или методу.",
        404: "Модель или API-метод не найдены.",
        429: "Ограничение частоты или квоты.",
        503: "Модель или провайдер недоступны.",
    }
    code, request_id, category = "", "", ""
    try:
        data = json.loads(await response.content.read(16384))
        error = data.get("error", {})
        raw_code = str(error.get("code", ""))
        known = {"model_price_error", "upstream_error", "model_not_found", "provider_routing", "bad_response_body", "invalid_request_error", "insufficient_quota", "rate_limit_exceeded", "invalid_api_key", "insufficient_balance"}
        if raw_code in known or raw_code in {str(i) for i in range(400, 600)}:
            code = raw_code
        msg = str(error.get("message", ""))
        lower = msg.lower()
        for marker, label in (("no compatible priced endpoint", "нет совместимого тарифа"), ("no available endpoints", "нет доступного исполнителя"), ("configured_price", "не удалось определить цену"), ("reserve", "ошибка резерва"), ("insufficient", "недостаточный лимит или баланс"), ("invalid token", "ключ не принят"), ("resolution", "параметр разрешения")):
            if marker in lower:
                category = label
                break
        match = re.search(r"request id:\s*([0-9]{10,30}[A-Za-z0-9_-]{0,30})\b", msg, re.I)
        if match:
            request_id = match.group(1)
    except (ValueError, TypeError, AttributeError, aiohttp.ClientError, TimeoutError):
        pass
    details = f"HTTP {status}" + (f" · {code}" if code else "") + (f" · {category}" if category else "")
    return f"GetAPI: {details}. {hints.get(status, 'Ошибка сервиса или провайдера.')}" + (f"\nRequest ID: {request_id}" if request_id else "") + "\nПроверь журнал расходов. Автоповтора нет."
