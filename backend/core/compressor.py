import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL"),
)

COMPRESS_PROMPT = """你是一个教学对话摘要助手。请将以下师生对话压缩成简洁的摘要。

要求：
- 记录学生已经掌握的知识点
- 记录学生曾经卡壳或答错的地方
- 记录当前教学进度（讲到哪了）
- 不超过300字
- 使用客观第三人称（"学生已理解……"、"老师引导了……"）

对话内容：
"""


def _content_to_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type in ("text", "input_text"):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif item_type in ("image_url", "input_image"):
                parts.append("[图片]")
        return "\n".join(parts)
    return str(content)


def compress_messages(messages: list[dict]) -> str:
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
        response = client.chat.completions.create(
            model=os.getenv("MODEL_NAME", "gpt-4o-mini"),
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
        # If compression fails, fall back to crude truncation
        return conversation_text[:500] + "\n...(压缩失败，截断保留)"
