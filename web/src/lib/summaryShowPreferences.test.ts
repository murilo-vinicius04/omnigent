import { beforeEach, describe, expect, it } from "vitest";
import type { SummaryShowBlock } from "@/lib/blockStream";
import {
  DEFAULT_SHOW_KINDS,
  SUMMARY_SHOW_STORAGE_KEY,
  readShowKinds,
  visibleShowBlocks,
  writeShowKinds,
} from "@/lib/summaryShowPreferences";

const block = (kind: SummaryShowBlock["kind"]): SummaryShowBlock => ({
  kind,
  label: kind,
  content: "x",
});

describe("summaryShowPreferences", () => {
  beforeEach(() => localStorage.clear());

  it("shows evidence by default and leaves code out", () => {
    expect(DEFAULT_SHOW_KINDS.table).toBe(true);
    expect(DEFAULT_SHOW_KINDS.image).toBe(true);
    expect(DEFAULT_SHOW_KINDS.code).toBe(false);
  });

  it("round-trips the reader's choice", () => {
    writeShowKinds({ ...DEFAULT_SHOW_KINDS, code: true, link: false });
    expect(readShowKinds().code).toBe(true);
    expect(readShowKinds().link).toBe(false);
  });

  it("falls back to the defaults on corrupt storage", () => {
    localStorage.setItem(SUMMARY_SHOW_STORAGE_KEY, "{not json");
    expect(readShowKinds()).toEqual(DEFAULT_SHOW_KINDS);
  });

  it("keeps a file whatever the reader chose", () => {
    // Attaching a file was already the decision to show it.
    writeShowKinds({ table: false, image: false, link: false, output: false, code: false });
    const kept = visibleShowBlocks([block("table"), block("file")]);
    expect(kept.map((b) => b.kind)).toEqual(["file"]);
  });

  it("drops unknown kinds rather than rendering them", () => {
    const kept = visibleShowBlocks([{ kind: "weird" as never, label: "", content: "x" }]);
    expect(kept).toEqual([]);
  });
});
