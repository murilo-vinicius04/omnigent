import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FriendlyResponse } from "./FriendlyResponse";
import { useSpeechPlaybackStore } from "@/lib/speechPlayback";
import { useChatStore } from "@/store/chatStore";

afterEach(cleanup);

describe("FriendlyResponse", () => {
  const summary = { text: "Consertei o vazamento e os testes passaram.", lang: "pt-BR" };

  it("shows the rewrite by default and keeps the original hidden", () => {
    render(
      <FriendlyResponse summary={summary} id="resp_1">
        <div>ORIGINAL WITH `code`</div>
      </FriendlyResponse>,
    );

    expect(screen.getByTestId("friendly-response-text").textContent).toBe(summary.text);
    expect(screen.queryByTestId("friendly-response-original")).toBeNull();
  });

  it("reveals the original on click and hides it again", () => {
    render(
      <FriendlyResponse summary={summary} id="resp_1">
        <div>ORIGINAL WITH `code`</div>
      </FriendlyResponse>,
    );

    const toggle = screen.getByTestId("friendly-response-toggle");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");

    fireEvent.click(toggle);
    // The original must always be reachable: the rewrite drops code on purpose.
    expect(screen.getByTestId("friendly-response-original").textContent).toContain(
      "ORIGINAL WITH `code`",
    );
    expect(toggle.getAttribute("aria-expanded")).toBe("true");

    fireEvent.click(toggle);
    expect(screen.queryByTestId("friendly-response-original")).toBeNull();
  });
});

describe("FriendlyResponse — server-synthesized audio", () => {
  const withAudio = { text: "Consertei o vazamento.", lang: "pt-BR", audioFileId: "f_audio_1" };

  it("falls back to the browser engine when the recording has been pruned", async () => {
    // A session keeps only its newest recordings, so an older summary still
    // names an audio file whose bytes are gone. The control must speak, not
    // sit silent.
    const play = vi
      .spyOn(window.HTMLMediaElement.prototype, "play")
      .mockRejectedValue(new Error("404"));
    const speak = vi
      .spyOn(useSpeechPlaybackStore.getState(), "playManual")
      .mockImplementation(() => {});
    useChatStore.setState({ conversationId: "conv_1" } as never);

    render(
      <FriendlyResponse summary={withAudio} id="resp_gone">
        <div>original</div>
      </FriendlyResponse>,
    );

    fireEvent.click(screen.getByTestId("friendly-response-play"));
    await vi.waitFor(() => expect(speak).toHaveBeenCalled());
    expect(speak).toHaveBeenCalledWith("resp_gone", withAudio.text, withAudio.lang);

    play.mockRestore();
    speak.mockRestore();
  });

  it("plays the generated audio from the read-aloud control, not the browser engine", () => {
    const play = vi
      .spyOn(window.HTMLMediaElement.prototype, "play")
      .mockResolvedValue(undefined);
    const speak = vi.spyOn(useSpeechPlaybackStore.getState(), "playManual");
    useChatStore.setState({ conversationId: "conv_1" } as never);

    render(
      <FriendlyResponse summary={withAudio} id="resp_1">
        <div>original</div>
      </FriendlyResponse>,
    );

    const audio = screen.getByTestId("friendly-response-audio");
    expect(audio.getAttribute("src")).toContain("/resources/files/f_audio_1/content");

    fireEvent.click(screen.getByTestId("friendly-response-play"));
    expect(play).toHaveBeenCalled();
    // The browser speech engine must not also fire — that was the old voice.
    expect(speak).not.toHaveBeenCalled();
    play.mockRestore();
  });

  it("keeps the browser engine when the summary carries no audio", () => {
    const play = vi
      .spyOn(window.HTMLMediaElement.prototype, "play")
      .mockResolvedValue(undefined);
    render(
      <FriendlyResponse summary={{ text: "sem audio", lang: "pt-BR" }} id="resp_2">
        <div>original</div>
      </FriendlyResponse>,
    );

    expect(screen.queryByTestId("friendly-response-audio")).toBeNull();
    fireEvent.click(screen.getByTestId("friendly-response-play"));
    expect(play).not.toHaveBeenCalled();
    play.mockRestore();
  });
});
