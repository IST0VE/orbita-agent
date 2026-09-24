import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { RootBoundary } from "./app/RootBoundary";
import { installClientCapture } from "./lib/clientLog.ts";
import "@xyflow/react/dist/style.css";
import "./styles/index.css";

// До первого кадра: исключения при монтировании и в первых эффектах тоже
// должны попасть в журнал интерфейса.
installClientCapture();

createRoot(document.getElementById("root") as HTMLElement).render(
  <StrictMode>
    {/* Границы виджетов начинаются внутри приложения. Всё, что падает до них,
        раньше оставляло пустой `#root`. */}
    <RootBoundary>
      <App />
    </RootBoundary>
  </StrictMode>,
);
