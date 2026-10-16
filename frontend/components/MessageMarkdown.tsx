import { Fragment, type ReactNode } from "react";
import { textCharacters } from "@/lib/textCharacters";

function characters(text: string, animated: boolean): ReactNode {
  return animated ? textCharacters(text).map((char, index) =>
    <span key={index} className="reply-character">{char}</span>) : text;
}

// Deliberately render React text, never HTML. Model/user strings cannot create
// scripts, event handlers, images or unsafe links. Supports common coach prose.
function inline(text: string, animated = false): ReactNode[] {
  return text.split(/(\*\*[^*\n]+\*\*|`[^`\n]+`|\[[^\]\n]+\]\(https?:\/\/[^\s)]+\))/g).map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={i}>{characters(part.slice(2, -2), animated)}</strong>;
    if (part.startsWith("`") && part.endsWith("`")) return <code key={i} className="rounded bg-raised px-1 font-mono">{characters(part.slice(1, -1), animated)}</code>;
    const link = /^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)$/.exec(part);
    if (link) return <a key={i} href={link[2]} target="_blank" rel="noopener noreferrer" className="underline">{characters(link[1], animated)}</a>;
    return <Fragment key={i}>{characters(part, animated)}</Fragment>;
  });
}

// Ignore escaped pipes and pipes inside inline code when detecting table cells.
function tableCells(line: string): string[] {
  const cells: string[] = []; let cell = "", code = false;
  for (let i = 0; i < line.length; i++) {
    const char = line[i];
    if (char === "\\" && line[i + 1] === "|") { cell += "|"; i++; continue; }
    if (char === "`") code = !code;
    if (char === "|" && !code) { cells.push(cell.trim()); cell = ""; }
    else cell += char;
  }
  cells.push(cell.trim());
  if (line.trimStart().startsWith("|")) cells.shift();
  if (line.trimEnd().endsWith("|") && cells.at(-1) === "") cells.pop();
  return cells;
}

export default function MessageMarkdown({ text, trailing, tables = false, animated = false }: { text: string; trailing?: ReactNode; tables?: boolean; animated?: boolean }) {
  const renderInline = (value: string) => inline(value, animated);
  const blocks: ReactNode[] = [];
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  let lastContentLine = lines.length - 1;
  while (lastContentLine >= 0 && !lines[lastContentLine].trim()) lastContentLine--;
  const suffix = (index: number) => index === lastContentLine ? trailing : null;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const header = tables && line.includes("|") ? tableCells(line) : [];
    const divider = tables && i + 1 < lines.length ? tableCells(lines[i + 1]) : [];
    if (header.length >= 2 && header.length === divider.length && divider.every(cell => /^:?-{3,}:?$/.test(cell))) {
      const rows: string[][] = [];
      const start = i;
      i += 2;
      while (i < lines.length && lines[i].trim() && lines[i].includes("|")) {
        const cells = tableCells(lines[i]);
        // A partial streamed row stays as prose until it has all its cells.
        if (cells.length !== header.length) break;
        rows.push(cells); i++;
      }
      i--;
      const align = (index: number): "left" | "center" | "right" => divider[index].endsWith(":") ? divider[index].startsWith(":") ? "center" : "right" : "left";
      blocks.push(<Fragment key={start}><div role="region" aria-label="回复表格，可横向滚动" tabIndex={0} className="participant-reply-table">
        <table><thead><tr>{header.map((cell, index) => <th key={index} scope="col" style={{textAlign: align(index)}}>{renderInline(cell)}</th>)}</tr></thead>
          <tbody>{rows.map((row, index) => <tr key={index}>{row.map((cell, column) => <td key={column} style={{textAlign: align(column)}}>{renderInline(cell)}</td>)}</tr>)}</tbody>
        </table></div>{suffix(i)}</Fragment>);
    } else if (/^\s*```/.test(line)) {
      const start = i;
      const code: string[] = [];
      while (++i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i]);
      blocks.push(<Fragment key={start}><pre className="my-2 overflow-x-auto rounded-xl bg-raised p-3 text-xs"><code>{characters(code.join("\n"), animated)}</code></pre>{i >= lastContentLine ? trailing : null}</Fragment>);
    } else if (/^\s*#{1,6}\s+/.test(line)) {
      blocks.push(<div role="heading" aria-level={Math.min(line.trimStart().match(/^#+/)![0].length, 6)} key={i} className="mt-3 mb-1 font-semibold">{renderInline(line.replace(/^\s*#{1,6}\s+/, ""))}{suffix(i)}</div>);
    } else if (/^\s*([-*+] |\d+[.)] )/.test(line)) {
      const start = i;
      const ordered = /^\s*\d/.test(line);
      const items: ReactNode[] = [];
      const pattern = ordered ? /^\s*\d+[.)]\s+/ : /^\s*[-*+]\s+/;
      do { items.push(<li key={i}>{renderInline(lines[i].replace(pattern, ""))}{suffix(i)}</li>); i++; } while (i < lines.length && pattern.test(lines[i]));
      i--;
      blocks.push(ordered ? <ol key={start} className="my-2 list-decimal space-y-1 pl-5">{items}</ol> : <ul key={start} className="my-2 list-disc space-y-1 pl-5">{items}</ul>);
    } else if (/^\s*>\s?/.test(line)) {
      blocks.push(<blockquote key={i} className="my-2 border-l-2 border-accent-edge pl-3 text-ink-muted">{renderInline(line.replace(/^\s*>\s?/, ""))}{suffix(i)}</blockquote>);
    } else if (line.trim()) {
      blocks.push(<p key={i} className="my-1 whitespace-pre-wrap">{renderInline(line)}{suffix(i)}</p>);
    } else blocks.push(<div key={i} className="h-2" />);
  }
  return <div className="whitespace-normal break-words">{blocks}</div>;
}
