import { createElement, memo, type ReactNode } from "react";
import { safeUrl } from "./safeUrl";

const LINK = /\[([^\]]{1,500})\]\(([^)\s]{1,4096})\)/g;

function inline(text: string): ReactNode[] {
  const result: ReactNode[] = [];
  let cursor = 0;
  for (const match of text.matchAll(LINK)) {
    const index = match.index ?? 0;
    result.push(text.slice(cursor, index));
    const href = safeUrl(match[2], true);
    result.push(
      href ? (
        <a key={`${index}:${href}`} href={href} target="_blank" rel="noreferrer noopener">
          {match[1]}
        </a>
      ) : (
        match[1]
      ),
    );
    cursor = index + match[0].length;
  }
  result.push(text.slice(cursor));
  return result;
}

export const SafeMarkdown = memo(function SafeMarkdown({ value }: { value: unknown }) {
  const text = typeof value === "string" ? value : String(value ?? "");
  return (
    <div className="safe-markdown">
      {text.split(/\r?\n/).map((line, index) => {
        const heading = /^(#{1,6})\s+(.*)$/.exec(line);
        if (heading) {
          return createElement(`h${heading[1].length}`, { key: index }, inline(heading[2]));
        }
        return line ? <p key={index}>{inline(line)}</p> : <br key={index} />;
      })}
    </div>
  );
});
