import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { RootBoundary } from "./app/RootBoundary";
import { installClientCapture } from "./lib/clientLog.ts";
import { initAuth } from "./oidc";
import "@xyflow/react/dist/style.css";
import "./styles/index.css";

// До первого кадра: исключения при монтировании и в первых эффектах тоже
// должны попасть в журнал интерфейса.
installClientCapture();

const root = createRoot(document.getElementById("root") as HTMLElement);

// Вход — до первого кадра: без него каждый запрос интерфейса получил бы 401.
initAuth().then(
  () => root.render(
    <StrictMode>
      {/* Границы виджетов начинаются внутри приложения. Всё, что падает до них,
          раньше оставляло пустой `#root`. */}
      <RootBoundary>
        <App />
      </RootBoundary>
    </StrictMode>,
  ),
  (error: unknown) => root.render(
    <div className="auth-failed" role="alert">
      <p>Не удалось войти: {error instanceof Error ? error.message : String(error)}</p>
      <p><a href="/">Попробовать снова</a></p>
    </div>,
  ),
);
