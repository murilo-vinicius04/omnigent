import { describe, expect, it } from "vitest";
import { itemsToBlocks } from "./itemsToBlocks";
import { BlockStream } from "./blockStream";
import type { MessageItem } from "./conversationItems";
import type { TextDone } from "./blocks";
import type { StreamEvent } from "./events";
import type { Response } from "./types";

describe("Spoken summary parsing paths shape validation", () => {
  const malformedPayloads: { name: string; content: unknown[] }[] = [
    {
      name: "object text",
      content: [
        { type: "output_text", text: "Answer text" },
        { type: "spoken_summary", text: { "pt-BR": "malformed object" }, lang: "pt-BR" },
      ],
    },
    {
      name: "missing text",
      content: [
        { type: "output_text", text: "Answer text" },
        { type: "spoken_summary", lang: "pt-BR" },
      ],
    },
    {
      name: "missing lang",
      content: [
        { type: "output_text", text: "Answer text" },
        { type: "spoken_summary", text: "Summary text" },
      ],
    },
    {
      name: "number text",
      content: [
        { type: "output_text", text: "Answer text" },
        { type: "spoken_summary", text: 12345, lang: "en-US" },
      ],
    },
    {
      name: "empty string text",
      content: [
        { type: "output_text", text: "Answer text" },
        { type: "spoken_summary", text: "", lang: "en-US" },
      ],
    },
    {
      name: "whitespace text",
      content: [
        { type: "output_text", text: "Answer text" },
        { type: "spoken_summary", text: "   ", lang: "en-US" },
      ],
    },
    {
      name: "empty lang",
      content: [
        { type: "output_text", text: "Answer text" },
        { type: "spoken_summary", text: "Summary text", lang: "" },
      ],
    },
    {
      name: "null entries in content",
      content: [null, { type: "output_text", text: "Answer text" }, undefined, null],
    },
  ];

  describe("Path 1: itemsToBlocks (loaded history)", () => {
    for (const { name, content } of malformedPayloads) {
      it(`drops malformed part without crashing: ${name}`, () => {
        const item = {
          id: "msg_1",
          type: "message",
          role: "assistant",
          content,
          response_id: "resp_1",
        } as unknown as MessageItem;

        expect(() => {
          const blocks = itemsToBlocks([item]);
          const textBlock = blocks.find((b) => b.type === "text_done") as TextDone | undefined;
          expect(textBlock).toBeDefined();
          expect(textBlock?.spokenSummary).toBeUndefined();
        }).not.toThrow();
      });
    }

    it("extracts valid spoken_summary correctly", () => {
      const item = {
        id: "msg_1",
        type: "message",
        role: "assistant",
        content: [
          { type: "output_text", text: "Full answer" },
          { type: "spoken_summary", text: "Valid summary", lang: "pt-BR" },
        ],
        response_id: "resp_1",
      } as unknown as MessageItem;

      const blocks = itemsToBlocks([item]);
      const textBlock = blocks.find((b) => b.type === "text_done") as TextDone | undefined;
      expect(textBlock).toBeDefined();
      expect(textBlock?.spokenSummary).toEqual({ text: "Valid summary", lang: "pt-BR" });
    });
  });

  describe("Path 2: blockStream message_done with prior streaming deltas", () => {
    for (const { name, content } of malformedPayloads) {
      it(`drops malformed part without crashing: ${name}`, () => {
        expect(() => {
          const stream = new BlockStream();
          const blocks = stream.reduceSync([
            {
              type: "response_created",
              response: { id: "resp_1", status: "in_progress" } as unknown as Response,
            },
            {
              type: "text_delta",
              delta: "Answer text",
            },
            {
              type: "message_done",
              itemId: "msg_1",
              responseId: "resp_1",
              content: content as Record<string, unknown>[],
            } as StreamEvent,
          ]);

          const textBlock = blocks.find((b) => b.type === "text_done") as TextDone | undefined;
          expect(textBlock).toBeDefined();
          expect(textBlock?.spokenSummary).toBeUndefined();
        }).not.toThrow();
      });
    }

    it("extracts valid spoken_summary when streaming deltas were open", () => {
      const stream = new BlockStream();
      const blocks = stream.reduceSync([
        {
          type: "response_created",
          response: { id: "resp_1", status: "in_progress" } as unknown as Response,
        },
        {
          type: "text_delta",
          delta: "Answer text",
        },
        {
          type: "message_done",
          itemId: "msg_1",
          responseId: "resp_1",
          content: [
            { type: "output_text", text: "Answer text" },
            { type: "spoken_summary", text: "Valid summary", lang: "pt-BR" },
          ],
        },
      ]);

      const textBlock = blocks.find((b) => b.type === "text_done") as TextDone | undefined;
      expect(textBlock).toBeDefined();
      expect(textBlock?.spokenSummary).toEqual({ text: "Valid summary", lang: "pt-BR" });
    });
  });

  describe("Path 3: blockStream message_done without prior deltas (fresh turn)", () => {
    for (const { name, content } of malformedPayloads) {
      it(`drops malformed part without crashing: ${name}`, () => {
        expect(() => {
          const stream = new BlockStream();
          const blocks = stream.reduceSync([
            {
              type: "message_done",
              itemId: "msg_1",
              responseId: "resp_1",
              content: content as Record<string, unknown>[],
            } as StreamEvent,
          ]);

          const textBlock = blocks.find((b) => b.type === "text_done") as TextDone | undefined;
          if (textBlock) {
            expect(textBlock.spokenSummary).toBeUndefined();
          }
        }).not.toThrow();
      });
    }

    it("extracts valid spoken_summary on fresh message_done", () => {
      const stream = new BlockStream();
      const blocks = stream.reduceSync([
        {
          type: "message_done",
          itemId: "msg_1",
          responseId: "resp_1",
          content: [
            { type: "output_text", text: "Fresh answer text" },
            { type: "spoken_summary", text: "Valid summary", lang: "pt-BR" },
          ],
        },
      ]);

      const textBlock = blocks.find((b) => b.type === "text_done") as TextDone | undefined;
      expect(textBlock).toBeDefined();
      expect(textBlock?.spokenSummary).toEqual({ text: "Valid summary", lang: "pt-BR" });
    });
  });
});
