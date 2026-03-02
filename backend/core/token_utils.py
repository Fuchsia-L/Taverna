import tiktoken

# Use cl100k_base encoding (works for gpt-4o, gpt-4o-mini, etc.)
_enc = tiktoken.encoding_for_model("gpt-4o-mini")


def estimate_tokens(text: str) -> int:
    """Accurate token count using tiktoken."""
    return len(_enc.encode(text))


def _extract_text(content) -> str:
    """Extract plain text from string content or OpenAI content-parts list."""
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
        return "\n".join(parts)
    return ""


def _count_image_parts(content) -> int:
    """Count image blocks in OpenAI content-parts list."""
    if not isinstance(content, list):
        return 0
    count = 0
    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type in ("image_url", "input_image"):
            count += 1
    return count


def estimate_messages_tokens(messages: list[dict]) -> int:
    """Estimate total tokens for a list of message dicts."""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        total += estimate_tokens(_extract_text(content))
        # Rough image token budget; exact accounting varies by model/provider.
        total += _count_image_parts(content) * 85
        total += 4  # overhead per message (role, formatting)
    return total
