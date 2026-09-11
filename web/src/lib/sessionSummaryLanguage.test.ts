import { describe, expect, it, vi } from "vitest";
import {
  otherLanguage,
  readSummaryLanguage,
  shortLanguageLabel,
  writeSummaryLanguage,
} from "./sessionSummaryLanguage";

vi.mock("./sessionsApi", () => ({ updateSession: vi.fn().mockResolvedValue({}) }));
import { updateSession } from "./sessionsApi";

describe("sessionSummaryLanguage", () => {
  it("reads a configured language off the session's labels", () => {
    expect(readSummaryLanguage({ spoken_summary_language: "en-US" })).toBe("en-US");
    expect(readSummaryLanguage({ spoken_summary_language: " pt-BR " })).toBe("pt-BR");
  });

  it("treats an unset or unknown language as no session choice", () => {
    // Falls through to the project or server default rather than inventing one.
    expect(readSummaryLanguage(undefined)).toBeNull();
    expect(readSummaryLanguage({})).toBeNull();
    expect(readSummaryLanguage({ spoken_summary_language: "fr-FR" })).toBeNull();
  });

  it("writes the enabled flag alongside the language", async () => {
    // The server reads the label pair as a unit: a language written on its own
    // never reaches the branch that resolves it, and is silently ignored.
    await writeSummaryLanguage("conv_1", "en-US");
    expect(updateSession).toHaveBeenCalledWith("conv_1", {
      labels: { spoken_summary_enabled: "true", spoken_summary_language: "en-US" },
      silent: true,
    });
  });

  it("toggles between the two languages", () => {
    expect(otherLanguage("pt-BR")).toBe("en-US");
    expect(otherLanguage("en-US")).toBe("pt-BR");
    expect(shortLanguageLabel("pt-BR")).toBe("PT");
    expect(shortLanguageLabel("en-US")).toBe("EN");
  });
});
