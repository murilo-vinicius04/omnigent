import { beforeEach, describe, expect, it } from "vitest";
import {
  isNarrationEnabled,
  readSessionNarration,
  useNarrationStore,
  writeSessionNarration,
} from "./sessionNarrationPreferences";
import { SPOKEN_SUMMARY_PLAYBACK_STORAGE_KEY } from "./spokenSummaryPlaybackPreferences";

describe("session narration preference", () => {
  beforeEach(() => {
    window.localStorage.clear();
    useNarrationStore.setState({ overrides: {} });
  });

  it("falls back to the device default until the session decides", () => {
    // The device default is on, so a session nobody has touched narrates.
    expect(readSessionNarration("conv_1")).toBeNull();
    expect(isNarrationEnabled("conv_1")).toBe(true);

    window.localStorage.setItem(SPOKEN_SUMMARY_PLAYBACK_STORAGE_KEY, "false");
    expect(isNarrationEnabled("conv_1")).toBe(false);
  });

  it("lets a session override the device default in both directions", () => {
    window.localStorage.setItem(SPOKEN_SUMMARY_PLAYBACK_STORAGE_KEY, "true");
    writeSessionNarration("conv_quiet", false);
    expect(isNarrationEnabled("conv_quiet")).toBe(false);
    // The default still applies to a session that has not chosen.
    expect(isNarrationEnabled("conv_other")).toBe(true);

    window.localStorage.setItem(SPOKEN_SUMMARY_PLAYBACK_STORAGE_KEY, "false");
    writeSessionNarration("conv_loud", true);
    expect(isNarrationEnabled("conv_loud")).toBe(true);
  });

  it("keeps sessions independent of each other", () => {
    useNarrationStore.getState().setEnabled("conv_a", true);
    useNarrationStore.getState().setEnabled("conv_b", false);
    expect(useNarrationStore.getState().isEnabled("conv_a")).toBe(true);
    expect(useNarrationStore.getState().isEnabled("conv_b")).toBe(false);
  });

  it("survives a blocked store rather than throwing", () => {
    const getItem = window.localStorage.getItem;
    window.localStorage.getItem = () => {
      throw new Error("blocked");
    };
    expect(() => readSessionNarration("conv_1")).not.toThrow();
    expect(readSessionNarration("conv_1")).toBeNull();
    window.localStorage.getItem = getItem;
  });
});
