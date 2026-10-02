"use client";

import { useState } from "react";
import Link from "next/link";
import { ArrowRight, Loader2, ShieldCheck } from "lucide-react";

import { PublicNav } from "@/components/public-nav";
import { SiteFooter } from "@/components/site-footer";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { ApiError, apiErrorMessage, guestApi } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { buildChatPromptUrl } from "@/lib/chat-prompt";
import { getOrCreateGuestId } from "@/lib/guest-id";

/**
 * The homepage's anonymous sandbox result (HYP-67). Signed-out visitors get a
 * small number of free, capped-model, capped-length completions rendered
 * right here instead of being sent to /login -- see `lib/api.ts#guestApi` and
 * `inference_api/guest_routes.py` for the two server-enforced daily ceilings
 * (per-browser AND per-IP) backing this.
 */
type SandboxState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "success"; output: string; remaining: number }
  | { status: "limited"; message: string }
  | { status: "error"; message: string };

export default function HomePage() {
  const { isAuthenticated, ensureSsoCookie } = useAuth();
  const [prompt, setPrompt] = useState("");
  const [sandbox, setSandbox] = useState<SandboxState>({ status: "idle" });

  async function handlePromptSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = prompt.trim();
    if (!trimmed) return;

    if (!isAuthenticated) {
      // No account yet: run it through the free, capped sandbox right here
      // instead of bouncing to /login. Both the model and the response length
      // are forced server-side, and the server independently caps this per
      // browser AND per IP -- see guestApi's docstring.
      setSandbox({ status: "loading" });
      try {
        const guestId = getOrCreateGuestId();
        const result = await guestApi.inference(trimmed, guestId);
        setSandbox({ status: "success", output: result.output, remaining: result.remaining_today });
      } catch (error) {
        if (error instanceof ApiError && error.status === 429) {
          setSandbox({ status: "limited", message: apiErrorMessage(error) });
        } else {
          setSandbox({ status: "error", message: apiErrorMessage(error) });
        }
      }
      return;
    }

    try {
      // Best-effort: mints the SSO cookie when a session exists so the new
      // tab lands signed in.
      await ensureSsoCookie();
    } catch {
      // Still open the tab — the front door will redirect if needed.
    }
    window.open(buildChatPromptUrl(trimmed), "_blank", "noopener,noreferrer");
  }

  function handlePromptKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  }

  return (
    <div className="min-h-dvh bg-background">
      <PublicNav />
      <main>
        <section className="bg-background">
          <div className="page-shell flex min-h-[calc(100dvh-4rem)] items-center py-12">
            <div className="max-w-2xl">
              <div className="mb-6 inline-flex items-center gap-2 rounded-sm border border-border px-3 py-1.5 text-[11px] font-medium uppercase tracking-label text-muted-foreground">
                <ShieldCheck className="h-3.5 w-3.5 text-foreground" strokeWidth={1.5} />
                Secure auth, billing, usage, and wallet verification
              </div>
              {/* Display headline: Sora Light at the kit's display size, short
                  period-terminated sentences, one headline per surface. */}
              <h1 className="font-display text-[44px] font-light leading-[52px] tracking-display sm:text-[56px] sm:leading-[64px]">
                Open infrastructure.
                <br />
                Inference with receipts.
              </h1>
              <p className="mt-6 max-w-xl text-lg leading-8 text-muted-foreground">
                Manage access, subscriptions, credit usage, wallet verification, and demo
                inference from one console.
              </p>

              <form onSubmit={handlePromptSubmit} className="mt-8 max-w-xl">
                <Label htmlFor="home-prompt">Ask something</Label>
                {/* Mirrors Open-WebUI's MessageInput composer (HYP-68): one
                    rounded-3xl surface, borderless transparent textarea on top,
                    and a round arrow-up send button bottom-right (filled when
                    there is text, muted-and-disabled when empty). Colours are
                    the same ramp stops the chat app's custom.css maps its gray
                    scale onto. */}
                <div className="mt-2 flex w-full flex-col rounded-3xl border border-[#1a1a1d] bg-[rgba(138,138,147,0.05)] px-1 transition hover:border-[#2a2a2f] focus-within:border-[#2a2a2f] [.light_&]:border-[#ede8e7] [.light_&]:bg-[rgba(247,246,244,0.9)] [.light_&]:hover:border-[#d9d6d4] [.light_&]:focus-within:border-[#d9d6d4]">
                  <textarea
                    id="home-prompt"
                    value={prompt}
                    onChange={(event) => setPrompt(event.target.value)}
                    onKeyDown={handlePromptKeyDown}
                    placeholder={
                      isAuthenticated
                        ? "Ask anything. Opens in a new chat tab. Press Enter there to send."
                        : "Try the free sandbox. A few requests a day, no account needed."
                    }
                    rows={2}
                    className="scrollbar-hidden max-h-80 w-full resize-none bg-transparent px-1 pb-[5px] pt-2.5 text-base text-foreground outline-none placeholder:text-[#686872]"
                  />
                  <div className="mx-0.5 mb-2.5 mt-0.5 flex items-center justify-end px-2.5">
                    <button
                      type="submit"
                      aria-label="Send message"
                      disabled={!prompt.trim() || sandbox.status === "loading"}
                      className={
                        prompt.trim() && sandbox.status !== "loading"
                          ? "self-center rounded-full bg-[#f7f6f4] p-1.5 text-[#0a0a0b] transition hover:bg-[#ede8e7] [.light_&]:bg-[#0a0a0b] [.light_&]:text-[#f7f6f4] [.light_&]:hover:bg-[#111113]"
                          : "disabled self-center rounded-full bg-[#35353b] p-1.5 text-[#0a0a0b] transition [.light_&]:bg-[#d9d6d4] [.light_&]:text-[#f7f6f4]"
                      }
                    >
                      {sandbox.status === "loading" ? (
                        <Loader2 className="size-5 animate-spin" strokeWidth={1.5} />
                      ) : (
                        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" className="size-5">
                          <path
                            fillRule="evenodd"
                            clipRule="evenodd"
                            d="M8 14a.75.75 0 0 1-.75-.75V4.56L4.03 7.78a.75.75 0 0 1-1.06-1.06l4.5-4.5a.75.75 0 0 1 1.06 0l4.5 4.5a.75.75 0 0 1-1.06 1.06L8.75 4.56v8.69A.75.75 0 0 1 8 14Z"
                          />
                        </svg>
                      )}
                    </button>
                  </div>
                </div>
                <p className="metadata mt-2 text-xs">
                  {isAuthenticated
                    ? "Opens the chat app in a new tab with this prompt ready to send. Nothing is sent until you press Enter there."
                    : "Free sandbox: a small number of short, capped-model replies per day, right on this page. Sign up for full access."}
                </p>

                {!isAuthenticated && sandbox.status !== "idle" ? (
                  <Card className="mt-4">
                    <CardContent className="p-4">
                      {sandbox.status === "loading" ? (
                        <p className="text-sm text-muted-foreground">Thinking…</p>
                      ) : sandbox.status === "success" ? (
                        <div className="space-y-2">
                          <p className="whitespace-pre-wrap text-sm leading-relaxed">
                            {sandbox.output}
                          </p>
                          <p className="metadata text-xs">
                            {sandbox.remaining > 0
                              ? `${sandbox.remaining} free sandbox request${sandbox.remaining === 1 ? "" : "s"} left today.`
                              : "That was your last free sandbox request for today."}{" "}
                            <Link href="/signup" className="underline underline-offset-2">
                              Sign up for full access
                            </Link>
                            .
                          </p>
                        </div>
                      ) : (
                        <div className="space-y-2">
                          <p className="text-sm leading-relaxed text-muted-foreground">
                            {sandbox.message}
                          </p>
                          {sandbox.status === "limited" ? (
                            <p className="metadata text-xs">
                              <Link href="/signup" className="underline underline-offset-2">
                                Sign up for an API key
                              </Link>{" "}
                              to keep going.
                            </p>
                          ) : null}
                        </div>
                      )}
                    </CardContent>
                  </Card>
                ) : null}
              </form>

              <div className="mt-6 flex flex-col gap-3 sm:flex-row">
                {isAuthenticated ? (
                  <Button size="lg" asChild>
                    <Link href="/dashboard">
                      Go to dashboard
                      <ArrowRight strokeWidth={1.5} />
                    </Link>
                  </Button>
                ) : (
                  <Button size="lg" asChild>
                    <Link href="/signup">
                      Create account
                      <ArrowRight strokeWidth={1.5} />
                    </Link>
                  </Button>
                )}
                <Button size="lg" variant="outline" asChild>
                  <Link href="/pricing">View pricing</Link>
                </Button>
              </div>
            </div>
          </div>
        </section>
      </main>
      <SiteFooter />
    </div>
  );
}
