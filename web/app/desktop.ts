import { invoke, isTauri } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";

type DesktopInvoker = (command: string) => Promise<unknown>;

export async function takeDesktopBootstrapToken(
  invoker: DesktopInvoker = invoke,
): Promise<string | null> {
  const token = await invoker("take_desktop_bootstrap_token");
  if (token === null) return null;
  if (typeof token !== "string" || !token) {
    throw new Error("The desktop narration engine returned an invalid credential.");
  }
  return token;
}

export async function establishDesktopSession(
  fetcher: typeof fetch = fetch,
  tokenReader: () => Promise<string | null> = takeDesktopBootstrapToken,
): Promise<void> {
  const token = await tokenReader();
  if (token === null) return;
  const response = await fetcher("/api/desktop/session", {
    method: "POST",
    headers: {
      Accept: "application/json",
      "X-Doc2Audiobook-Desktop-Token": token,
    },
  });
  if (!response.ok) {
    throw new Error("The desktop narration engine could not be authenticated.");
  }
}

export function isDesktopRuntime(): boolean {
  return isTauri();
}

export async function chooseNativeDocument(
  picker: typeof open = open,
): Promise<string | null> {
  const selection = await picker({
    multiple: false,
    directory: false,
    filters: [
      {
        name: "Documents",
        extensions: ["docx", "pdf", "txt", "md"],
      },
    ],
  });
  return typeof selection === "string" ? selection : null;
}

export function documentNameFromPath(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).at(-1) ?? "";
}

export function extensionForName(name: string): string {
  const dotIndex = name.lastIndexOf(".");
  return dotIndex >= 0 ? name.slice(dotIndex).toLowerCase() : "";
}
