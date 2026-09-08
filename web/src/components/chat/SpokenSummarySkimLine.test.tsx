import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  resetSpeechEngine,
  setSpeechEngine,
  type SpeechEngine,
  useSpeechPlaybackStore,
} from "@/lib/speechPlayback";
import { SpokenSummarySkimLine } from "./SpokenSummarySkimLine";

class MockSpeechEngine implements SpeechEngine {
  isSupported = vi.fn().mockReturnValue(true);
  speak = vi.fn();
  stop = vi.fn();
  isSpeaking = vi.fn().mockReturnValue(false);
}

afterEach(() => {
  cleanup();
  resetSpeechEngine();
});

beforeEach(() => {
  setSpeechEngine(new MockSpeechEngine());
  useSpeechPlaybackStore.setState({ isSpeaking: false, speakingItemId: null });
});

describe("SpokenSummarySkimLine", () => {
  it("renders the skim-line with summary text and play button when idle", () => {
    render(
      <SpokenSummarySkimLine
        summary={{ text: "Here is a brief summary.", lang: "en-US" }}
        itemId="msg_1"
      />,
    );

    expect(screen.getByTestId("spoken-summary-skim-line")).toBeInTheDocument();
    expect(screen.getByTestId("spoken-summary-text")).toHaveTextContent("Here is a brief summary.");
    expect(screen.getByTestId("spoken-summary-play-button")).toBeInTheDocument();
    expect(screen.queryByTestId("spoken-summary-stop-button")).not.toBeInTheDocument();
  });

  it("renders the stop affordance when this message is currently speaking", () => {
    useSpeechPlaybackStore.setState({ isSpeaking: true, speakingItemId: "msg_1" });

    render(
      <SpokenSummarySkimLine
        summary={{ text: "Here is a brief summary.", lang: "en-US" }}
        itemId="msg_1"
      />,
    );

    expect(screen.getByTestId("spoken-summary-stop-button")).toBeInTheDocument();
    expect(screen.getByText("Stop")).toBeInTheDocument();
    expect(screen.queryByTestId("spoken-summary-play-button")).not.toBeInTheDocument();
  });

  it("renders play button when another message is speaking", () => {
    useSpeechPlaybackStore.setState({ isSpeaking: true, speakingItemId: "other_msg" });

    render(
      <SpokenSummarySkimLine
        summary={{ text: "Here is a brief summary.", lang: "en-US" }}
        itemId="msg_1"
      />,
    );

    expect(screen.getByTestId("spoken-summary-play-button")).toBeInTheDocument();
    expect(screen.queryByTestId("spoken-summary-stop-button")).not.toBeInTheDocument();
  });

  it("clicking the stop button stops audio playback", () => {
    useSpeechPlaybackStore.setState({ isSpeaking: true, speakingItemId: "msg_1" });

    render(
      <SpokenSummarySkimLine
        summary={{ text: "Here is a brief summary.", lang: "en-US" }}
        itemId="msg_1"
      />,
    );

    const stopBtn = screen.getByTestId("spoken-summary-stop-button");
    fireEvent.click(stopBtn);

    expect(useSpeechPlaybackStore.getState().isSpeaking).toBe(false);
    expect(useSpeechPlaybackStore.getState().speakingItemId).toBeNull();
  });

  it("with itemId null, asserts the Stop control appears while that message is speaking", () => {
    useSpeechPlaybackStore.setState({ isSpeaking: true, speakingItemId: "resp_null_item" });

    render(
      <SpokenSummarySkimLine
        summary={{ text: "Here is a brief summary.", lang: "en-US" }}
        id="resp_null_item"
        itemId={null}
      />,
    );

    expect(screen.getByTestId("spoken-summary-stop-button")).toBeInTheDocument();
    expect(screen.getByText("Stop")).toBeInTheDocument();
    expect(screen.queryByTestId("spoken-summary-play-button")).not.toBeInTheDocument();
  });

  it("renders without playback controls when effectiveId is empty string", () => {
    render(
      <SpokenSummarySkimLine
        summary={{ text: "Here is a brief summary.", lang: "en-US" }}
        id=""
        itemId=""
      />,
    );

    expect(screen.getByTestId("spoken-summary-skim-line")).toBeInTheDocument();
    expect(screen.getByTestId("spoken-summary-text")).toHaveTextContent("Here is a brief summary.");
    expect(screen.queryByTestId("spoken-summary-play-button")).not.toBeInTheDocument();
    expect(screen.queryByTestId("spoken-summary-stop-button")).not.toBeInTheDocument();
  });
});
