import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";

import { AgentActivity } from "./AgentActivity";

vi.mock("@/lib/identity", () => ({
  authenticatedFetch: vi.fn(async () => ({
    ok: true,
    json: async () => ({
      notes: [
        {
          id: "1",
          created_at: 1,
          from_user: "hipolito",
          from_title: "Replay",
          to_title: "Camera",
          topic: "Coordination",
          text: "Ready to help",
          status: "queued",
        },
      ],
      shared_sessions: [
        {
          id: "s1",
          title: "Replay",
          project: "Franka",
          owner: "hipolito",
          status: "working",
          task_summary: "Analyzing replay",
        },
      ],
    }),
  })),
}));

afterEach(cleanup);

it("shows shared work and message status without opening an inbox", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <AgentActivity />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Agent activity" }));
  expect(await screen.findByText("Ready to help")).toBeInTheDocument();
  expect(screen.getByText("Analyzing replay")).toBeInTheDocument();
  expect(screen.getByText(/Queued/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Replay" })).toHaveAttribute("href", "/c/s1");
  client.clear();
});
