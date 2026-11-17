import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Home } from "./home/Home";
import { ModelPage } from "./model/ModelPage";
import { DiffView } from "./report/DiffView";
import { ReportPage } from "./report/ReportPage";
import { VocabularyProvider } from "./report/VocabularyContext";
import "./theme.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={new QueryClient()}>
      <VocabularyProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/r/:id" element={<ReportPage />} />
          <Route path="/r/:id/e/:edition" element={<ReportPage />} />
          <Route path="/r/:id/diff" element={<DiffView />} />
          <Route path="/model" element={<ModelPage />} />
        </Routes>
      </BrowserRouter>
      </VocabularyProvider>
    </QueryClientProvider>
  </StrictMode>,
);
