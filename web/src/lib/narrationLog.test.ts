import { beforeEach, describe, expect, it, vi } from "vitest";

const authenticatedFetch = vi.fn(
  async (_url: string, _init?: RequestInit) => ({ ok: true }) as Response,
);
vi.mock("./identity", () => ({
  authenticatedFetch: (url: string, init?: RequestInit) => authenticatedFetch(url, init),
}));

import { reportNarration, resetNarrationLog } from "./narrationLog";

describe("autoplay decisions reported to the server log", () => {
  beforeEach(() => {
    resetNarrationLog();
    authenticatedFetch.mockClear();
  });

  it("posts what was decided, and about which summary", async () => {
    reportNarration({
      path: "cross-session",
      decision: "too-old",
      sessionId: "conv_a",
      itemId: "resp_1",
      detail: "1800s old",
    });

    await vi.waitFor(() => {
      expect(authenticatedFetch).toHaveBeenCalledTimes(1);
    });
    const [url, init] = authenticatedFetch.mock.calls[0]!;
    expect(url).toBe("/v1/narration/decision");
    expect(JSON.parse(String(init?.body))).toEqual({
      path: "cross-session",
      decision: "too-old",
      session_id: "conv_a",
      item_id: "resp_1",
      detail: "1800s old",
    });
  });

  it("reports the same decision about the same summary once, since hooks re-run on every render", async () => {
    for (let i = 0; i < 5; i += 1) {
      reportNarration({
        path: "in-session",
        decision: "waiting-for-summary",
        sessionId: "conv_a",
        itemId: "resp_1",
      });
    }
    reportNarration({
      path: "in-session",
      decision: "handed-to-playback",
      sessionId: "conv_a",
      itemId: "resp_1",
    });

    await vi.waitFor(() => {
      expect(authenticatedFetch).toHaveBeenCalledTimes(2);
    });
  });

  it("never throws when the report cannot be sent", async () => {
    authenticatedFetch.mockRejectedValueOnce(new Error("offline"));
    expect(() => {
      reportNarration({ path: "playback", decision: "live-failed", sessionId: "conv_a" });
    }).not.toThrow();
    await vi.waitFor(() => {
      expect(authenticatedFetch).toHaveBeenCalledTimes(1);
    });
  });
});
