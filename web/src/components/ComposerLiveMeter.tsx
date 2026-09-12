import { useLiveConversationStore, conversationCostUsd } from "@/lib/liveConversation";

/** Render seconds as `m:ss`, which is how a call is read. */
function clock(seconds: number): string {
  const whole = Math.floor(seconds);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

/**
 * Shows how long the spoken conversation has been open and what it has cost.
 *
 * Only visible while one is open. A session bills by wall clock whether
 * anyone is talking or not, and it is closed only by the reader, so the
 * running cost has to be in front of them rather than discoverable later on
 * a bill. Nothing here can close the session: that is the mic button's job,
 * and two controls for one thing is how a session gets left open.
 */
export function ComposerLiveMeter() {
  const sessionId = useLiveConversationStore((s) => s.sessionId);
  const connecting = useLiveConversationStore((s) => s.connecting);
  const elapsedS = useLiveConversationStore((s) => s.elapsedS);

  if (connecting) {
    return (
      <span className="px-1 text-xs opacity-60" data-testid="composer-live-meter">
        connecting…
      </span>
    );
  }
  if (!sessionId) return null;

  return (
    <span
      className="flex items-center gap-1.5 px-1 text-xs tabular-nums"
      data-testid="composer-live-meter"
      title="This conversation is billed by wall clock, talking or not."
    >
      <span className="size-1.5 rounded-full bg-red-500" aria-hidden />
      <span>{clock(elapsedS)}</span>
      <span className="opacity-60">${conversationCostUsd(elapsedS).toFixed(3)}</span>
    </span>
  );
}
