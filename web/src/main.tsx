import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { RootBoundary } from "./app/RootBoundary";
import "@xyflow/react/dist/style.css";
import "./styles/index.css";

createRoot(document.getElementById("root") as HTMLElement).render(
  <StrictMode>
    {/* Границы виджетов начинаются внутри приложения. Всё, что падает до них,
        раньше оставляло пустой `#root`. */}
    <RootBoundary>
      <App />
    </RootBoundary>
  </StrictMode>,
);
