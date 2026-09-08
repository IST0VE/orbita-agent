import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

/**
 * Сборка фронта. Всё, что здесь есть, — это React и прокси на сервер агента.
 *
 * Прокси, а не прямые запросы на localhost:2024: тогда браузер видит один
 * источник, и CORS вместе с настройками ключей просто не возникает как тема.
 * Собранный статикой фронт ходит по тем же относительным путям — адрес
 * задаётся при сборке через VITE_API_URL.
 */
const TARGET = process.env.VITE_API_URL || "http://127.0.0.1:2024";

// Пути сервера LangGraph, которые фронт зовёт напрямую, плюс наши /api/*.
const PROXIED = ["/api", "/assistants", "/threads", "/runs", "/info", "/ok", "/store"];

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: Object.fromEntries(
      PROXIED.map((path) => [path, { target: TARGET, changeOrigin: true }]),
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
        manualChunks(id) {
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
});
