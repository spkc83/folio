import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import type { Payload } from "./lib/api";
import { ExportPage } from "./report/ExportPage";
import "./theme.css";

const data = JSON.parse(document.getElementById("lr-payload")!.textContent!) as Payload & { hosted_url: string };

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ExportPage data={data} />
  </StrictMode>,
);
