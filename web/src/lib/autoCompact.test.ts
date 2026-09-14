import { describe, expect, it } from "vitest";
import {
  autoCompactState,
  autoCompactStateText,
  clampThreshold,
  DEFAULT_THRESHOLD_PCT,
  MAX_THRESHOLD_PCT,
  MIN_THRESHOLD_PCT,
  STATE_LABEL,
  THRESHOLD_LABEL,
  thresholdPct,
} from "./autoCompact";

describe("the auto-compaction labels", () => {
  it("names the same labels the server writes", () => {
    // Both halves are duplicated across languages; if one is renamed the
    // control silently stops working, so pin the strings.
    expect(THRESHOLD_LABEL).toBe("omnigent.autocompact_pct");
    expect(STATE_LABEL).toBe("omnigent.autocompact_state");
  });

  it("falls back to the default when the session has not chosen one", () => {
    expect(thresholdPct(undefined)).toBe(DEFAULT_THRESHOLD_PCT);
    expect(thresholdPct({})).toBe(DEFAULT_THRESHOLD_PCT);
    expect(thresholdPct({ [THRESHOLD_LABEL]: "  " })).toBe(DEFAULT_THRESHOLD_PCT);
    expect(thresholdPct({ [THRESHOLD_LABEL]: "soon" })).toBe(DEFAULT_THRESHOLD_PCT);
  });

  it("reads a chosen threshold", () => {
    expect(thresholdPct({ [THRESHOLD_LABEL]: "45" })).toBe(45);
  });

  it("clamps a threshold the server would refuse", () => {
    expect(thresholdPct({ [THRESHOLD_LABEL]: "1" })).toBe(MIN_THRESHOLD_PCT);
    expect(thresholdPct({ [THRESHOLD_LABEL]: "400" })).toBe(MAX_THRESHOLD_PCT);
    expect(clampThreshold(62.4)).toBe(62);
  });
});

describe("what the ring says about compaction", () => {
  it("says nothing when compaction has nothing to report", () => {
    expect(autoCompactState(undefined)).toBeNull();
    expect(autoCompactState({ [STATE_LABEL]: "" })).toBeNull();
    expect(autoCompactState({ [STATE_LABEL]: "something-else" })).toBeNull();
    expect(autoCompactStateText(null)).toBeNull();
  });

  it("explains that a compacted session still shows its old size", () => {
    expect(autoCompactState({ [STATE_LABEL]: "compacted" })).toBe("compacted");
    // The reader watched exactly this and thought the compaction had failed.
    expect(autoCompactStateText("compacted")).toMatch(/updates when the next turn ends/);
  });

  it("says the notes are being written before the compaction", () => {
    expect(autoCompactState({ [STATE_LABEL]: "writing-notes" })).toBe("writing-notes");
    expect(autoCompactStateText("writing-notes")).toMatch(/Writing the context down/);
  });
});
