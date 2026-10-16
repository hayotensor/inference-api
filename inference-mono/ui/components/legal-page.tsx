import { PublicNav } from "@/components/public-nav";
import { SiteFooter } from "@/components/site-footer";

export type LegalSection = { title: string; body: string[] };

/**
 * Shared layout for the Terms and Privacy pages (HYP-69). Both carry a
 * visible notice that the text is AI-generated and has not been reviewed by
 * counsel, so nobody mistakes it for vetted legal advice.
 */
export function LegalPage({
  title,
  updated,
  sections
}: {
  title: string;
  updated: string;
  sections: LegalSection[];
}) {
  return (
    <div className="flex min-h-dvh flex-col bg-background">
      <PublicNav />
      <main className="page-shell w-full flex-1 max-w-3xl py-12">
        <p className="eyebrow">Legal</p>
        <h1 className="mt-2 font-display text-[32px] font-medium leading-10 tracking-display">
          {title}
        </h1>
        <LegalBody updated={updated} sections={sections} />
      </main>
      <SiteFooter />
    </div>
  );
}

/**
 * The legal text itself (updated date, draft notice, numbered sections,
 * contact). Split out so /privacy can place it beneath its technical
 * breakdown without duplicating the legally reviewed copy.
 */
export function LegalBody({ updated, sections }: { updated: string; sections: LegalSection[] }) {
  return (
    <>
      <p className="metadata mt-2 text-xs">Last updated {updated}</p>

      <div
        role="note"
        className="mt-6 rounded-sm border border-border-strong p-4 text-xs leading-relaxed text-muted-foreground"
      >
        <span className="font-medium text-foreground">Draft notice.</span> This document was
        generated with AI assistance and has not yet been reviewed by a qualified lawyer. It
        is provided in good faith to describe how the Service works, and will be revised
        after legal review. It is not legal advice.
      </div>

      <div className="mt-8 space-y-8 text-sm leading-relaxed text-muted-foreground">
        {sections.map((section, index) => (
          <section key={section.title}>
            <h2 className="font-display text-lg font-medium text-foreground">
              {index + 1}. {section.title}
            </h2>
            {section.body.map((paragraph) => (
              <p key={paragraph} className="mt-2">
                {paragraph}
              </p>
            ))}
          </section>
        ))}
      </div>
      <p className="mt-10 text-sm text-muted-foreground">
        Contact:{" "}
        <a
          href="mailto:contact@talaris.xyz"
          className="focus-ring text-foreground underline-offset-4 hover:text-eggshell hover:underline"
        >
          contact@talaris.xyz
        </a>
      </p>
    </>
  );
}
