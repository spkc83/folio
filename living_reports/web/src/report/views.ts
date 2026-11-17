import type { View } from "../lib/api";

export const VIEWS: { id: View; label: string }[] = [
  { id: "narrative", label: "Narrative" },
  { id: "dashboard", label: "Dashboard" },
  { id: "brief", label: "Decision brief" },
  { id: "evidence", label: "Evidence" },
];
