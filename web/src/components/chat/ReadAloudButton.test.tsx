import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ReadAloudButton } from "./ReadAloudButton";
import { useSpeechPlaybackStore } from "@/lib/speechPlayback";
import { useChatStore } from "@/store/chatStore";
import { useVoiceBackendStore } from "@/lib/sessionVoiceBackend";

afterEach(() => {
  cleanup();
  useSpeechPlaybackStore.setState({ speakingItemId: null, isSpeaking: false });
});

describe("ReadAloudButton", () => {
  const summary = { text: "RAL terminou a compilação.", lang: "pt-BR" };

  it("reads a hub message again in the live voice, under its own turn", () => {
    const speakNow = vi.fn();
    useChatStore.setState({ conversationId: "hub_1" } as never);
    useVoiceBackendStore.getState().set("hub_1", "live");
    useSpeechPlaybackStore.setState({ speakNow } as never);

    render(<ReadAloudButton summary={summary} id="resp_report" />);
    fireEvent.click(screen.getByTestId("read-aloud-play"));

    expect(speakNow).toHaveBeenCalledWith(
      "resp_report",
      "RAL terminou a compilação.",
      "pt-BR",
      undefined,
      "hub_1",
    );
  });

  it("stops the reading when its turn is the one being read", () => {
    const stop = vi.fn();
    useChatStore.setState({ conversationId: "hub_1" } as never);
    useVoiceBackendStore.getState().set("hub_1", "live");
    useSpeechPlaybackStore.setState({ speakingItemId: "resp_report", stop } as never);

    render(<ReadAloudButton summary={summary} id="resp_report" />);
    fireEvent.click(screen.getByTestId("read-aloud-stop"));

    expect(stop).toHaveBeenCalled();
  });

  it("shows nothing when there is neither a live voice nor a recording", () => {
    useChatStore.setState({ conversationId: "conv_2" } as never);
    useVoiceBackendStore.getState().set("conv_2", "local");

    render(<ReadAloudButton summary={summary} id="resp_x" />);

    expect(screen.queryByTestId("read-aloud-play")).toBeNull();
  });
});
