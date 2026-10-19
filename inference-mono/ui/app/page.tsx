"use client";

import { useLayoutEffect, useRef, useState } from "react";
import { ShieldCheck } from "lucide-react";

import { PublicNav } from "@/components/public-nav";
import { SiteFooter } from "@/components/site-footer";
import { useAuth } from "@/lib/auth";
import { buildChatPromptUrl } from "@/lib/chat-prompt";
import { UI_DEMO_MODE } from "@/lib/demo-mode";

/**
 * Open-WebUI's composer is one text row (28px line, 10px/5px padding) plus a
 * 44px button row: ~97px tall in total. The textarea grows with its content
 * from that floor up to the same 320px cap (`max-h-80`) the chat uses.
 */
const TEXTAREA_MIN_HEIGHT = 51;

export default function HomePage() {
  const { isAuthenticated, ensureSsoCookie } = useAuth();
  const [prompt, setPrompt] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const hasPrompt = !UI_DEMO_MODE && prompt.trim().length > 0;

  useLayoutEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.max(TEXTAREA_MIN_HEIGHT, el.scrollHeight)}px`;
  }, [prompt]);

  async function handlePromptSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (UI_DEMO_MODE) return;
    const trimmed = prompt.trim();
    if (!trimmed) return;

    // HYP-48: always hand off to the chat app in a NEW tab, with the prompt
    // sitting unsent in its composer (`?q=...&submit=false`). Signed-out
    // visitors land in a free guest chat there (HYP-67): the chat front door
    // sees the prompt and starts a metered guest session instead of bouncing
    // to /login. The tab is opened synchronously, inside the click/Enter
    // gesture -- opening it after an `await` is what popup blockers refuse.
    const url = buildChatPromptUrl(trimmed);
    const tab = window.open("", "_blank");

    if (isAuthenticated) {
      try {
        // Mint the SSO cookie so the new tab lands signed in.
        await ensureSsoCookie();
      } catch {
        // Still open the chat; its front door handles a missing session.
      }
    }

    if (tab) {
      tab.opener = null;
      tab.location.href = url;
    } else {
      // Popups blocked outright: use this tab rather than doing nothing.
      window.location.assign(url);
    }
  }

  function handlePromptKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  }

  return (
    <div className="min-h-dvh bg-background">
      <PublicNav />
      <main>
        <section className="bg-background">
          <div className="page-shell flex min-h-[calc(100dvh-4rem)] flex-col items-center justify-center py-12 text-center">
            <div className="mb-6 inline-flex items-center gap-2 rounded-sm border border-border px-3 py-1.5 text-[11px] font-medium uppercase tracking-label text-muted-foreground">
              <ShieldCheck className="h-3.5 w-3.5 text-foreground" strokeWidth={1.5} />
              Private AI
            </div>
            {/* Display headline: Sora Light at the kit's display size, short
                period-terminated sentences, one headline per surface. */}
            <h1 className="font-display text-[44px] font-light leading-[52px] tracking-display sm:text-[56px] sm:leading-[64px]">
              Open infrastructure.
              <br />
              Inference with receipts.
            </h1>
            <p className="mx-auto mt-6 max-w-2xl text-lg leading-8 text-muted-foreground">
              Manage access, subscriptions, credit usage, wallet verification, and demo
              inference from one console.
            </p>

            {/* HYP-68: a 1:1 copy of Open-WebUI's MessageInput composer, at its
                width -- the chat centres the composer in a `max-w-3xl` column
                with `px-2.5`, i.e. a 748px box -- so the hand-off looks like
                the same input. One 8px-radius surface (the chat's custom.css
                maps rounded-3xl to 8px), a borderless one-row textarea and the
                chat's round 32px send button bottom-right: filled when there
                is text, muted and disabled when empty. Colours are the ramp
                stops the chat maps its gray scale onto. */}
            <form onSubmit={handlePromptSubmit} className="mx-auto mt-10 w-full max-w-3xl px-2.5 text-left">
              <label htmlFor="home-prompt" className="sr-only">
                Ask anything
              </label>
              <div className="relative flex w-full flex-1 flex-col rounded-[8px] border border-[#1a1a1d] bg-[rgba(138,138,147,0.05)] px-1 shadow-lg transition hover:border-[#2a2a2f] focus-within:border-[#2a2a2f] [.light_&]:border-[#f7f6f4] [.light_&]:bg-white/90 [.light_&]:hover:border-[#ede8e7] [.light_&]:focus-within:border-[#ede8e7]">
                <div className="px-2.5">
                  <textarea
                    ref={textareaRef}
                    id="home-prompt"
                    value={prompt}
                    onChange={(event) => setPrompt(event.target.value)}
                    onKeyDown={handlePromptKeyDown}
                    placeholder={
                      UI_DEMO_MODE
                        ? "Chat and inference are unavailable in the local demo."
                        : "How can I help you today?"
                    }
                    rows={1}
                    disabled={UI_DEMO_MODE}
                    style={{ minHeight: TEXTAREA_MIN_HEIGHT }}
                    className="scrollbar-hidden block max-h-80 w-full resize-none overflow-auto bg-transparent px-1 pb-[5px] pt-2.5 text-base leading-7 text-foreground outline-none placeholder:text-[#686872]"
                  />
                </div>
                <div className="mx-0.5 mb-2.5 mt-0.5 flex max-w-full items-center justify-end">
                  <div className="mr-1 flex shrink-0 self-end">
                    <button
                      type="submit"
                      aria-label="Send message"
                      title="Send message"
                      disabled={!hasPrompt}
                      className={
                        hasPrompt
                          ? "self-center rounded-full bg-[#f7f6f4] p-1.5 text-[#0a0a0b] transition hover:bg-[#ede8e7] [.light_&]:bg-black [.light_&]:text-white [.light_&]:hover:bg-[#111113]"
                          : "disabled self-center rounded-full bg-[#35353b] p-1.5 text-[#111113] transition [.light_&]:bg-[#d9d6d4] [.light_&]:text-white"
                      }
                    >
                      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor" className="size-5">
                        <path
                          fillRule="evenodd"
                          clipRule="evenodd"
                          d="M8 14a.75.75 0 0 1-.75-.75V4.56L4.03 7.78a.75.75 0 0 1-1.06-1.06l4.5-4.5a.75.75 0 0 1 1.06 0l4.5 4.5a.75.75 0 0 1-1.06 1.06L8.75 4.56v8.69A.75.75 0 0 1 8 14Z"
                        />
                      </svg>
                    </button>
                  </div>
                </div>
              </div>
              <p className="mt-2 text-center text-xs text-[#686872]">
                {UI_DEMO_MODE
                  ? "The chat hand-off is disabled in the local demo."
                  : isAuthenticated
                    ? "Opens in a new chat tab with your prompt ready to send."
                    : "Opens a free guest chat in a new tab. No account needed for a few messages a day."}
              </p>
            </form>
          </div>
        </section>
      </main>
      <SiteFooter />
    </div>
  );
}
