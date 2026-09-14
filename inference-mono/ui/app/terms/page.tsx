import type { Metadata } from "next";

import { PublicNav } from "@/components/public-nav";
import { SiteFooter } from "@/components/site-footer";

export const metadata: Metadata = {
  title: "Terms of Service"
};

/**
 * Placeholder Terms of Service (HYP-62). Real, on-topic content rather than
 * lorem ipsum -- accurate to what this product actually is (an API console
 * for metered inference) -- but explicitly a placeholder pending counsel
 * review; swap before this ever needs to be legally binding.
 */
export default function TermsPage() {
  return (
    <div className="min-h-dvh bg-background">
      <PublicNav />
      <main className="page-shell max-w-3xl py-12">
        <p className="eyebrow">Legal</p>
        <h1 className="mt-2 font-display text-[32px] font-medium leading-10 tracking-display">
          Terms of Service
        </h1>
        <p className="metadata mt-2 text-xs">Last updated September 2026</p>

        <div className="mt-8 space-y-8 text-sm leading-relaxed text-muted-foreground">
          <section>
            <h2 className="font-display text-lg font-medium text-foreground">1. Agreement</h2>
            <p className="mt-2">
              These Terms govern access to and use of Talaris&apos;s API console, dashboard, and
              inference services (the &quot;Service&quot;). By creating an account or using the
              Service you agree to be bound by these Terms. If you do not agree, do not use the
              Service.
            </p>
          </section>
          <section>
            <h2 className="font-display text-lg font-medium text-foreground">2. Accounts</h2>
            <p className="mt-2">
              You are responsible for maintaining the confidentiality of your credentials and API
              keys, and for all activity under your account. You must be at least 18 years old, or
              the age of majority in your jurisdiction, to create an account.
            </p>
          </section>
          <section>
            <h2 className="font-display text-lg font-medium text-foreground">
              3. Acceptable use
            </h2>
            <p className="mt-2">
              You agree not to use the Service to generate or distribute unlawful content, to
              attempt to circumvent rate limits or billing metering, or to reverse engineer the
              underlying inference infrastructure. Usage is metered and billed per the plan
              selected on the pricing page.
            </p>
          </section>
          <section>
            <h2 className="font-display text-lg font-medium text-foreground">
              4. Billing and cancellation
            </h2>
            <p className="mt-2">
              Paid plans are billed in advance through Stripe on a monthly or annual interval.
              Credit allowances do not roll over between billing periods unless stated otherwise
              on the plan. You may cancel or change plans at any time from the billing dashboard;
              cancellation takes effect at the end of the current billing period.
            </p>
          </section>
          <section>
            <h2 className="font-display text-lg font-medium text-foreground">
              5. Service availability
            </h2>
            <p className="mt-2">
              The Service is provided on an &quot;as is&quot; and &quot;as available&quot; basis,
              without warranties of any kind, express or implied. We do not guarantee
              uninterrupted or error-free operation.
            </p>
          </section>
          <section>
            <h2 className="font-display text-lg font-medium text-foreground">
              6. Limitation of liability
            </h2>
            <p className="mt-2">
              To the maximum extent permitted by law, Talaris will not be liable for any indirect,
              incidental, special, consequential, or punitive damages arising out of or related to
              your use of the Service.
            </p>
          </section>
          <section>
            <h2 className="font-display text-lg font-medium text-foreground">7. Changes</h2>
            <p className="mt-2">
              We may update these Terms from time to time. Material changes will be reflected by
              updating the date above. Continued use of the Service after a change constitutes
              acceptance of the revised Terms.
            </p>
          </section>
          <section>
            <h2 className="font-display text-lg font-medium text-foreground">8. Contact</h2>
            <p className="mt-2">
              Questions about these Terms can be sent to{" "}
              <a
                href="mailto:contact@talaris.xyz"
                className="focus-ring text-foreground underline-offset-4 hover:text-eggshell hover:underline"
              >
                contact@talaris.xyz
              </a>
              .
            </p>
          </section>
        </div>
      </main>
      <SiteFooter />
    </div>
  );
}
