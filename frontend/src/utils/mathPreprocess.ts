function preprocessPlainText(segment: string): string {
  return segment
    .replace(/\\\[([\s\S]*?)\\\]/g, (_, inner: string) => `$$${inner}$$`)
    .replace(/\\\(([\s\S]*?)\\\)/g, (_, inner: string) => `$${inner}$`);
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

export function preprocessLaTeX(content: unknown): string {
  const text = normalizeText(content);
  const fencedParts = text.split(/(```[\s\S]*?```)/g);
  return fencedParts
    .map((fencedPart) => {
      if (fencedPart.startsWith("```") && fencedPart.endsWith("```")) {
        return fencedPart;
      }
      const inlineParts = fencedPart.split(/(`[^`\n]*`)/g);
      return inlineParts
        .map((inlinePart) => {
          if (inlinePart.startsWith("`") && inlinePart.endsWith("`")) {
            return inlinePart;
          }
          return preprocessPlainText(inlinePart);
        })
        .join("");
    })
    .join("");
}
