// Which parts of an answer the reader wants shown under its summary.
//
// The summary is meant to stay friendly, so showing everything defeats it: a
// reader who wants source under every answer and one who never does are both
// right, for themselves. Files are not listed here — an attached file was sent
// deliberately, so it always shows.

import type { SummaryShowBlock } from "./blockStream";

export const SUMMARY_SHOW_STORAGE_KEY = "omnigent:summary-show-kinds";

/** The kinds a reader can choose to see, in the order Settings lists them. */
export const CHOOSABLE_SHOW_KINDS = ["table", "image", "link", "output", "code"] as const;
export type ChoosableShowKind = (typeof CHOOSABLE_SHOW_KINDS)[number];

/**
 * On by default, except code.
 *
 * A table or an image is the answer's evidence and reads at a glance; source
 * under a spoken summary is noise for most turns, so it is opt-in.
 */
export const DEFAULT_SHOW_KINDS: Record<ChoosableShowKind, boolean> = {
  table: true,
  image: true,
  link: true,
  output: true,
  code: false,
};

/**
 * Read the reader's choice, falling back to the defaults.
 *
 * Never throws: blocked or corrupt storage reads as "no choice made".
 */
export function readShowKinds(): Record<ChoosableShowKind, boolean> {
  if (typeof window === "undefined") return { ...DEFAULT_SHOW_KINDS };
  try {
    const raw = window.localStorage.getItem(SUMMARY_SHOW_STORAGE_KEY);
    if (!raw) return { ...DEFAULT_SHOW_KINDS };
    const parsed = JSON.parse(raw) as Partial<Record<ChoosableShowKind, unknown>>;
    const out = { ...DEFAULT_SHOW_KINDS };
    for (const kind of CHOOSABLE_SHOW_KINDS) {
      if (typeof parsed?.[kind] === "boolean") out[kind] = parsed[kind] as boolean;
    }
    return out;
  } catch {
    return { ...DEFAULT_SHOW_KINDS };
  }
}

/** Persist the choice. Swallows quota/access errors. */
export function writeShowKinds(kinds: Record<ChoosableShowKind, boolean>): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(SUMMARY_SHOW_STORAGE_KEY, JSON.stringify(kinds));
  } catch {
    // Degrade gracefully
  }
}

/**
 * Drop the blocks this reader does not want to see.
 *
 * @param blocks Everything the summary carries.
 * @param kinds The reader's choice; read fresh when omitted.
 * @returns The blocks to render, files always among them.
 */
export function visibleShowBlocks(
  blocks: SummaryShowBlock[] | undefined,
  kinds: Record<ChoosableShowKind, boolean> = readShowKinds(),
): SummaryShowBlock[] {
  if (!blocks?.length) return [];
  return blocks.filter(
    (block) => block.kind === "file" || kinds[block.kind as ChoosableShowKind] === true,
  );
}
