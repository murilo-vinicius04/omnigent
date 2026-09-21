import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const KEY = "omnigent:live-voice-engine";

import type { GeminiAvailability } from "@/lib/liveVoiceEngine";

// Mutable knobs the mocked modules read at call time.
let availability: Promise<GeminiAvailability> = Promise.resolve("configured");
let unmuteAvailability: Promise<GeminiAvailability> = Promise.resolve("configured");
let liveSessionActive = false;

vi.mock("@/lib/liveVoiceEngine", () => ({
  getLiveVoiceEngine: () => {
    const stored = window.localStorage.getItem(KEY);
    return stored === "gemini" || stored === "unmute" ? stored : "gpt";
  },
  setLiveVoiceEngine: (engine: "gpt" | "gemini" | "unmute") => {
    window.localStorage.setItem(KEY, engine);
  },
  fetchGeminiLiveAvailability: () => availability,
  fetchUnmuteLiveAvailability: () => unmuteAvailability,
}));

vi.mock("@/lib/liveConversation", () => ({
  useLiveConversationStore: (
    sel: (s: { sessionId: string | null; connecting: boolean }) => unknown,
  ) => sel({ sessionId: liveSessionActive ? "s_1" : null, connecting: false }),
}));

import { LiveVoiceEnginePicker } from "./LiveVoiceEnginePicker";

beforeEach(() => {
  window.localStorage.clear();
  availability = Promise.resolve("configured");
  unmuteAvailability = Promise.resolve("configured");
  liveSessionActive = false;
});

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

function openMenu() {
  fireEvent.click(screen.getByRole("combobox", { name: "Live voice engine" }));
}

describe("LiveVoiceEnginePicker", () => {
  it("renders with GPT Live selected by default", async () => {
    render(<LiveVoiceEnginePicker />);
    await waitFor(() => {
      expect(screen.getByRole("combobox", { name: "Live voice engine" })).toHaveTextContent(
        "GPT Live",
      );
    });
  });

  it("disables the Gemini option when unconfigured", async () => {
    availability = Promise.resolve("unconfigured");
    render(<LiveVoiceEnginePicker />);
    openMenu();
    const option = await screen.findByRole("option", { name: /Gemini Live/ });
    expect(option).toHaveAttribute("aria-disabled", "true");
    expect(option).toHaveAttribute("title", "No Gemini key configured on the server");
  });

  it("stored gemini + unconfigured -> shows GPT and storage is gpt", async () => {
    window.localStorage.setItem(KEY, "gemini");
    availability = Promise.resolve("unconfigured");
    render(<LiveVoiceEnginePicker />);
    await waitFor(() => {
      expect(screen.getByRole("combobox", { name: "Live voice engine" })).toHaveTextContent(
        "GPT Live",
      );
    });
    expect(window.localStorage.getItem(KEY)).toBe("gpt");
  });

  it("stored gemini + unknown -> stays gemini and the option is enabled", async () => {
    window.localStorage.setItem(KEY, "gemini");
    availability = Promise.resolve("unknown");
    render(<LiveVoiceEnginePicker />);
    await waitFor(() => {
      expect(screen.getByRole("combobox", { name: "Live voice engine" })).toHaveTextContent(
        "Gemini Live",
      );
    });
    expect(window.localStorage.getItem(KEY)).toBe("gemini");
    openMenu();
    const option = await screen.findByRole("option", { name: /Gemini Live/ });
    expect(option).not.toHaveAttribute("aria-disabled", "true");
  });

  it("choosing Gemini persists the engine choice", async () => {
    render(<LiveVoiceEnginePicker />);
    openMenu();
    fireEvent.click(await screen.findByRole("option", { name: /Gemini Live/ }));
    await waitFor(() => {
      expect(window.localStorage.getItem(KEY)).toBe("gemini");
    });
  });

  it("choosing Unmute persists the engine choice", async () => {
    render(<LiveVoiceEnginePicker />);
    openMenu();
    fireEvent.click(await screen.findByRole("option", { name: /Unmute \(local\)/ }));
    await waitFor(() => {
      expect(window.localStorage.getItem(KEY)).toBe("unmute");
    });
  });

  it("a stopped Unmute stack never switches the reader to billed GPT Live", async () => {
    window.localStorage.setItem(KEY, "unmute");
    unmuteAvailability = Promise.resolve("unconfigured");
    render(<LiveVoiceEnginePicker />);
    openMenu();
    const option = await screen.findByRole("option", { name: /Unmute \(local\)/ });
    expect(option).toHaveAttribute("title", "The local Unmute stack is not running");
    expect(window.localStorage.getItem(KEY)).toBe("unmute");
  });

  it("is disabled while a live conversation is active", async () => {
    liveSessionActive = true;
    render(<LiveVoiceEnginePicker />);
    await act_wait();
    expect(screen.getByRole("combobox", { name: "Live voice engine" })).toBeDisabled();
  });
});

/** Let the availability promise resolve inside the React act zone. */
async function act_wait(): Promise<void> {
  await waitFor(() => {});
}
