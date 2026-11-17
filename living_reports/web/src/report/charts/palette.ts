/** Six categorical slots, validated in light and dark (see theme.css). */
export const SLOTS = 6;

/**
 * The color for one member. ``keys`` is the full ordered member list, not the filtered one,
 * so a member keeps its color when others are filtered out.
 */
export function slotColor(key: string, keys: string[]): string {
  const index = keys.indexOf(key);
  return index >= 0 && index < SLOTS ? `var(--c${index + 1})` : "var(--gray)";
}
