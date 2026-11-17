import { useCallback, useContext, useRef, useState } from "react";
import { AnchorContext, useFact } from "./context";
import { Provenance } from "./Provenance";

/** One traced number. Selecting it shows where it comes from, and offers a question on it. */
export function Fact({ id }: { id: string }) {
  const fact = useFact(id);
  const anchor = useContext(AnchorContext);
  const [open, setOpen] = useState(false);
  const button = useRef<HTMLButtonElement>(null);
  const close = useCallback(() => {
    setOpen(false);
    button.current?.focus();
  }, []);
  if (!fact) return <span className="fact" data-verified="false">No data</span>;
  const verified = fact.state === "ok" && Boolean(fact.query_hash);
  return (
    <span className="fact-wrap">
      <button type="button" ref={button} className="fact fact-btn" data-verified={verified} title={fact.label}
        aria-expanded={open} onClick={() => setOpen(!open)}>
        {fact.formatted}
      </button>
      {open && (
        <Provenance fact={fact} onClose={close}
          onAsk={anchor ? () => { setOpen(false); anchor(id); } : undefined} />
      )}
    </span>
  );
}
