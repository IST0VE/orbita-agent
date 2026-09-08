import type { GraphTopology } from "../../lib/graph";
import type { EngineCapabilities, UiManifest } from "../manifest/types";
import { fallbackManifest, validateManifest } from "../manifest/validate";

import { authorizedFetch } from "../../auth";
const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
const manifestCache = new Map<string, { etag?: string; value: UiManifest }>();
let capabilityCache: { etag?: string; value: EngineCapabilities } | undefined;

export type UiBundle = {
  assistant: { assistant_id: string; graph_id: string };
  topology: GraphTopology;
  topologyHash: string;
  manifest: UiManifest;
  manifestEtag?: string;
  generatedAt?: string;
  fallback: boolean;
  warning?: string;
};

function headers(extra?: HeadersInit): HeadersInit {
  const result = new Headers(extra);
  result.set("content-type", "application/json");
  return result;
}

async function errorOf(response: Response): Promise<Error> {
  const body = (await response.json().catch(() => null)) as { error?: string; error_code?: string } | null;
  const error = new Error(body?.error ?? `HTTP ${response.status}`);
  error.name = body?.error_code ?? "UiApiError";
  return error;
}

export async function loadCapabilities(apiUrl: string): Promise<EngineCapabilities> {
  const response = await authorizedFetch(`${apiUrl}/api/ui/capabilities`, {
    headers: headers(capabilityCache?.etag ? { "if-none-match": capabilityCache.etag } : undefined),
  });
  if (response.status === 304 && capabilityCache) return capabilityCache.value;
  if (!response.ok) throw await errorOf(response);
  const value = (await response.json()) as EngineCapabilities;
  if (value.engine !== "orbita-ui" || !value.manifest_versions?.includes("1.0")) {
    throw new Error("сервер UI engine вернул несовместимые capabilities");
  }
  capabilityCache = { etag: response.headers.get("etag") ?? undefined, value };
  return value;
}

export async function loadManifest(apiUrl: string, graphId: string): Promise<UiManifest> {
  if (!SAFE_ID.test(graphId)) throw new Error("некорректный graph id");
  const cached = manifestCache.get(graphId);
  const response = await authorizedFetch(`${apiUrl}/api/ui/graphs/${encodeURIComponent(graphId)}/manifest`, {
    headers: headers(cached?.etag ? { "if-none-match": cached.etag } : undefined),
  });
  if (response.status === 304 && cached) return cached.value;
  if (!response.ok) throw await errorOf(response);
  const value = validateManifest(await response.json(), graphId);
  manifestCache.set(graphId, { etag: response.headers.get("etag") ?? undefined, value });
  return value;
}

async function fetchTopology(apiUrl: string, assistantId: string): Promise<GraphTopology> {
  const response = await authorizedFetch(`${apiUrl}/assistants/${encodeURIComponent(assistantId)}/graph`);
  if (!response.ok) throw await errorOf(response);
  const value = (await response.json()) as GraphTopology;
  if (!Array.isArray(value.nodes) || !Array.isArray(value.edges)) throw new Error("некорректная topology");
  return value;
}

async function digest(value: unknown): Promise<string> {
  const bytes = new TextEncoder().encode(JSON.stringify(value));
  if (!globalThis.crypto?.subtle) return `local:${bytes.length}`;
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  return `sha256:${Array.from(new Uint8Array(hash), (part) => part.toString(16).padStart(2, "0")).join("")}`;
}

export async function loadUiBundle(
  apiUrl: string,
  assistant: { assistant_id: string; graph_id: string; name?: string },
): Promise<UiBundle> {
  let manifest: UiManifest;
  let warning: string | undefined;
  let fallback = false;
  try {
    const response = await authorizedFetch(
      `${apiUrl}/api/ui/assistants/${encodeURIComponent(assistant.assistant_id)}/bundle?graph_id=${encodeURIComponent(assistant.graph_id)}`,
      { headers: headers() },
    );
    if (!response.ok) throw await errorOf(response);
    const payload = (await response.json()) as {
      assistant?: { assistant_id?: string; graph_id?: string };
      manifest?: unknown;
      manifest_etag?: string;
      generated_at?: string;
    };
    if (
      payload.assistant?.assistant_id !== assistant.assistant_id ||
      payload.assistant?.graph_id !== assistant.graph_id
    ) {
      throw new Error("bundle содержит несовместимую пару assistant/manifest");
    }
    manifest = validateManifest(payload.manifest, assistant.graph_id);
    manifestCache.set(assistant.graph_id, {
      etag: payload.manifest_etag ? `"${payload.manifest_etag}"` : undefined,
      value: manifest,
    });
  } catch (error) {
    try {
      manifest = await loadManifest(apiUrl, assistant.graph_id);
      warning = `bundle недоступен, использованы согласованные graph_id endpoints: ${
        error instanceof Error ? error.message : String(error)
      }`;
    } catch (manifestError) {
      fallback = true;
      warning = manifestError instanceof Error ? manifestError.message : String(manifestError);
      manifest = fallbackManifest(assistant.graph_id, assistant.name ?? assistant.graph_id);
    }
  }
  const topology = await fetchTopology(apiUrl, assistant.assistant_id);
  return {
    assistant,
    topology,
    topologyHash: await digest(topology),
    manifest,
    manifestEtag: manifestCache.get(assistant.graph_id)?.etag,
    fallback,
    warning,
  };
}

export async function loadResource(
  apiUrl: string,
  resourceId: string,
  operation: string,
  params: Record<string, string> = {},
): Promise<unknown> {
  if (!SAFE_ID.test(resourceId) || !SAFE_ID.test(operation)) throw new Error("некорректный resource binding");
  const query = new URLSearchParams({ operation });
  for (const [key, value] of Object.entries(params)) {
    if (!SAFE_ID.test(key) || value.length > 4096) throw new Error("некорректные параметры resource binding");
    query.set(key, value);
  }
  const response = await authorizedFetch(
    `${apiUrl}/api/ui/resources/${encodeURIComponent(resourceId)}?${query}`,
    { headers: headers() },
  );
  if (!response.ok) throw await errorOf(response);
  return response.json();
}

export async function mutateResource(
  apiUrl: string,
  resourceId: string,
  operation: string,
  payload: Record<string, unknown>,
): Promise<unknown> {
  if (!SAFE_ID.test(resourceId) || !SAFE_ID.test(operation)) {
    throw new Error("некорректный resource binding");
  }
  const response = await authorizedFetch(
    `${apiUrl}/api/ui/resources/${encodeURIComponent(resourceId)}?operation=${encodeURIComponent(operation)}`,
    {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({
        ...payload,
        idempotency_key: crypto.randomUUID?.() ?? `${Date.now()}-${Math.random()}`,
      }),
    },
  );
  if (!response.ok) throw await errorOf(response);
  return response.json();
}

export type ActionValidation = { valid: true; duplicate: boolean; idempotency_key: string };

export async function validateAction(
  apiUrl: string,
  body: Record<string, unknown>,
): Promise<ActionValidation> {
  const response = await authorizedFetch(`${apiUrl}/api/ui/actions/validate`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify(body),
  });
  if (!response.ok) throw await errorOf(response);
  return response.json() as Promise<ActionValidation>;
}
