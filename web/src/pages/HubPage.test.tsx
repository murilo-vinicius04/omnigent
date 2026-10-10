import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({ openHub: vi.fn() }));
vi.mock("@/lib/hubApi", () => ({ openHub: h.openHub }));

import { HubPage } from "@/pages/HubPage";

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/hub" element={<HubPage />} />
          <Route path="/c/:conversationId" element={<p data-testid="chat">chat</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("HubPage", () => {
  beforeEach(() => h.openHub.mockReset());

  it("opens the hub conversation in the regular chat", async () => {
    h.openHub.mockResolvedValueOnce("conv_hub");
    renderAt("/hub");
    await waitFor(() => expect(screen.getByTestId("chat")).toBeTruthy());
  });

  it("says when the hub cannot be opened", async () => {
    h.openHub.mockRejectedValueOnce(new Error("hub unavailable"));
    renderAt("/hub");
    await waitFor(() =>
      expect(screen.getByTestId("hub-opening").textContent).toContain("hub unavailable"),
    );
  });
});
