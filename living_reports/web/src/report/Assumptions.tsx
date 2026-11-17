import { useState } from "react";
import type { Assumption } from "../lib/api";

/** Each assumption is visible, and the reader can change it. */
export function Assumptions({ items, onChange }: { items: Assumption[]; onChange: (text: string) => void }) {
  const [editing, setEditing] = useState<string | null>(null);
  const [text, setText] = useState("");
  if (items.length === 0) return null;
  return (
    <div className="assumptions">
      <span className="meta">Assumptions</span>
      {items.map((a) => editing === a.id ? (
        <form key={a.id} onSubmit={(e) => { e.preventDefault(); setEditing(null); if (text.trim()) onChange(text); }}>
          <input aria-label="New assumption" className="ask-input" autoFocus value={text} onChange={(e) => setText(e.target.value)} />
        </form>
      ) : (
        <span className="assumption" key={a.id}>
          {a.text}{" "}
          <button type="button" className="link" onClick={() => { setText(a.text); setEditing(a.id); }}>Change</button>
        </span>
      ))}
    </div>
  );
}
