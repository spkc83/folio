import type { Section } from "../lib/api";

/**
 * How a section's own words were checked, in plain words. Code proves each claim against the
 * data. The words between the claims, and what the text leaves out, get a weaker check: a
 * second model reviews them, and its answer is a probability. The text says "not proven".
 */
const REVIEWED = "Reviewed by a second model. Probable, not proven.";
const OUTDATED = "Reviewed by a second model before the data changed. Not reviewed since.";
const NOT_REVIEWED = "Not reviewed.";

const WORDING = {
  connectors: "Only connecting words, such as “also”. Checked by code.",
  model: REVIEWED,
  code: "Fixed sentences that code wrote.",
  none: NOT_REVIEWED,
} as const;

/** The check of the words between the claims. */
export function wordingText(section: Section): string {
  const review = section.review;
  if (!review) return section.narration === "code" ? WORDING.code : NOT_REVIEWED;
  return review.wording === "model" && review.outdated ? OUTDATED : WORDING[review.wording];
}

/** The check that the text leaves out no important claim. Never more than probable. */
export function omissionText(section: Section): string {
  const review = section.review;
  if (review?.omissions !== "model") return NOT_REVIEWED;
  return review.outdated ? OUTDATED : REVIEWED;
}
