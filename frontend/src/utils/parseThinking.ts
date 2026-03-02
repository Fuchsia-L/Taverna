export interface ParsedMessage {
  thinking: string | null;
  visible: string;
}

export function parseThinking(raw: string): ParsedMessage {
  const matches = [...raw.matchAll(/<think>([\s\S]*?)<\/think>/gi)];
  const thinking = matches
    .map((match) => match[1]?.trim())
    .filter((item): item is string => Boolean(item))
    .join("\n\n")
    .trim();
  return {
    thinking: thinking || null,
    visible: raw.replace(/<think>[\s\S]*?<\/think>/gi, "").trim()
  };
}
