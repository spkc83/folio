import { useQuery } from "@tanstack/react-query";
import { getJSON, type Config } from "./api";

const OFF: Config = { demo: false };

/** The deployment's flags. Demo controls stay hidden until the server says demo is on. */
export function useConfig(): Config {
  const config = useQuery({ queryKey: ["config"], queryFn: () => getJSON<Config>("/api/config"), staleTime: Infinity });
  return config.data ?? OFF;
}
