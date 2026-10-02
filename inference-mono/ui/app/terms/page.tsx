import type { Metadata } from "next";

import { LegalPage, type LegalSection } from "@/components/legal-page";

export const metadata: Metadata = {
  title: "Terms of Service"
};

const SECTIONS: LegalSection[] = [
  {
    title: "Agreement",
    body: [
      "These Terms govern access to and use of Talaris's API console, dashboard, chat application, and inference services (the \"Service\"). By creating an account or using the Service you agree to these Terms and acknowledge our Privacy Policy. If you do not agree, do not use the Service."
    ]
  },
  {
    title: "Eligibility and accounts",
    body: [
      "You must be at least 18 years old, or the age of majority in your jurisdiction, and not barred from using the Service under applicable law. You are responsible for keeping your credentials, wallet keys, and API keys confidential and for all activity under your account."
    ]
  },
  {
    title: "Acceptable use",
    body: [
      "You agree not to use the Service to generate or distribute unlawful, infringing, or harmful content, to attempt to circumvent rate limits, quotas, or billing metering, to probe or disrupt the network, or to reverse engineer the underlying infrastructure. We may suspend access for violations."
    ]
  },
  {
    title: "AI output",
    body: [
      "Model output may be inaccurate, incomplete, or offensive and must not be relied on as professional advice. You are responsible for how you use outputs and for your inputs. Inference is performed by independent miners; attestation describes the software they run and is not a guarantee of output accuracy."
    ]
  },
  {
    title: "Billing and cancellation",
    body: [
      "Paid plans are billed in advance through Stripe on a monthly or annual interval; pay-as-you-go top-ups are charged when made. Credit allowances do not roll over unless the plan says so. You may cancel at any time from the billing dashboard and cancellation takes effect at the end of the current period. Consumers in the EU/UK have statutory withdrawal rights, which for digital services begin to be waived once you ask us to start the service and it is delivered. Taxes such as VAT are added where required."
    ]
  },
  {
    title: "Your content and our rights",
    body: [
      "You retain ownership of your inputs and, as between you and us, your outputs. You grant us a limited license to process them solely to operate the Service. We and our licensors retain all rights in the Service itself."
    ]
  },
  {
    title: "Availability and warranties",
    body: [
      "The Service is provided \"as is\" and \"as available\" without warranties of any kind to the extent permitted by law. We do not guarantee uninterrupted or error-free operation. Nothing here limits rights that cannot be excluded under mandatory consumer law."
    ]
  },
  {
    title: "Limitation of liability",
    body: [
      "To the maximum extent permitted by law, Talaris is not liable for indirect, incidental, special, consequential, or punitive damages, and its total liability is limited to the amounts you paid in the 12 months before the claim. Nothing excludes liability for fraud, death or personal injury caused by negligence, or anything else that cannot be limited by law."
    ]
  },
  {
    title: "Termination",
    body: [
      "You may stop using the Service and close your account at any time. We may suspend or terminate access for breach of these Terms or legal necessity. Provisions that by nature should survive will survive."
    ]
  },
  {
    title: "Governing law and disputes",
    body: [
      "Subject to mandatory consumer protections in your country of residence (including your right to bring claims in your local courts), these Terms are governed by the laws of the jurisdiction to be specified by Talaris upon legal review, and disputes will be resolved in its courts. Export control and sanctions laws apply to your use of the Service."
    ]
  },
  {
    title: "Changes",
    body: [
      "We may update these Terms. Material changes will be announced in the Service or by email, and continued use after the effective date constitutes acceptance, except where the law requires fresh consent."
    ]
  }
];

export default function TermsPage() {
  return <LegalPage title="Terms of Service" updated="October 2026" sections={SECTIONS} />;
}
