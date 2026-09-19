import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

import { contentSecurityPolicy } from "./build/csp";

/**
 * Сборка фронта. Всё, что здесь есть, — это React и прокси на сервер агента.
 *
 * Прокси, а не прямые запросы на localhost:2024: тогда браузер видит один
 * источник, и CORS вместе с настройками ключей просто не возникает как тема.
 * Собранный статикой фронт ходит по тем же относительным путям — адрес
 * задаётся при сборке через VITE_API_URL.
 *
 * Тот же адрес попадает в `connect-src` политики документа: адрес API,
 * встроенный в код, и адрес API, разрешённый политикой, обязаны быть одним
 * адресом. Расхождение между ними — не ошибка выполнения, а тишина: запрос
 * не уходит вовсе, и увидеть это можно только в консоли браузера.
 */

// Пути сервера LangGraph, которые фронт зовёт напрямую, плюс наши /api/*.
const PROXIED = ["/api", "/assistants", "/threads", "/runs", "/info", "/ok", "/store"];

export default defineConfig(({ mode }) => {
  // Не только `process.env`: адрес приезжает и из `.env`, откуда его берёт
  // `import.meta.env` в самом приложении.
  const apiUrl = loadEnv(mode, process.cwd(), "VITE_").VITE_API_URL;
  // Некорректный или незащищённый удалённый адрес останавливает сборку здесь,
  // а не превращается в молча заблокированные запросы у оператора.
  const policy = contentSecurityPolicy(apiUrl);

  return {
    plugins: [
      react(),
      {
        name: "orbita-csp",
        transformIndexHtml: {
          order: "pre" as const,
          handler: (html: string) => html.replace("%ORBITA_CSP%", policy),
        },
      },
    ],
    server: {
      port: 5173,
      proxy: Object.fromEntries(
        PROXIED.map((path) => [
          path,
          { target: apiUrl || "http://127.0.0.1:2024", changeOrigin: true },
        ]),
      ),
    },
    build: {
      // Карты кода на проде не нужны, а вес у них больше самого бандла.
      sourcemap: false,
      // SDK LangGraph 1.x приносит схемы zod и заметно тяжелее кода интерфейса.
      // Отдельные vendor-чанки не заставляют браузер скачивать их заново при
      // каждой правке панели и удерживают entry bundle небольшим.
      rollupOptions: {
        output: {
          manualChunks(id: string) {
            if (id.includes("@xyflow") || id.includes("d3-")) return "graph";
            if (
              id.includes("@langchain") ||
              id.includes("langsmith") ||
              id.includes("zod")
            ) {
              return "langgraph";
            }
            return undefined;
          },
        },
      },
    },
  };
});
