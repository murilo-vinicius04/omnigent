import { afterEach, describe, expect, it, vi } from "vitest";
import {
  DEFAULT_SPOKEN_SUMMARY_PLAYBACK,
  readSpokenSummaryPlayback,
  SPOKEN_SUMMARY_PLAYBACK_STORAGE_KEY,
  writeSpokenSummaryPlayback,
} from "./spokenSummaryPlaybackPreferences";

afterEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

describe("spokenSummaryPlaybackPreferences", () => {
  it("defaults to on when nothing is stored", () => {
    // Browser storage is per address, so a reader who reached the same server
    // by another URL used to lose narration silently. Off must be a choice.
    expect(DEFAULT_SPOKEN_SUMMARY_PLAYBACK).toBe(true);
    expect(readSpokenSummaryPlayback()).toBe(true);
  });

  it("round-trips both boolean values", () => {
    writeSpokenSummaryPlayback(true);
    expect(readSpokenSummaryPlayback()).toBe(true);

    writeSpokenSummaryPlayback(false);
    expect(readSpokenSummaryPlayback()).toBe(false);
  });

  it('treats any non-"true" stored value as off (defensive against hand edits)', () => {
    localStorage.setItem(SPOKEN_SUMMARY_PLAYBACK_STORAGE_KEY, "1");
    expect(readSpokenSummaryPlayback()).toBe(false);

    localStorage.setItem(SPOKEN_SUMMARY_PLAYBACK_STORAGE_KEY, "yes");
    expect(readSpokenSummaryPlayback()).toBe(false);

    localStorage.setItem(SPOKEN_SUMMARY_PLAYBACK_STORAGE_KEY, "true");
    expect(readSpokenSummaryPlayback()).toBe(true);
  });

  it("never throws when storage is inaccessible", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("quota exceeded");
    });
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("access denied");
    });
    expect(() => writeSpokenSummaryPlayback(true)).not.toThrow();
    // Unreadable storage means "no choice recorded", which is the default.
    expect(readSpokenSummaryPlayback()).toBe(true);
  });
});
