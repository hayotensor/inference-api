"use client";

import { useState } from "react";
import Link from "next/link";
import { ArrowRight, ShieldCheck } from "lucide-react";

import { PublicNav } from "@/components/public-nav";
import { SiteFooter } from "@/components/site-footer";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useAuth } from "@/lib/auth";
import { buildChatPromptUrl } from "@/lib/chat-prompt";

export default function HomePage() {
  const { isAuthenticated, ensureSsoCookie } = useAuth();
  const [prompt, setPrompt] = useState("");

  async function handlePromptSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = prompt.trim();
    if (!trimmed) return;
    try {
      // Best-effort: mints the SSO cookie when a session exists so the new
      // tab lands signed in; a no-op when signed out, in which case the
      // chat app's own front door sends the visitor to /login and back.
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
                <div className="mt-2 flex flex-col gap-2 rounded-3xl border border-border/70 bg-card/80 p-2 shadow-lg backdrop-blur-sm transition-colors focus-within:border-foreground/40 dark:bg-card/40 sm:flex-row sm:items-end">
                  <Textarea
                    id="home-prompt"
                    value={prompt}
                    onChange={(event) => setPrompt(event.target.value)}
                    onKeyDown={handlePromptKeyDown}
                    placeholder="Ask anything. Opens in a new chat tab — press Enter there to send."
                    rows={2}
                    className="min-h-[52px] flex-1 resize-none rounded-2xl border-transparent bg-transparent px-3 py-2.5 shadow-none hover:border-transparent"
                  />
                  <Button
                    type="submit"
                    size="lg"
                    disabled={!prompt.trim()}
                    className="rounded-2xl sm:shrink-0"
                  >
                    Ask
                    <ArrowRight strokeWidth={1.5} />
                  </Button>
                </div>
                <p className="metadata mt-2 text-xs">
                  Opens the chat app in a new tab with this prompt ready to send — nothing is
                  sent until you press Enter there.
                </p>
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
