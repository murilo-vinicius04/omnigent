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
  it("defaults to off when nothing is stored", () => {
    expect(DEFAULT_SPOKEN_SUMMARY_PLAYBACK).toBe(false);
    expect(readSpokenSummaryPlayback()).toBe(false);
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
    expect(readSpokenSummaryPlayback()).toBe(false);
  });
});
