import { describe, expect, it } from "vitest";
import { appFileLink, chatFileRef, parseChatFileRef } from "./chatFileRef";

const CHAT = "4a1867870bb74b44b3fb98f1ddd42887";

describe("hub file tabs", () => {
  it("round-trip a chat and a path", () => {
    const ref = chatFileRef(CHAT, "RA-L/reviews/REVISION_PLAN.md");
    expect(parseChatFileRef(ref)).toEqual({ chatId: CHAT, path: "RA-L/reviews/REVISION_PLAN.md" });
  });

  it.each(["docs/notes.md", `${CHAT}/`, `${CHAT}x/a.md`, "4A1867870BB74B44B3FB98F1DDD42887/a.md"])(
    "leave a plain path alone: %s",
    (path) => {
      expect(parseChatFileRef(path)).toBeNull();
    },
  );
});

describe("app file links", () => {
  it("name the chat, the file and the origin they were written for", () => {
    expect(
      appFileLink(
        `http://localhost:6767/c/${CHAT}?file=RA-L/reviews/REVISION_PLAN.md`,
        "http://nexus:6767/c/x",
      ),
    ).toEqual({
      chatId: CHAT,
      path: "RA-L/reviews/REVISION_PLAN.md",
      origin: "http://localhost:6767",
    });
  });

  it("decode the path and resolve a relative link against the page", () => {
    expect(appFileLink(`/c/${CHAT}?file=a%20b/c.md`, "http://localhost:6767/c/x")).toEqual({
      chatId: CHAT,
      path: "a b/c.md",
      origin: "http://localhost:6767",
    });
  });

  it.each([
    `http://localhost:6767/c/${CHAT}`,
    `http://localhost:6767/c/${CHAT}?file=`,
    `http://localhost:6767/c/${CHAT}/files?file=a.md`,
    `http://localhost:6767/x/${CHAT}?file=a.md`,
    "https://example.com/page",
    "mailto:someone@example.com",
  ])("ignore any other link: %s", (href) => {
    expect(appFileLink(href, "http://localhost:6767/")).toBeNull();
  });
});
