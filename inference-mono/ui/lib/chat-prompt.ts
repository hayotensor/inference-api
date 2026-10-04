/**
 * Home-page "try a prompt" box → chat app handoff.
 *
 * The chat front door's URL is env-configured; see the same read in
 * `components/dashboard-shell.tsx` and `lib/auth.tsx` (each keeps its own
 * copy rather than importing one another's — see this file's ownership
 * notes in the surrounding task).
 */
const CHAT_URL = process.env.NEXT_PUBLIC_CHAT_URL || "/chat";

/**
 * Builds a URL that opens the chat app with `prompt` sitting in the
 * composer, unsent. Open-WebUI's `Chat.svelte` already reads a `q` query
 * param to seed the message input and a `submit` param to decide whether to
 * send it immediately (defaulting to `true` when `q` is present) — so
 * `submit=false` is required here, not optional, or the prompt would fire
 * before the user ever sees it.
 */
export function buildChatPromptUrl(prompt: string): string {
  const trimmed = prompt.trim();
  if (!trimmed) return CHAT_URL;

  const params = new URLSearchParams({ q: trimmed, submit: "false" });
  const separator = CHAT_URL.includes("?") ? "&" : "?";
  return `${CHAT_URL}${separator}${params.toString()}`;
}
