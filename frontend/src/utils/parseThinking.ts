export interface ParsedMessage {
  thinking: string | null;
  visible: string;
}

function normalizeText(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  if (value == null) {
    return "";
  }
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

export function parseThinking(raw: unknown): ParsedMessage {
  const text = normalizeText(raw);
  const matches = [...text.matchAll(/<think>([\s\S]*?)<\/think>/gi)];
  const thinking = matches
    .map((match) => match[1]?.trim())
    .filter((item): item is string => Boolean(item))
    .join("\n\n")
    .trim();
  return {
    thinking: thinking || null,
    visible: text.replace(/<think>[\s\S]*?<\/think>/gi, "").trim()
  };
}
