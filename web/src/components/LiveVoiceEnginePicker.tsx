"use client";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  fetchGeminiLiveAvailability,
  fetchUnmuteLive,
  getLiveVoiceEngine,
  getUnmuteVoice,
  setLiveVoiceEngine,
  setUnmuteVoice,
  type GeminiAvailability,
  type UnmuteVoice,
} from "@/lib/liveVoiceEngine";
import { useLiveConversationStore } from "@/lib/liveConversation";
import { useEffect, useState } from "react";

/**
 * Picks which engine the spoken conversation runs on, beside the mic.
 *
 * GPT Live is the established path; Gemini Live and a local Unmute stack are
 * the alternatives. The
 * choice is global (localStorage) rather than per-session because the
 * engine is a billing and capability decision, not a conversation topic.
 * The selector locks while a conversation is open — switching engines
 * mid-call would drop the reader's audio for a new handshake.
 */
export function LiveVoiceEnginePicker({ disabled }: { disabled?: boolean }) {
  const [engine, setEngine] = useState(getLiveVoiceEngine);
  const [geminiAvailability, setGeminiAvailability] = useState<GeminiAvailability>("unknown");
  const [unmuteAvailability, setUnmuteAvailability] = useState<GeminiAvailability>("unknown");
  const [unmuteVoices, setUnmuteVoices] = useState<UnmuteVoice[]>([]);
  const [unmuteVoice, setUnmuteVoiceState] = useState<string | null>(getUnmuteVoice);
  const conversationSessionId = useLiveConversationStore((s) => s.sessionId);
  const conversationConnecting = useLiveConversationStore((s) => s.connecting);
  const sessionActive = Boolean(conversationSessionId) || conversationConnecting;

  useEffect(() => {
    let cancelled = false;
    void fetchGeminiLiveAvailability().then((status) => {
      if (cancelled) return;
      setGeminiAvailability(status);
      if (status === "unconfigured") {
        setEngine((current) => {
          if (current === "gemini") {
            setLiveVoiceEngine("gpt");
            return "gpt";
          }
          return current;
        });
      }
    });
    // Unmute is never switched away from: falling back would silently start
    // billing GPT Live. A stopped stack says so when a call is started.
    void fetchUnmuteLive().then(({ availability, voices, defaultVoice }) => {
      if (cancelled) return;
      setUnmuteAvailability(availability);
      setUnmuteVoices(voices);
      // Show the voice the server will actually use when none was chosen.
      setUnmuteVoiceState((current) =>
        current && voices.some((v) => v.id === current) ? current : defaultVoice,
      );
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const geminiDisabled = geminiAvailability === "unconfigured";

  const locked = disabled || sessionActive;

  return (
    <>
      <Select
        value={engine}
        onValueChange={(next) => {
          if (next !== "gpt" && next !== "gemini" && next !== "unmute") return;
          setLiveVoiceEngine(next);
          setEngine(next);
        }}
        disabled={locked}
      >
        <SelectTrigger
          size="sm"
          aria-label="Live voice engine"
          title={
            sessionActive
              ? "Cannot switch engines while a live conversation is open"
              : "Which live voice engine a spoken conversation uses"
          }
        >
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="gpt">GPT Live</SelectItem>
          <SelectItem
            value="gemini"
            disabled={geminiDisabled}
            title={geminiDisabled ? "No Gemini key configured on the server" : "Gemini Live"}
          >
            Gemini Live
          </SelectItem>
          <SelectItem
            value="unmute"
            title={
              unmuteAvailability === "unconfigured"
                ? "The local Unmute stack is not running"
                : "Kyutai Unmute on this machine's GPU (English only)"
            }
          >
            Unmute (local)
          </SelectItem>
        </SelectContent>
      </Select>
      {engine === "unmute" && unmuteVoices.length > 1 && unmuteVoice && (
        <Select
          value={unmuteVoice}
          onValueChange={(next) => {
            if (!unmuteVoices.some((v) => v.id === next)) return;
            setUnmuteVoice(next);
            setUnmuteVoiceState(next);
          }}
          disabled={locked}
        >
          <SelectTrigger
            size="sm"
            aria-label="Unmute voice"
            title={
              sessionActive
                ? "Cannot switch voices while a live conversation is open"
                : "Which voice Unmute speaks with"
            }
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {unmuteVoices.map((v) => (
              <SelectItem key={v.id} value={v.id}>
                {v.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
    </>
  );
}
