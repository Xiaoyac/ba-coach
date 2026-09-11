import { Fragment, type ReactNode } from "react";

// Deliberately render React text, never HTML. Model/user strings cannot create
// scripts, event handlers, images or unsafe links. Supports common coach prose.
function inline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*\n]+\*\*|`[^`\n]+`|\[[^\]\n]+\]\(https?:\/\/[^\s)]+\))/g).map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={i}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`")) return <code key={i} className="rounded bg-raised px-1 font-mono">{part.slice(1, -1)}</code>;
    const link = /^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)$/.exec(part);
    if (link) return <a key={i} href={link[2]} target="_blank" rel="noopener noreferrer" className="underline">{link[1]}</a>;
    return <Fragment key={i}>{part}</Fragment>;
  });
}

export default function MessageMarkdown({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^\s*```/.test(line)) {
      const code: string[] = [];
      while (++i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i]);
      blocks.push(<pre key={i} className="my-2 overflow-x-auto rounded-xl bg-raised p-3 text-xs"><code>{code.join("\n")}</code></pre>);
    } else if (/^\s*#{1,6}\s+/.test(line)) {
      blocks.push(<div role="heading" aria-level={Math.min(line.trimStart().match(/^#+/)![0].length, 6)} key={i} className="mt-3 mb-1 font-semibold">{inline(line.replace(/^\s*#{1,6}\s+/, ""))}</div>);
    } else if (/^\s*([-*+] |\d+[.)] )/.test(line)) {
      const ordered = /^\s*\d/.test(line);
      const items: ReactNode[] = [];
      const pattern = ordered ? /^\s*\d+[.)]\s+/ : /^\s*[-*+]\s+/;
      do { items.push(<li key={i}>{inline(lines[i].replace(pattern, ""))}</li>); i++; } while (i < lines.length && pattern.test(lines[i]));
      i--;
      blocks.push(ordered ? <ol key={i} className="my-2 list-decimal space-y-1 pl-5">{items}</ol> : <ul key={i} className="my-2 list-disc space-y-1 pl-5">{items}</ul>);
    } else if (/^\s*>\s?/.test(line)) {
      blocks.push(<blockquote key={i} className="my-2 border-l-2 border-accent-edge pl-3 text-ink-muted">{inline(line.replace(/^\s*>\s?/, ""))}</blockquote>);
    } else if (line.trim()) {
      blocks.push(<p key={i} className="my-1 whitespace-pre-wrap">{inline(line)}</p>);
    } else blocks.push(<div key={i} className="h-2" />);
  }
  return <div className="whitespace-normal break-words">{blocks}</div>;
}
