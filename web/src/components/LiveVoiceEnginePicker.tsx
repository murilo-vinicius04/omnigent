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
  getLiveVoiceEngine,
  setLiveVoiceEngine,
  type GeminiAvailability,
} from "@/lib/liveVoiceEngine";
import { useLiveConversationStore } from "@/lib/liveConversation";
import { useEffect, useState } from "react";

/**
 * Picks which engine the spoken conversation runs on, beside the mic.
 *
 * GPT Live is the established path; Gemini Live is the alternative. The
 * choice is global (localStorage) rather than per-session because the
 * engine is a billing and capability decision, not a conversation topic.
 * The selector locks while a conversation is open — switching engines
 * mid-call would drop the reader's audio for a new handshake.
 */
export function LiveVoiceEnginePicker({ disabled }: { disabled?: boolean }) {
  const [engine, setEngine] = useState(getLiveVoiceEngine);
  const [geminiAvailability, setGeminiAvailability] = useState<GeminiAvailability>("unknown");
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
    return () => {
      cancelled = true;
    };
  }, []);

  const geminiDisabled = geminiAvailability === "unconfigured";

  return (
    <Select
      value={engine}
      onValueChange={(next) => {
        if (next !== "gpt" && next !== "gemini") return;
        setLiveVoiceEngine(next);
        setEngine(next);
      }}
      disabled={disabled || sessionActive}
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
          title={
            geminiDisabled ? "No Gemini key configured on the server" : "Gemini Live"
          }
        >
          Gemini Live
        </SelectItem>
      </SelectContent>
    </Select>
  );
}
