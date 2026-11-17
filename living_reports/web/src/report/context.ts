import { createContext, useContext } from "react";
import type { FactT, QueryView } from "../lib/api";

export const FactsContext = createContext<Record<string, FactT>>({});
export const QueriesContext = createContext<Record<string, QueryView>>({});
export const useFact = (id: string): FactT | undefined => useContext(FactsContext)[id];

/** Set where a number can open a thread. */
export const AnchorContext = createContext<((factId: string) => void) | null>(null);
