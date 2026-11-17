import { useQuery } from "@tanstack/react-query";
import { createContext, useContext, type ReactNode } from "react";
import { getJSON, type Vocabulary } from "../lib/api";

const VocabularyContext = createContext<Vocabulary | null>(null);

/** Loads the vocabulary once for the whole app. Pages without it (the export) get null. */
export function VocabularyProvider({ children }: { children: ReactNode }) {
  const vocabulary = useQuery({ queryKey: ["vocabulary"], queryFn: () => getJSON<Vocabulary>("/api/vocabulary"), staleTime: Infinity, retry: false });
  return <VocabularyContext.Provider value={vocabulary.data ?? null}>{children}</VocabularyContext.Provider>;
}

export const useVocabulary = () => useContext(VocabularyContext);

const capitalized = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** The label people read for a metric or dimension id. A time column ("…:month", "month") reads as its grain. */
export function labelOf(v: Vocabulary | null, id: string): string {
  const term = v?.metrics?.find((t) => t.id === id) ?? v?.dimensions?.find((t) => t.id === id);
  if (term) return term.label;
  if (id === "month") return "Month";
  const grain = id.includes(":") ? id.split(":").pop() ?? "" : "";
  return grain ? capitalized(grain) : id;
}
