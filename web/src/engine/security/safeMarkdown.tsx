import { memo } from "react";
import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { safeUrl } from "./safeUrl";

const components: Components = {
  a: ({ href, children }) => href
    ? <a href={href} target={href.startsWith("#") ? undefined : "_blank"} rel="noreferrer noopener">{children}</a>
    : <span>{children}</span>,
  table: ({ children }) => <div className="table-scroll"><table>{children}</table></div>,
  // Opening a document must not fetch external images automatically.
  img: ({ src, alt }) => src
    ? <a href={src} target="_blank" rel="noreferrer noopener">{alt || "изображение"}</a>
    : <span>{alt}</span>,
};

export const SafeMarkdown = memo(function SafeMarkdown({ value }: { value: unknown }) {
  const text = typeof value === "string" ? value : String(value ?? "");
  return (
    <div className="safe-markdown">
      <Markdown remarkPlugins={[remarkGfm]} components={components}
        urlTransform={(url) => url.startsWith("#") ? url : safeUrl(url, true) ?? ""}
      >{text}</Markdown>
    </div>
  );
});
