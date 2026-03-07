import logging
import re

from core.providers import get_async_client_for_model, load_config, resolve_model

logger = logging.getLogger(__name__)

COMPRESS_PROMPT = """你是一个教学对话摘要助手。请将以下师生对话压缩成简洁的摘要。

要求：
- 记录学生已经掌握的知识点
- 记录学生曾经卡壳或答错的地方
- 记录当前教学进度（讲到哪了）
- 不超过300字
- 使用客观第三人称（"学生已理解……"、"老师引导了……"）

对话内容：
"""


_THINK_RE = re.compile(r"<think>[\s\S]*?</think>\s*", re.IGNORECASE)


def _content_to_text(content) -> str:
    if isinstance(content, str):
        return _THINK_RE.sub("", content)
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type in ("text", "input_text"):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(_THINK_RE.sub("", text))
            elif item_type in ("image_url", "input_image"):
                parts.append("[图片]")
        return "\n".join(parts)
    return str(content)


async def compress_messages(messages: list[dict]) -> str:
    """
    Send older messages to AI for compression into a summary.
    Returns the summary string.

    IMPORTANT: The conversation content is sent as a `user` message,
    NOT stuffed into `system`. This avoids model instability with
    large system prompts, and avoids Python format-string injection
    if student messages contain curly braces like {variable}.
    """
    conversation_text = ""
    for msg in messages:
        role_label = "老师" if msg["role"] == "assistant" else "学生"
        conversation_text += f"{role_label}：{_content_to_text(msg['content'])}\n"

    try:
        config = load_config()
        compressor_model = config.get("compressor_model") or config.get("default_model")
        # Validate that the model actually exists in enabled_models; fall back to any available model
        try:
            compressor_model = resolve_model(compressor_model)
        except ValueError:
            logger.warning("No valid compressor model found, skipping compression")
            return ""
        client = get_async_client_for_model(compressor_model)
        response = await client.chat.completions.create(
            model=compressor_model,
            messages=[
                {
                    "role": "system",
                    "content": "你是一个教学对话摘要助手。",
                },
                {
                    "role": "user",
                    "content": COMPRESS_PROMPT + conversation_text,
                },
            ],
            max_tokens=500,
        )
        return response.choices[0].message.content or ""
    except Exception:
        logger.exception("Compression failed, returning empty summary")
        return ""
