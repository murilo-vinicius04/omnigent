import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { useSpeechPlaybackStore } from "@/lib/speechPlayback";
import { SpokenSummarySkimLine } from "./SpokenSummarySkimLine";

afterEach(cleanup);

beforeEach(() => {
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
});
