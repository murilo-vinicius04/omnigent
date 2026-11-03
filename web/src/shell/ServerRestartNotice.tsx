import { useEffect, useState } from "react";
import { authenticatedFetch } from "@/lib/identity";

interface Notice {
  id: string;
  phase: "planned" | "complete" | "failed";
  expires_at: number;
}

function isNotice(value: unknown): value is Notice {
  if (!value || typeof value !== "object") return false;
  const n = value as Record<string, unknown>;
  return (
    typeof n.id === "string" &&
    (n.phase === "planned" || n.phase === "complete" || n.phase === "failed") &&
    typeof n.expires_at === "number" &&
    n.expires_at > Date.now() / 1000
  );
}

/** Notices are public operational metadata; never put private messages in this file. */
export function ServerRestartNotice() {
  const [notice, setNotice] = useState<Notice | null>(null);
  const [dismissed, setDismissed] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    let pending = false;
    // A page opened after a restart has already loaded the new bundle.
    let plannedOnThisPage: string | null = null;
    const check = async () => {
      if (pending) return;
      pending = true;
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), 4000);
      try {
        const response = await authenticatedFetch(`/server-restart-notice.json?t=${Date.now()}`, {
          cache: "no-store",
          signal: controller.signal,
        });
        const data: unknown = response.ok ? await response.json() : null;
        if (isNotice(data) && data.phase === "planned") plannedOnThisPage = data.id;
        let refreshed = false;
        try {
          refreshed =
            isNotice(data) &&
            data.phase === "complete" &&
            sessionStorage.getItem("omnigent-restart-refreshed") === data.id;
        } catch {
          /* Storage may be disabled. */
        }
        const appliesToThisPage =
          isNotice(data) && (data.phase !== "complete" || data.id === plannedOnThisPage);
        if (alive) setNotice(appliesToThisPage && !refreshed ? data : null);
      } catch {
        // Keep the last notice visible during the server's brief outage.
        if (alive) setNotice((current) => (current && isNotice(current) ? current : null));
      } finally {
        window.clearTimeout(timeout);
        pending = false;
      }
    };
    void check();
    const timer = window.setInterval(() => void check(), 5000);
    window.addEventListener("focus", check);
    return () => {
      alive = false;
      window.clearInterval(timer);
      window.removeEventListener("focus", check);
    };
  }, []);

  if (
    !notice ||
    dismissed === `${notice.id}:${notice.phase}` ||
    notice.expires_at <= Date.now() / 1000
  )
    return null;
  const key = `${notice.id}:${notice.phase}`;
  return (
    <div
      role="status"
      aria-live="polite"
      className="fixed inset-x-4 top-4 z-[100] mx-auto flex max-w-xl items-center gap-3 rounded-xl border border-border bg-background p-4 text-sm shadow-lg"
    >
      <div className="min-w-0 flex-1">
        {notice.phase === "planned" &&
          "The server will restart shortly. This page may freeze; once the server is back, refresh it (F5)."}
        {notice.phase === "complete" &&
          "The Omnigent server is back. Refresh this page to load the latest version."}
        {notice.phase === "failed" &&
          "The server restart needs attention. The service may still be recovering."}
      </div>
      {notice.phase === "complete" && (
        <button
          type="button"
          className="rounded-md bg-primary px-3 py-2 text-primary-foreground"
          onClick={() => {
            try {
              sessionStorage.setItem("omnigent-restart-refreshed", notice.id);
            } catch {
              /* Storage may be disabled. */
            }
            window.location.reload();
          }}
        >
          Refresh
        </button>
      )}
      <button
        type="button"
        className="rounded-md px-2 py-2 text-muted-foreground hover:text-foreground"
        aria-label="Dismiss restart notice"
        onClick={() => setDismissed(key)}
      >
        ×
      </button>
    </div>
  );
}
