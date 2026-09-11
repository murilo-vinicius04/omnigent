import { useEffect, useRef, useState } from "react";
import { ChevronRightIcon, SquareIcon, Volume2Icon } from "lucide-react";
import { cn } from "@/lib/utils";
import { claimSpeechChannel, useSpeechPlaybackStore } from "@/lib/speechPlayback";
import { useChatStore } from "@/store/chatStore";

export interface FriendlyResponseProps {
  /** The rewritten, reader-facing version of the reply. */
  summary: { text: string; lang: string; audioFileId?: string; audioPending?: boolean };
  /** Stable identifier for playback tracking (the turn's responseId). */
  id?: string;
  /** The model's original reply, one click away. */
  children: React.ReactNode;
}

/**
 * Renders the friendly rewrite as the answer, with the original behind a toggle.
 *
 * The rewrite is produced by a separate cheap model and reproduces every fact,
 * command and path from the original — it is a rewrite, not a summary — so it
 * can stand as the default view. The original stays one click away because a
 * rewrite is still a second model's account of the first one's work, and for
 * anything load-bearing the reader needs the source.
 */
export function FriendlyResponse({ summary, id, children }: FriendlyResponseProps) {
  const [showOriginal, setShowOriginal] = useState(false);
  const [audioPlaying, setAudioPlaying] = useState(false);
  // Set when the reader asks for a recording that is still being made, so the
  // control can say so instead of answering with the robotic host voice.
  const [waitingForAudio, setWaitingForAudio] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const speakingItemId = useSpeechPlaybackStore((s) => s.speakingItemId);
  const stop = useSpeechPlaybackStore((s) => s.stop);
  const sessionId = useChatStore((s) => s.conversationId);

  const effectiveId = id || "";
  // Server-synthesized audio when this summary has it; otherwise the control
  // falls back to the browser's own speech engine.
  const audioUrl =
    summary.audioFileId && sessionId
      ? `/v1/sessions/${encodeURIComponent(sessionId)}/resources/files/${encodeURIComponent(summary.audioFileId)}/content`
      : undefined;
  const autoplayOwnsThis = Boolean(effectiveId && speakingItemId === effectiveId);
  const isSpeaking = audioUrl ? audioPlaying || autoplayOwnsThis : autoplayOwnsThis;

  useEffect(() => {
    if (audioUrl) setWaitingForAudio(false);
  }, [audioUrl]);

  // Stop the audio if this bubble goes away mid-playback.
  useEffect(() => {
    const el = audioRef.current;
    return () => {
      el?.pause();
    };
  }, []);

  const toggleSpeech = () => {
    // The written summary lands as soon as it exists; its recording follows a
    // good while later. Asking for it early gets an answer, not silence.
    if (!audioUrl && summary.audioPending) {
      setWaitingForAudio(true);
      return;
    }
    if (audioUrl) {
      const el = audioRef.current;
      if (!el) return;
      if (audioPlaying || autoplayOwnsThis) {
        // Covers both streams: this element, and the one autoplay owns.
        el.pause();
        el.currentTime = 0;
        setAudioPlaying(false);
        stop();
        return;
      }
      // Sole owner of the channel: silences the engine and any other player.
      claimSpeechChannel(el, sessionId);
      void el.play().then(
        () => setAudioPlaying(true),
        () => {
          // The recording would not start: a session keeps only its newest
          // ones, so an older summary outlives its audio. Stay silent -- the
          // summary is on screen, and the host's robotic voice is what the
          // generated one exists to replace.
          el.pause();
          el.currentTime = 0;
          setAudioPlaying(false);
        },
      );
      return;
    }
    if (isSpeaking) stop();
  };

  return (
    <div data-testid="friendly-response" className="min-w-0">
      <div data-testid="friendly-response-text" className="min-w-0 whitespace-pre-wrap">
        {summary.text}
      </div>

      <div className="mt-1.5 flex items-center gap-3 text-xs text-muted-foreground">
        <button
          type="button"
          onClick={() => setShowOriginal((v) => !v)}
          data-testid="friendly-response-toggle"
          aria-expanded={showOriginal}
          className="inline-flex items-center gap-0.5 rounded hover:text-foreground focus-visible:outline-none"
        >
          <ChevronRightIcon
            className={cn("size-3 transition-transform", showOriginal && "rotate-90")}
            aria-hidden="true"
          />
          {showOriginal ? "Hide original" : "Show original"}
        </button>

        {(audioUrl || summary.audioPending) && (
          <button
            type="button"
            onClick={toggleSpeech}
            data-testid={isSpeaking ? "friendly-response-stop" : "friendly-response-play"}
            aria-label={isSpeaking ? "Stop reading aloud" : "Read aloud"}
            className="inline-flex items-center gap-1 rounded hover:text-foreground focus-visible:outline-none"
          >
            {isSpeaking ? (
              <>
                <SquareIcon className="size-3 fill-current text-primary" aria-hidden="true" />
                <span className="font-medium text-primary">Stop</span>
              </>
            ) : (
              <Volume2Icon
                className={cn("size-3.5", !audioUrl && summary.audioPending && "opacity-60")}
                aria-hidden="true"
              />
            )}
          </button>
        )}

        {waitingForAudio && !audioUrl && (
          <span data-testid="friendly-response-audio-pending" aria-live="polite">
            Recording it now — one moment.
          </span>
        )}
      </div>

      {audioUrl && (
        <audio
          ref={audioRef}
          src={audioUrl}
          preload="none"
          data-summary-audio=""
          data-testid="friendly-response-audio"
          onEnded={() => setAudioPlaying(false)}
          onError={() => setAudioPlaying(false)}
        />
      )}

      {showOriginal && (
        <div
          data-testid="friendly-response-original"
          className="mt-2 border-l-2 border-border pl-3"
        >
          {children}
        </div>
      )}
    </div>
  );
}
