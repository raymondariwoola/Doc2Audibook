import { describe, expect, it, vi } from "vitest";

import {
  chooseNativeDocument,
  documentNameFromPath,
  establishDesktopSession,
  extensionForName,
  takeDesktopBootstrapToken,
} from "./desktop";

describe("desktop bootstrap", () => {
  it("reads the one-time credential through Tauri IPC instead of the URL", async () => {
    const invoker = vi.fn(async () => "secret-value");
    await expect(takeDesktopBootstrapToken(invoker)).resolves.toBe("secret-value");
    expect(invoker).toHaveBeenCalledWith("take_desktop_bootstrap_token");
  });

  it("rejects malformed credentials returned by the native command", async () => {
    const invoker = vi.fn(async () => 42);
    await expect(takeDesktopBootstrapToken(invoker)).rejects.toThrow(
      "invalid credential",
    );
  });

  it("establishes the HttpOnly session through the one-time header", async () => {
    const fetcher = vi.fn(async () => new Response("{}", { status: 200 }));
    await establishDesktopSession(
      fetcher as typeof fetch,
      async () => "secret",
    );
    expect(fetcher).toHaveBeenCalledWith(
      "/api/desktop/session",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({
          "X-Doc2Audiobook-Desktop-Token": "secret",
        }),
      }),
    );
  });

  it("allows reload to reuse an existing cookie after the token is consumed", async () => {
    const fetcher = vi.fn(async () => new Response("{}", { status: 200 }));
    await establishDesktopSession(fetcher as typeof fetch, async () => null);
    expect(fetcher).not.toHaveBeenCalled();
  });
});

describe("native document adapter", () => {
  it("normalizes Windows and macOS path names", () => {
    expect(documentNameFromPath(String.raw`C:\Books\My notes.md`)).toBe(
      "My notes.md",
    );
    expect(documentNameFromPath("/Users/me/Books/Story.docx")).toBe("Story.docx");
    expect(extensionForName("Story.DOCX")).toBe(".docx");
  });

  it("returns one selected path and ignores a cancelled dialog", async () => {
    const selected = vi.fn(async () => String.raw`C:\Books\Story.docx`);
    await expect(chooseNativeDocument(selected as never)).resolves.toContain(
      "Story.docx",
    );
    const cancelled = vi.fn(async () => null);
    await expect(chooseNativeDocument(cancelled as never)).resolves.toBeNull();
  });
});
