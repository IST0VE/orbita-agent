/**
 * Состояние связи с сервером: «на связи», «нет доступа», «нет сервера».
 *
 * Вынесено из `App.tsx` потому, что это законченная обязанность со своим
 * жизненным циклом: таймер, четыре подписки на события окна и отмена запроса.
 * В компоненте она занимала тридцать строк между загрузкой ассистентов и
 * загрузкой манифеста, ни с одной из них не связанная.
 *
 * Проверка не идёт, пока вкладка скрыта: фоновая вкладка, опрашивающая сервер
 * раз в пятнадцать секунд, тратит чужой ресурс и ничего никому не показывает.
 */
import { useEffect, useState } from "react";

import { checkServer, type ServerStatus } from "../api";

const INTERVAL_MS = 15_000;

export function useServerStatus(): ServerStatus | null {
  const [online, setOnline] = useState<ServerStatus | null>(null);

  useEffect(() => {
    let live = true;
    let checking = false;
    const controller = new AbortController();
    const check = async () => {
      if (checking || document.visibilityState === "hidden") return;
      checking = true;
      try {
        const value = await checkServer(controller.signal);
        if (live) setOnline(value);
      } finally {
        checking = false;
      }
    };
    const offline = () => setOnline("offline");
    void check();
    const timer = window.setInterval(check, INTERVAL_MS);
    window.addEventListener("online", check);
    window.addEventListener("offline", offline);
    window.addEventListener("focus", check);
    document.addEventListener("visibilitychange", check);
    return () => {
      live = false;
      controller.abort();
      window.clearInterval(timer);
      window.removeEventListener("online", check);
      window.removeEventListener("offline", offline);
      window.removeEventListener("focus", check);
      document.removeEventListener("visibilitychange", check);
    };
  }, []);

  return online;
}
