const segmenter = typeof Intl.Segmenter === "function"
  ? new Intl.Segmenter("zh", { granularity: "grapheme" }) : null;

/** Keep emoji, combining accents and joined characters together. */
export function textCharacters(text: string): string[] {
  return segmenter ? Array.from(segmenter.segment(text), item => item.segment) : Array.from(text);
}
