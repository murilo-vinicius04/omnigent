import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FriendlyResponse } from "./FriendlyResponse";
import { useSpeechPlaybackStore } from "@/lib/speechPlayback";
import { useChatStore } from "@/store/chatStore";
import { useVoiceBackendStore } from "@/lib/sessionVoiceBackend";

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

  it("stays silent when the recording has been pruned", () => {
    // A session keeps only its newest recordings, so an older summary can name
    // one that is gone. The host's robotic voice is what the generated one
    // exists to replace, and the summary is already on screen: say nothing.
    const speak = vi.fn();
    useChatStore.setState({ conversationId: "conv_1" } as never);
    useSpeechPlaybackStore.setState({ playManual: speak } as never);
    vi.spyOn(window.HTMLMediaElement.prototype, "play").mockRejectedValue(new Error("gone"));

    render(
      <FriendlyResponse summary={withAudio} id="resp_gone">
        <div>original</div>
      </FriendlyResponse>,
    );
    fireEvent.click(screen.getByTestId("friendly-response-play"));

    return Promise.resolve().then(() => {
      expect(speak).not.toHaveBeenCalled();
      vi.restoreAllMocks();
    });
  });

  it("plays the generated audio from the read-aloud control, not the browser engine", () => {
    const play = vi.spyOn(window.HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
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

  it("does not layer the engine over a recording that starts late", () => {
    // A large recording on a slow link can reject play() and then start anyway
    // once data arrives. Speaking on rejection without pausing puts the engine
    // on top of it -- the same words twice, heard as an echo.
    const pause = vi.spyOn(window.HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
    vi.spyOn(window.HTMLMediaElement.prototype, "play").mockRejectedValue(new Error("stalled"));

    render(
      <FriendlyResponse summary={withAudio} id="resp_slow">
        <div>original</div>
      </FriendlyResponse>,
    );
    fireEvent.click(screen.getByTestId("friendly-response-play"));

    return Promise.resolve().then(() => {
      expect(pause).toHaveBeenCalled();
      vi.restoreAllMocks();
    });
  });

  it("silences any other summary player before starting this one", async () => {
    // Two streams of the same words, offset, is what an echo actually is. The
    // channel claim is the single place that guarantees only one can be live,
    // whichever path started the other.
    const other = document.createElement("audio");
    other.setAttribute("data-summary-audio", "");
    Object.defineProperty(other, "paused", { value: false, configurable: true });
    const otherPause = vi.fn();
    other.pause = otherPause;
    document.body.appendChild(other);

    vi.spyOn(window.HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);

    render(
      <FriendlyResponse summary={withAudio} id="resp_claim">
        <div>original</div>
      </FriendlyResponse>,
    );
    fireEvent.click(screen.getByTestId("friendly-response-play"));

    expect(otherPause).toHaveBeenCalled();
    document.body.removeChild(other);
    vi.restoreAllMocks();
  });

  it("says the recording is coming instead of reading it in the host voice", () => {
    // The written summary ships the moment it exists; synthesis takes tens of
    // seconds. Asking for the voice early must not answer with the robotic one.
    const speak = vi.fn();
    useSpeechPlaybackStore.setState({ playManual: speak } as never);

    render(
      <FriendlyResponse
        summary={{ text: "resumo", lang: "pt-BR", audioPending: true }}
        id="resp_pending"
      >
        <div>original</div>
      </FriendlyResponse>,
    );
    fireEvent.click(screen.getByTestId("friendly-response-play"));

    expect(screen.getByTestId("friendly-response-audio-pending")).toBeTruthy();
    expect(speak).not.toHaveBeenCalled();
  });

  it("plays the recording once it lands, and drops the waiting notice", () => {
    const { rerender } = render(
      <FriendlyResponse
        summary={{ text: "resumo", lang: "pt-BR", audioPending: true }}
        id="resp_late"
      >
        <div>original</div>
      </FriendlyResponse>,
    );
    fireEvent.click(screen.getByTestId("friendly-response-play"));
    expect(screen.getByTestId("friendly-response-audio-pending")).toBeTruthy();

    rerender(
      <FriendlyResponse
        summary={{ text: "resumo", lang: "pt-BR", audioFileId: "f_late" }}
        id="resp_late"
      >
        <div>original</div>
      </FriendlyResponse>,
    );

    expect(screen.queryByTestId("friendly-response-audio-pending")).toBeNull();
    expect(screen.getByTestId("friendly-response-audio").getAttribute("src")).toContain("f_late");
  });

  it("offers no read-aloud control when there is no recording and none coming", () => {
    // Nothing to play and nothing on its way: the control would only ever be
    // able to produce the robotic voice, so it is not shown at all.
    render(
      <FriendlyResponse summary={{ text: "sem audio", lang: "pt-BR" }} id="resp_2">
        <div>original</div>
      </FriendlyResponse>,
    );

    expect(screen.queryByTestId("friendly-response-play")).toBeNull();
    expect(screen.queryByTestId("friendly-response-audio")).toBeNull();
  });
});

describe("FriendlyResponse on the live voice", () => {
  const summary = { text: "All one hundred ninety nine tests pass.", lang: "en" };

  afterEach(() => {
    useVoiceBackendStore.setState({ choices: {} });
    window.localStorage.clear();
  });

  function chooseLive(sessionId: string) {
    useChatStore.setState({ conversationId: sessionId } as never);
    useVoiceBackendStore.getState().set(sessionId, "live");
  }

  it("offers the control with no recording, since none is needed", () => {
    chooseLive("conv_live");
    render(
      <FriendlyResponse summary={summary} id="resp_1">
        <div>ORIGINAL</div>
      </FriendlyResponse>,
    );
    // No audioFileId and nothing pending: the local voice would show nothing.
    expect(screen.getByTestId("friendly-response-play")).toBeTruthy();
  });

  it("reads through the live voice rather than the recording", () => {
    chooseLive("conv_live");
    const speakNow = vi.fn((_id: string, _text: string) => true);
    useSpeechPlaybackStore.setState({ speakNow } as never);

    render(
      <FriendlyResponse summary={{ ...summary, audioFileId: "file_1" }} id="resp_1">
        <div>ORIGINAL</div>
      </FriendlyResponse>,
    );
    fireEvent.click(screen.getByTestId("friendly-response-play"));

    // The bug this pins: the play button had its own audio element and
    // played the local recording, so choosing the live voice changed nothing.
    expect(speakNow).toHaveBeenCalled();
    expect(speakNow.mock.calls[0]?.[1]).toBe(summary.text);
  });

  it("never asks the reader to wait for a recording it will not use", () => {
    chooseLive("conv_live");
    useSpeechPlaybackStore.setState({ speakNow: vi.fn(() => true) } as never);
    render(
      <FriendlyResponse summary={{ ...summary, audioPending: true }} id="resp_1">
        <div>ORIGINAL</div>
      </FriendlyResponse>,
    );
    fireEvent.click(screen.getByTestId("friendly-response-play"));
    expect(screen.queryByTestId("friendly-response-audio-pending")).toBeNull();
  });

  it("still plays the recording when the session is on the local voice", () => {
    // jsdom's play() returns undefined rather than a promise.
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
    useChatStore.setState({ conversationId: "conv_local" } as never);
    const speakNow = vi.fn(() => true);
    useSpeechPlaybackStore.setState({ speakNow } as never);
    render(
      <FriendlyResponse summary={{ ...summary, audioFileId: "file_1" }} id="resp_1">
        <div>ORIGINAL</div>
      </FriendlyResponse>,
    );
    fireEvent.click(screen.getByTestId("friendly-response-play"));
    expect(speakNow).not.toHaveBeenCalled();
  });
});
