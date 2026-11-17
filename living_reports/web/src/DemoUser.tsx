import { useState } from "react";
import { currentUser, setCurrentUser } from "./lib/api";

/** Selects a seeded user. A real identity system replaces this after the MVP. */
export function DemoUser({ onChange }: { onChange?: (user: string) => void }) {
  const [user, setUser] = useState(currentUser());
  return (
    <label className="tier-field">
      <span className="meta">Demo user</span>
      <select value={user} onChange={(e) => { setCurrentUser(e.target.value); setUser(e.target.value); onChange?.(e.target.value); }}>
        <option value="ana">ana (author)</option>
        <option value="omar">omar (owner)</option>
        <option value="priya">priya (owner)</option>
        <option value="chidi">chidi (compliance)</option>
      </select>
    </label>
  );
}
