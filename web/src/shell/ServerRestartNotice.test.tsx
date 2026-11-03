import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ServerRestartNotice } from "./ServerRestartNotice";

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  sessionStorage.clear();
});

it("warns before a restart and offers a refresh when it completes", async () => {
  vi.useFakeTimers();
  const expires_at = Date.now() / 1000 + 900;
  let phase = "planned";
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ ok: true, json: async () => ({ id: "a", phase, expires_at }) })),
  );
  render(<ServerRestartNotice />);
  await act(async () => {});
  expect(screen.getByText(/will restart shortly/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Dismiss restart notice" }));
  expect(screen.queryByText(/will restart shortly/)).toBeNull();
  phase = "complete";
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(screen.getByText(/server is back/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Refresh" })).toBeInTheDocument();
  vi.unstubAllGlobals();
});

it("ignores expired notices", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: true,
      json: async () => ({ id: "a", phase: "complete", expires_at: 1 }),
    })),
  );
  render(<ServerRestartNotice />);
  await act(async () => {});
  expect(screen.queryByRole("status")).toBeNull();
  vi.unstubAllGlobals();
});

it("does not ask for another refresh when the page was opened after the restart", async () => {
  const expires_at = Date.now() / 1000 + 900;
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: true,
      json: async () => ({ id: "already-finished", phase: "complete", expires_at }),
    })),
  );
  render(<ServerRestartNotice />);
  await act(async () => {});
  expect(screen.queryByRole("status")).toBeNull();
  expect(screen.queryByRole("button", { name: "Refresh" })).toBeNull();
  vi.unstubAllGlobals();
});

it("keeps the advance warning visible during a temporary server outage", async () => {
  vi.useFakeTimers();
  const expires_at = Date.now() / 1000 + 900;
  let online = true;
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      if (!online) throw new Error("offline");
      return { ok: true, json: async () => ({ id: "planned", phase: "planned", expires_at }) };
    }),
  );
  render(<ServerRestartNotice />);
  await act(async () => {});
  expect(screen.getByText(/once the server is back, refresh it/)).toBeInTheDocument();
  online = false;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(screen.getByText(/will restart shortly/)).toBeInTheDocument();
  vi.unstubAllGlobals();
});
