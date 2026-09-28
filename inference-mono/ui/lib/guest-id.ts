/**
 * Per-browser id for the homepage's unauthenticated sandbox (HYP-67).
 *
 * This is a SOFT, easily-cleared signal -- generated once and cached in
 * `localStorage` purely so a returning visitor's free-request count carries
 * over across page loads/tabs, not as an anti-abuse guarantee. Clearing site
 * data, using a private window, or switching browsers all mint a fresh id and
 * a fresh count. The server-side per-IP ceiling (see inference_api.guest_limits)
 * is the actual backstop; this id only feeds the much softer per-browser one.
 */

const GUEST_ID_STORAGE_KEY = "talaris-guest-id";

function randomId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  // Fallback for environments without crypto.randomUUID (very old browsers).
  return `guest-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/**
 * Reads (or mints and persists) this browser's guest id. Safe to call outside
 * the browser (returns a fresh, non-persisted id) and when storage access
 * throws (private mode / blocked storage) -- the sandbox still works, it just
 * won't remember the count across reloads for that visitor.
 */
export function getOrCreateGuestId(): string {
  if (typeof window === "undefined") return randomId();
  try {
    const existing = window.localStorage.getItem(GUEST_ID_STORAGE_KEY);
    if (existing) return existing;
    const id = randomId();
    window.localStorage.setItem(GUEST_ID_STORAGE_KEY, id);
    return id;
  } catch {
    return randomId();
  }
}
