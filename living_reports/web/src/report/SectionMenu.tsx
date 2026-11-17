import { useEffect, useRef, useState, type KeyboardEvent } from "react";

export type Command = "shorter" | "expand" | "regenerate" | "remove";

type Props = {
  sectionId: string;
  canEdit: boolean;
  onCommand: (c: Command) => void;
  onSwap: (form: string) => void;
  /** Opens the wording editor. Without it, the menu has no "Edit wording" item. */
  onEdit?: () => void;
  forms: () => Promise<string[]>;
};

type Item = { label: string; hint?: string; wording?: boolean; run: () => void; keepOpen?: boolean };

export const FORM_LABELS: Record<string, string> = {
  bars: "Ranked bars", table: "Table", line: "Line", small_multiples: "Small multiples", delta_bars: "Delta bars", segmented: "Segmented bar",
};
const NO_RIGHTS = "Only an author of this report can change its words";

/** The commands of one section, as a menu button with arrow-key movement. */
export function SectionMenu({ sectionId, canEdit, onCommand, onSwap, onEdit, forms }: Props) {
  const [open, setOpen] = useState(false);
  const [level, setLevel] = useState<"main" | "forms" | "confirm">("main");
  const [available, setAvailable] = useState<string[] | null>(null);
  const [focus, setFocus] = useState(0);
  const button = useRef<HTMLButtonElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const items = useRef<(HTMLLIElement | null)[]>([]);

  const close = (refocus = true) => {
    setOpen(false);
    setLevel("main");
    if (refocus) button.current?.focus();
  };
  const showForms = () => {
    setLevel("forms");
    setAvailable(null);
    setFocus(0);
    forms().then(setAvailable, () => setAvailable([]));
  };

  const main: Item[] = [
    { label: "Shorter", hint: "Rewrite this section's words only", wording: true, run: () => onCommand("shorter") },
    { label: "Expand", hint: "Add one paragraph", wording: true, run: () => onCommand("expand") },
    { label: "Swap chart", hint: "Show the data in another form", run: showForms, keepOpen: true },
    { label: "Regenerate wording", wording: true, run: () => onCommand("regenerate") },
    ...(onEdit ? [{ label: "Edit wording", hint: "Facts stay tokens; checks run again", wording: true, run: onEdit }] : []),
    // Last, and behind one confirm step: removal is the one command that loses work.
    { label: "Remove section", wording: true, run: () => { setLevel("confirm"); setFocus(1); }, keepOpen: true },
  ];
  const confirmItems: Item[] = [
    { label: "Remove", run: () => onCommand("remove") },
    { label: "Cancel", run: () => { setLevel("main"); setFocus(main.length - 1); }, keepOpen: true },
  ];
  const formItems: Item[] = [
    ...(available ?? []).map((f) => ({ label: FORM_LABELS[f] ?? f, run: () => onSwap(f) })),
    { label: "Back", run: () => { setLevel("main"); setFocus(2); }, keepOpen: true },
  ];
  const shown = level === "main" ? main : level === "forms" ? formItems : confirmItems;
  const names = { main: "Section commands", forms: "Chart forms", confirm: "Remove this section?" };

  useEffect(() => { if (open) items.current[focus]?.focus(); }, [open, focus, level, available]);
  useEffect(() => {
    if (!open) return;
    const outside = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) close(false); };
    document.addEventListener("mousedown", outside);
    return () => document.removeEventListener("mousedown", outside);
  });

  const activate = (item: Item) => {
    if (item.wording && !canEdit) return;
    item.run();
    if (!item.keepOpen) close();
  };
  const onKey = (e: KeyboardEvent) => {
    const last = shown.length - 1;
    const moves: Record<string, number> = { ArrowDown: focus === last ? 0 : focus + 1, ArrowUp: focus === 0 ? last : focus - 1, Home: 0, End: last };
    if (e.key in moves) { e.preventDefault(); setFocus(moves[e.key]); return; }
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); activate(shown[focus]); return; }
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); return; }
    if (e.key === "ArrowLeft" && level !== "main") { e.preventDefault(); setLevel("main"); setFocus(2); return; }
    if (e.key === "Tab") close(false);
  };

  return (
    <div className={`menu${open ? " open" : ""}`} ref={box}>
      <button type="button" className="dots" ref={button} aria-label="Section commands" aria-haspopup="menu"
        aria-expanded={open} aria-controls={`menu-${sectionId}`}
        onClick={() => { if (open) close(); else { setOpen(true); setFocus(0); } }}>
        ⋯
      </button>
      {open && (
        <ul role="menu" id={`menu-${sectionId}`} aria-label={names[level]} onKeyDown={onKey}>
          {level === "confirm" && <li role="none" className="meta confirm-q">Remove this section?</li>}
          {level === "forms" && available === null && <li role="none" className="meta">Loading the forms…</li>}
          {shown.map((item, i) => {
            const disabled = Boolean(item.wording && !canEdit);
            return (
              <li key={item.label} role="menuitem" tabIndex={-1} aria-label={item.label}
                aria-disabled={disabled || undefined} title={disabled ? NO_RIGHTS : undefined}
                ref={(el) => { items.current[i] = el; }}
                onClick={() => activate(item)} onMouseEnter={() => setFocus(i)}>
                {item.label}
                {item.hint && <small>{item.hint}</small>}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
