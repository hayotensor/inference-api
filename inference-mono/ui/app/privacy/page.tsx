import type { Metadata } from "next";

import { LegalPage, type LegalSection } from "@/components/legal-page";

export const metadata: Metadata = {
  title: "Privacy Policy"
};

const SECTIONS: LegalSection[] = [
  {
    title: "Who we are and scope",
    body: [
      "Talaris (\"we\", \"us\") operates an API console, dashboard, chat application, and attested inference network (the \"Service\"). For personal data of account holders we act as the controller (GDPR) or \"business\" (CCPA/CPRA). This policy applies to the website, dashboard, and chat application."
    ]
  },
  {
    title: "Information we collect",
    body: [
      "Account data you provide: email address, name, password (stored only as a salted hash), and wallet addresses you sign in with or link. Sign-in through Google gives us your verified email and name from Google.",
      "Usage and billing data: API request counts, token and credit consumption, selected models, plan and payment status (card details are handled by Stripe and never reach our servers), and referral attribution.",
      "Technical data: IP address, browser and device type, timestamps, and security logs used for abuse prevention and rate limiting. For the free guest sandbox we store a random per-browser identifier and your IP address to enforce daily limits.",
      "Prompts and responses: inference requests are routed to independent, attested miners to produce a response. We meter and may log request metadata (size, model, timing). We do not use your prompts or outputs to train models. Conversation history in the chat application is stored for you so you can return to it, and you can delete it."
    ]
  },
  {
    title: "Why we use it (legal bases)",
    body: [
      "Where the GDPR or UK GDPR applies, we rely on: performance of a contract (providing the Service, authentication, billing); legitimate interests (security, fraud and abuse prevention, rate limiting, product reliability); legal obligation (tax, accounting, responding to lawful requests); and consent (for optional communications and anything else we ask you to opt in to, which you can withdraw at any time).",
      "We do not sell personal information and do not share it for cross-context behavioral advertising."
    ]
  },
  {
    title: "Cookies and local storage",
    body: [
      "We use only strictly necessary cookies and browser storage: your session and single sign-on credential for the dashboard and chat, your theme preference, and the guest sandbox identifier. We do not use advertising or third-party analytics cookies, so no cookie banner is shown. If we add non-essential cookies we will ask for your consent first."
    ]
  },
  {
    title: "Sharing and processors",
    body: [
      "We share data only with providers that help us run the Service: Stripe (payments), our hosting and email delivery providers, Google (if you use Google sign-in), and the miners that process your inference requests. Public blockchain networks used for wallet verification are public by nature. We may disclose data when required by law or to protect rights and safety, and to a successor in a merger or asset sale."
    ]
  },
  {
    title: "International transfers",
    body: [
      "Our infrastructure and providers may be located outside your country, including in the United States. Where required, we rely on safeguards such as the EU Standard Contractual Clauses and the UK International Data Transfer Addendum."
    ]
  },
  {
    title: "Retention",
    body: [
      "We keep account and billing records while your account is active and afterwards as needed for legal, tax, and audit purposes. Guest sandbox identifiers and security logs are kept for a short period (generally 30 to 90 days). You may delete your chats at any time and request deletion of your account."
    ]
  },
  {
    title: "Your rights",
    body: [
      "EEA, UK, and Swiss residents (GDPR): you may request access, rectification, erasure, restriction, portability, and object to processing based on legitimate interests, and withdraw consent at any time. You may lodge a complaint with your local data protection authority.",
      "California and other US state residents (CCPA/CPRA and similar laws): you may request to know, access, correct, delete, and port your personal information, and opt out of sale or sharing (we do none) without discrimination. You may use an authorized agent.",
      "Residents of Brazil (LGPD), Canada (PIPEDA), Australia, and other regions have similar rights under local law, which we honor to the extent they apply. To exercise any right, email the contact below. We will verify your identity and respond within the period required by law (generally 30 to 45 days)."
    ]
  },
  {
    title: "Children",
    body: [
      "The Service is not directed to children under 16 and we do not knowingly collect their data. If you believe a child has provided us data, contact us and we will delete it."
    ]
  },
  {
    title: "Security",
    body: [
      "We use encryption in transit, access controls, and attested inference infrastructure to protect your data. No method of transmission or storage is completely secure. We will notify you and regulators of a data breach as required by law."
    ]
  },
  {
    title: "Changes",
    body: [
      "We may update this policy. Material changes will be announced in the Service or by email, and where consent is required we will ask for it again."
    ]
  }
];

export default function PrivacyPage() {
  return <LegalPage title="Privacy Policy" updated="October 2026" sections={SECTIONS} />;
}
