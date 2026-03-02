function preprocessPlainText(segment: string): string {
  return segment
    .replace(/\\\[([\s\S]*?)\\\]/g, (_, inner: string) => `$$${inner}$$`)
    .replace(/\\\(([\s\S]*?)\\\)/g, (_, inner: string) => `$${inner}$`);
}

export function preprocessLaTeX(content: string): string {
  const fencedParts = content.split(/(```[\s\S]*?```)/g);
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

