export function safeUrl(value: unknown, allowHttp = false): string | null {
  if (typeof value !== "string" || value.length > 4096) return null;
  try {
    const url = new URL(value, window.location.origin);
    if (url.protocol === "https:" || (allowHttp && url.protocol === "http:")) return url.href;
  } catch {
    return null;
  }
  return null;
}
