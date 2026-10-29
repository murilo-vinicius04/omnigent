import { beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({ fetch: vi.fn() }));
vi.mock("@/lib/identity", () => ({ authenticatedFetch: h.fetch }));

import {
  openHub,
  openHubTerminal,
  openHubLane,
  refreshHubProjectBrief,
  setHubProjectMachine,
} from "@/lib/hubApi";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("openHub", () => {
  beforeEach(() => h.fetch.mockReset());

  it("returns the hub conversation's id", async () => {
    h.fetch.mockResolvedValueOnce(json({ session_id: "conv_hub", sessions: [] }));
    await expect(openHub()).resolves.toBe("conv_hub");
    expect(h.fetch).toHaveBeenCalledWith("/v1/hub");
  });

  it("says why it could not", async () => {
    h.fetch.mockResolvedValueOnce(json({ detail: "hub unavailable" }, 503));
    await expect(openHub()).rejects.toThrow("hub unavailable");
  });
});

describe("openHubTerminal", () => {
  beforeEach(() => h.fetch.mockReset());

  it("keeps the shell resource so the hub can attach before its list loads", async () => {
    h.fetch.mockResolvedValueOnce(
      json({
        session_id: "conv_shells",
        terminal: {
          id: "terminal_bash_hub",
          metadata: {
            terminal_name: "bash",
            session_key: "hub",
            running: true,
            direct_attach_url: "ws://127.0.0.1:9000/attach?token=test",
          },
        },
      }),
    );

    await expect(openHubTerminal(false)).resolves.toEqual({
      sessionId: "conv_shells",
      terminalId: "terminal_bash_hub",
      terminal: {
        id: "terminal_bash_hub",
        name: "bash",
        session: "hub",
        running: true,
        directAttachUrl: "ws://127.0.0.1:9000/attach?token=test",
      },
    });
    expect(h.fetch).toHaveBeenCalledWith(
      "/v1/hub/terminal",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ new: false }),
      }),
    );
  });

  it.each([
    { session_id: "", terminal: { id: "terminal_bash_hub" } },
    { session_id: "conv_shells", terminal: { id: "" } },
    { session_id: "conv_shells" },
  ])("rejects an incomplete shell response: %j", async (body) => {
    h.fetch.mockResolvedValueOnce(json(body));
    await expect(openHubTerminal(true)).rejects.toThrow("the server did not name a shell");
  });
});

it("carries the explicit machine and folder for lanes, projects and terminals", async () => {
  h.fetch.mockReset();
  const machine = { host_id: "remote", workspace: "/home/murilo/project" };
  h.fetch.mockResolvedValueOnce(json({ opened: true }));
  await openHubLane("project", { topic: "work", message: "read", ...machine });
  expect(JSON.parse(h.fetch.mock.calls.at(-1)![1].body)).toMatchObject(machine);
  h.fetch.mockResolvedValueOnce(json(machine));
  await setHubProjectMachine("project", machine);
  expect(h.fetch).toHaveBeenLastCalledWith(
    "/v1/hub/projects/project/machine",
    expect.objectContaining({
      method: "PUT",
      body: JSON.stringify(machine),
    }),
  );
  h.fetch.mockResolvedValueOnce(json({ session_id: "s", terminal: { id: "terminal_bash_t" } }));
  await openHubTerminal(true, machine);
  expect(JSON.parse(h.fetch.mock.calls.at(-1)![1].body)).toEqual({ new: true, ...machine });
});

describe("refreshHubProjectBrief", () => {
  beforeEach(() => h.fetch.mockReset());

  it("asks the server to write the brief and returns it", async () => {
    h.fetch.mockResolvedValueOnce(json({ project_id: "p 1", status: "Calibrating." }));
    await expect(refreshHubProjectBrief("p 1")).resolves.toMatchObject({
      status: "Calibrating.",
    });
    expect(h.fetch).toHaveBeenCalledWith("/v1/hub/projects/p%201/brief/refresh", {
      method: "POST",
    });
  });

  it("says why it could not", async () => {
    h.fetch.mockResolvedValueOnce(
      json({ detail: "no chat of this project has reported yet" }, 409),
    );
    await expect(refreshHubProjectBrief("p1")).rejects.toThrow("has reported yet");
  });
});
