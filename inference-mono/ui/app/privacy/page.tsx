import type { Metadata } from "next";
import type { ReactNode } from "react";

import { LegalBody, type LegalSection } from "@/components/legal-page";
import {
  AttestationDiagram,
  CadenceDiagram,
  ChatStorageDiagram,
  EncryptionBoundaryDiagram,
  EnclaveDiagram,
  ReceiptDiagram,
  RequestPathDiagram,
  SealPinDiagram,
  StorageDiagram
} from "@/components/privacy-diagrams";
import { PublicNav } from "@/components/public-nav";
import { SiteFooter } from "@/components/site-footer";

export const metadata: Metadata = {
  title: "Privacy",
  description:
    "How Talaris handles your prompts: trusted execution environments, remote attestation, what is and is not encrypted end to end, what we store, and how often we verify."
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
      "Prompts and responses: inference requests are routed to independent, attested miners to produce a response. We store request metadata (token counts, model, timing) but not the text of prompts or responses. We do not use your prompts or outputs to train models. Chat application history is saved on your device by default; if you choose account storage it is stored on our servers until you delete it. Files you upload to the chat application are stored on our servers."
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


function Section({
  id,
  n,
  title,
  children
}: {
  id: string;
  n: number;
  title: string;
  children: ReactNode;
}) {
  return (
    <section id={id} className="scroll-mt-24">
      <p className="eyebrow">{String(n).padStart(2, "0")}</p>
      <h2 className="mt-1 font-display text-xl font-medium text-foreground">{title}</h2>
      <div className="mt-2 space-y-3">{children}</div>
    </section>
  );
}

function Code({ children }: { children: ReactNode }) {
  return <code className="font-mono text-xs text-foreground">{children}</code>;
}

function Table({ head, rows }: { head: string[]; rows: ReactNode[][] }) {
  return (
    <div className="overflow-x-auto rounded-sm border border-border">
      <table className="w-full border-collapse text-left text-xs">
        <thead>
          <tr className="border-b border-border-strong">
            {head.map((h) => (
              <th key={h} className="eyebrow px-3 py-2 font-medium">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className="border-b border-border last:border-0 align-top">
              {row.map((cell, j) => (
                <td key={j} className={j === 0 ? "px-3 py-2 text-foreground" : "px-3 py-2"}>
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const TOC: Array<[string, string]> = [
  ["summary", "The short version"],
  ["request-path", "Request path"],
  ["tee", "Trusted execution"],
  ["attestation", "Remote attestation"],
  ["sealing", "Sealed credentials and TLS pinning"],
  ["receipts", "Signed receipts"],
  ["encryption", "What is (and is not) end-to-end"],
  ["storage", "What we store"],
  ["cadence", "How often we attest"],
  ["chat", "The chat app"],
  ["limits", "Limits and caveats"],
  ["policy", "Privacy Policy (legal text)"]
];

export default function PrivacyPage() {
  return (
    <div className="flex min-h-dvh flex-col bg-background">
      <PublicNav />
      <main className="page-shell w-full flex-1 max-w-3xl py-12">
        <p className="eyebrow">Privacy</p>
        <h1 className="mt-2 font-display text-[32px] font-medium leading-10 tracking-display">
          How Talaris handles your data
        </h1>
        <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
          This page is for everyone, signed in or not. It describes, semi-technically, what
          happens to a prompt on its way through the system, what is recorded, and just as
          importantly what we do <em>not</em> claim. It is written against the source code of
          the serving plane, the attestation verifier, the miner enclave wrapper and the
          dashboard; function and field names are given so you can check them. The legal
          Privacy Policy follows in <a href="#policy" className="focus-ring text-foreground underline underline-offset-4">section 12</a>.
        </p>

        <nav aria-label="On this page" className="mt-6 rounded-sm border border-border p-4">
          <p className="eyebrow">On this page</p>
          <ol className="mt-2 grid gap-x-6 gap-y-1 text-xs text-muted-foreground sm:grid-cols-2">
            {TOC.map(([id, label], i) => (
              <li key={id}>
                <a href={`#${id}`} className="focus-ring hover:text-foreground">
                  <span className="metadata text-xs">{String(i + 1).padStart(2, "0")}</span> {label}
                </a>
              </li>
            ))}
          </ol>
        </nav>

        <div className="mt-10 space-y-12 text-sm leading-relaxed text-muted-foreground">
          <Section id="summary" n={1} title="The short version">
            <Table
              head={["Question", "Answer", "Detail"]}
              rows={[
                [
                  "Is inference run in hardware-isolated enclaves?",
                  "Yes, on the miner path",
                  "Requests are routed only to miners whose enclave passed attestation (section 4). Production policy requires Intel TDX and NVIDIA GPU evidence."
                ],
                [
                  "Is my data encrypted on the way to the enclave?",
                  "End to end if you opt in; otherwise in transit",
                  "API requests sent through the talaris-e2ee client are encrypted on your device to a key held only inside an enclave whose attestation your client verified itself; the Talaris API routes and bills ciphertext it cannot read. Plain API requests and the chat app are encrypted hop by hop: TLS to our edge, an encrypted tunnel to our servers, then certificate-pinned TLS from the API to the enclave, with the edge and API handling plaintext in memory to route your request (section 7)."
                ],
                [
                  "Do you store prompts and responses?",
                  "No",
                  "The API, the chat app's key broker and the miner enclave hold prompt and response text only in memory while your request is being served. None of them has a database column, log line or receipt field for it; we keep only metadata such as token counts, model and timing, which billing needs (section 8). The chat app is separate: its history is saved on your device by default, and on our servers only if you switch a chat to account storage, where it stays until you delete it (section 10)."
                ],
                [
                  "Is every request served by an attested node?",
                  "Yes",
                  "Requests only go to nodes with a current, passing hardware attestation (re-done about every hour by default). On every request we also verify the node's pinned TLS certificate against the attested fingerprint, and each response carries a signed receipt naming that attested node. A fresh hardware quote is not fetched per request (sections 6 and 9)."
                ],
                [
                  "Can the miner operator read my prompt?",
                  "No, not from outside the enclave",
                  "Inference runs inside an Intel TDX confidential VM with an NVIDIA confidential-computing GPU, whose memory the host machine and its operator cannot read. Before a miner gets any traffic, its enclave must pass remote attestation: genuine TDX and GPU hardware, an approved model, and a TLS key generated inside the enclave, so your request is decrypted only in there. One gap remains: the platform does not yet pin the enclave's software measurement to a published build, so today it cannot prove the miner runs our unmodified enclave software (sections 3, 4 and 11)."
                ]
              ]}
            />
          </Section>

          <Section id="request-path" n={2} title="Request path">
            <RequestPathDiagram />
            <p>
              An API call (<Code>POST /v1/chat/completions</Code> or <Code>/v1/inference</Code>) reaches
              our edge network over TLS, which hands it to the Talaris API through an encrypted
              tunnel. The API authenticates your key (stored only as a hash), checks
              your model allowlist and rate limit, reserves token credit, and asks the miner selector
              for candidates: miners that are attested, healthy, have the model loaded and hold an
              active credential, ranked by free capacity, then queue depth, then a reputation the
              platform computes from its own observed success rate (never a self-reported one).
            </p>
            <p>
              The forwarder then opens a new TLS connection to the chosen enclave, pinned to the
              certificate that enclave attested (section 5), and sends the request. If a miner fails,
              the API tries the next candidate, up to three attempts by default. The reservation is
              settled using the token counts the enclave reports, never an estimate.
            </p>
            <p>
              Miners publish one address for many model enclaves through an SNI pass-through proxy
              (HAProxy in TCP mode). It never terminates TLS and holds no key, so it can see which
              hostname was contacted and when, but not request or response contents.
            </p>
            <p>
              The signed-out homepage sandbox and the chat app use the same selection and forwarding
              code. The chat app adds an edge hop that maps your login to a managed API key before
              forwarding.
            </p>
          </Section>

          <Section id="tee" n={3} title="Trusted execution">
            <EnclaveDiagram />
            <p>
              Each model runs inside a single-model enclave: an Intel TDX confidential VM paired with
              an NVIDIA confidential-computing GPU that the engine is pinned to, with no CPU fallback.
              These technologies are designed so that the host operating system, hypervisor and the
              machine&apos;s operator cannot read the memory of the VM or the GPU. The prompt, the
              weights and the generated text exist in the clear only inside that boundary.
            </p>
            <p>
              The enclave wrapper (<Code>tee_wrapper</Code>) generates fresh identity keys and a fresh
              TLS certificate in memory at every boot and never reads one from disk. The inference
              engine (vLLM or SGLang) runs behind it and is started with a measured launch
              configuration, whose hash is part of what gets attested.
            </p>
            <p>
              What the code does is <em>verify the hardware evidence</em>; the confidentiality
              property itself is provided by the CPU and GPU vendors&apos; hardware, and our
              guarantee is only as strong as theirs (section 11).
            </p>
          </Section>

          <Section id="attestation" n={4} title="Remote attestation">
            <AttestationDiagram />
            <p>
              Attestation is how the platform decides an enclave is genuine before trusting it with
              anything. In <Code>ProvisionerService</Code> the platform generates a random 32-byte
              nonce and calls the enclave&apos;s <Code>GET /attestation?nonce=</Code>. The enclave
              answers with a document whose <Code>report_data</Code> is a hash (format v6) over: the
              nonce, its enclave key fingerprint, its TLS key fingerprint, the hash of the loaded model
              weights, the hash of the engine launch arguments, the hash of the GPU set, and the head
              of its usage hash chain. That digest is carried inside a hardware-signed Intel TDX quote
              and echoed in NVIDIA GPU evidence.
            </p>
            <p>
              The shared verifier (<Code>talaris_attest.verify_attestation</Code>) then checks, each as
              a separate pass/fail: the attestation mode, that the nonce echoes ours (freshness),
              that <Code>report_data</Code> recomputes correctly, the NVIDIA evidence, the Intel quote
              chain, that the attested GPUs are the ones pinned, that the model hash is on the
              platform-signed allowlist, and that the TLS certificate is vouched for by the enclave
              key. Every required check must pass; otherwise the miner is marked failed and receives
              no traffic. Production defaults refuse the development mode in which an enclave merely
              signs its own claim.
            </p>
            <p>
              What this does not yet cover: the TDX quote also carries the measurements of the
              software booted inside the VM (<Code>MRTD</Code> and <Code>RTMR0</Code> to{" "}
              <Code>RTMR3</Code>). The verifier parses them but does not yet compare them to the
              published enclave build, so attestation proves genuine hardware, the model and an
              in-enclave TLS key, but not that the enclave wrapper itself is unmodified. Pinning
              these measurements is the next step.
            </p>
            <p>
              Validators on the subnet run their own, independent challenges: each epoch every
              validator probes every miner (or, if the subnet is configured to, the same
              deterministic sample) with a fresh nonce, verifies the
              result with the same verifier, and uses it as a pass/fail gate on rewards.
            </p>
          </Section>

          <Section id="sealing" n={5} title="Sealed credentials and TLS pinning">
            <SealPinDiagram />
            <p>
              Once an enclave verifies, the platform mints a fresh random credential and seals it to
              the enclave&apos;s public key (a libsodium sealed box), signs the ciphertext with the
              platform key, and posts it to <Code>/provision</Code>. Only the enclave&apos;s private
              key, which never leaves it, can open it. That credential is the bearer token the
              platform later uses to call the enclave; it is stored on the platform encrypted at rest
              and replaced on each re-provisioning.
            </p>
            <p>
              Enclaves use self-signed certificates, so trust does not come from a certificate
              authority. It comes from the attestation: the fingerprint of the certificate is bound
              into the hardware-signed report. On every forward the platform compares the SHA-256 of
              the certificate actually presented against that attested fingerprint, and aborts the
              connection on a mismatch (and marks the miner stale). Pinning is on by default.
            </p>
          </Section>

          <Section id="receipts" n={6} title="Signed receipts">
            <ReceiptDiagram />
            <p>
              A response served by an attested miner carries a receipt, in the <Code>attestation</Code>{" "}
              field of <Code>/v1/inference</Code> and <Code>talaris_attestation</Code> on chat
              completions. It names the request id, model, model hash, the enclave&apos;s verify key,
              the TLS fingerprint, the attestation mode and the time that enclave was last
              hardware-verified, signed with the same Ed25519 key that signs the model allowlist. You
              fetch that public key from <Code>GET /attest</Code>, pin it, and verify with PyNaCl
              alone.
            </p>
            <p>
              Be precise about what this is: a platform-signed statement tying that response to a
              previously verified enclave. It is <em>not</em> a fresh hardware quote for each request.
              The receipt contains no prompt or response text.
            </p>
          </Section>

          <Section id="encryption" n={7} title="What is (and is not) end-to-end">
            <EncryptionBoundaryDiagram />
            <p>
              <strong>End-to-end encrypted API requests (opt-in).</strong> With the talaris-e2ee
              client library, your device encrypts the whole request (messages, parameters, tools)
              to a public key generated inside one enclave, using hybrid post-quantum HPKE
              (ML-KEM-768 + X25519). Before encrypting, the client fetches that enclave&apos;s
              attestation with its own fresh nonce and verifies it itself: genuine TDX and GPU
              evidence, and that the key is bound into the attested identity. It does not take our
              word for the key. The enclave encrypts the response back to a key only your device
              holds; streamed responses are sequenced and closed with an authenticated final event,
              so a relay cannot drop, reorder or forge output. On this path the Talaris API sees
              only what it needs to route and bill: your API key, the model, whether you are
              streaming, your max_tokens, message sizes, timing and token counts. Never the content.
            </p>
            <p>
              <strong>Everything else is hop by hop.</strong> Plain API requests and the chat app
              work as follows. Your connection to Talaris is protected by TLS in transit to our edge network
              (Cloudflare), which decrypts it and passes it to our servers over an encrypted tunnel;
              the platform&apos;s connection to the enclave is a second, separate,
              certificate-pinned TLS session. The edge and the Talaris API both see your request in
              plaintext: the API decrypts it to authenticate, meter and route it, and holds it in
              memory while forwarding it. So encryption is hop-by-hop: it is{" "}
              <strong>not</strong> a single end-to-end session between your device and the enclave.
              What the design guarantees is that every hop over the internet is encrypted, that
              past the API plaintext exists only inside attested hardware, and that the credential the platform
              uses to reach the enclave is sealed so that only the enclave can open it.
            </p>
            <p>
              If your threat model excludes the platform operator, you need to trust us to run the
              published code. The signed, verifiable parts (receipts, allowlist, epoch statistics)
              let you check what we claim about the miners; they do not remove the API from the trust
              path.
            </p>
          </Section>

          <Section id="storage" n={8} title="What we store, and what we do not">
            <StorageDiagram />
            <Table
              head={["Where", "What is recorded"]}
              rows={[
                [
                  "Account service",
                  "Email, name, salted password hash, linked wallet addresses; hashed API keys. Account events such as sign-in and sign-out are audit-logged with IP address, user agent and request id."
                ],
                [
                  "Usage ledger",
                  "Per request: request id, user and key id, model, prompt and completion token counts, credit charged, serving miner and model hash, status and timestamps. Per API key: endpoint, method, status, token counts."
                ],
                [
                  "Miner statistics",
                  "Per forward attempt: miner, model, outcome, latency, time to first token, token counts, request id. No user identifier. Aggregated per epoch into a signed snapshot that validators score from."
                ],
                [
                  "Enclave",
                  "A local usage database: key id, model, endpoint, token counts, latency, status, request id, in a tamper-evident hash chain. Its code states it never persists request or response bodies."
                ],
                [
                  "Free sandbox",
                  "A per-browser id and your IP address are hashed with a secret key and used as daily counters in Redis. A log line records the request id, model, token counts and those hashes, not the raw values and not the prompt."
                ]
              ]}
            />
            <p>
              Prompt and response text is held in memory to serve the request and is not written to
              any of these tables. The serving plane&apos;s access logs are set to warnings only, and the
              enclave&apos;s access log records method, path and status, never bodies. Third-party
              infrastructure in front of the service (such as a CDN or reverse proxy) is outside what
              this code controls; see the legal policy for the processors we use.
            </p>
          </Section>

          <Section id="cadence" n={9} title="How often we attest">
            <CadenceDiagram />
            <div className="grid gap-3 sm:grid-cols-3">
              {[
                ["Every request", "TLS certificate pin check and a signed receipt. No new hardware quote."],
                ["About every hour", "Full hardware attestation of each node (default 1 hour, operator-configurable)."],
                ["Every subnet epoch", "Independent validator challenge of the miners, with a fresh nonce."]
              ].map(([when, what]) => (
                <div key={when} className="rounded-sm border border-border p-4">
                  <p className="eyebrow text-foreground">{when}</p>
                  <p className="mt-2 text-xs leading-relaxed">{what}</p>
                </div>
              ))}
            </div>
            <Table
              head={["Check", "Who runs it", "When"]}
              rows={[
                [
                  "Full hardware attestation of an enclave",
                  "Talaris API (provisioner)",
                  "At first registration, then whenever the attestation expires. Default lifetime is 1 hour (ATTESTATION_TTL_SECONDS), found by a scan every 60 seconds and a maintenance pass every 2 minutes. Also redone after a TLS pin mismatch or when a model is added."
                ],
                [
                  "Fresh credential for the enclave",
                  "Talaris API",
                  "Each time an enclave is re-provisioned, so about hourly by default; tokens also expire after 24 hours."
                ],
                [
                  "Independent challenge of miners",
                  "Subnet validators",
                  "Once per subnet epoch, with a fresh nonce; every miner by default, or a deterministic sample if configured."
                ],
                [
                  "TLS certificate pin",
                  "Talaris API",
                  "On every connection to an enclave, that is, every request."
                ],
                [
                  "Signed receipt",
                  "Talaris API",
                  "On every response served by a miner. This is a signature, not a new hardware quote."
                ],
                [
                  "Signed model-list check in the dashboard",
                  "Your browser, via this site's server",
                  "When the dashboard loads and every 6 hours while it stays open; shown as stale after 24 hours. It verifies the platform-signed allowlist of approved model hashes against a key pinned in the site's configuration. It does not itself fetch hardware evidence from each miner."
                ]
              ]}
            />
            <p>
              These intervals are the defaults in the code and are configurable by the operator. A
              request is therefore not attested individually; it is served by an enclave whose
              hardware attestation is at most about one lifetime old, with a pinned certificate and a
              signed receipt on top.
            </p>
          </Section>

          <Section id="chat" n={10} title="The chat app, search and the sandbox">
            <ChatStorageDiagram />
            <p>
              The chat app is a rebranded Open-WebUI. By default new conversations are kept in your
              browser&apos;s own storage and not on our servers. You can switch an account or a single
              chat to account storage for multi-device access; those chats are kept on our servers
              under your account until you delete them (deletion removes them from the database),
              and Talaris administrators can access them unless you encrypt them with a passphrase
              in the browser first. If you lose the passphrase those chats cannot be recovered.
              Files you upload into a chat are stored on our servers so the model can read them,
              whichever storage you choose. The text of each message still passes through the Talaris API to reach a
              model, exactly as in section 2.
            </p>
            <p>
              Where web search is enabled, the search query is sent to a search provider
              (DuckDuckGo, with Bing or Wikipedia as fallbacks) by a small proxy we run. Treat search
              terms as leaving the attested boundary; the proxy also writes the query to its
              server log. The signed-out sandbox is capped per day per
              browser and per network, and its requests are not billed to any account.
            </p>
          </Section>

          <Section id="limits" n={11} title="Limits and caveats">
            <ul className="list-disc space-y-2 pl-5">
              <li>
                Hardware attestation shows the enclave is genuine and running the expected model and
                launch arguments. It is not a proof that the hardware has no flaw; TEEs have had
                published side-channel and firmware vulnerabilities, and we depend on Intel and
                NVIDIA for fixes.
              </li>
              <li>
                The enclave&apos;s software measurements (TDX <Code>MRTD</Code>/<Code>RTMR</Code>)
                are not yet checked against a published build (section 4). Until they are, the
                guarantee that the operator cannot read your prompt rests on genuine attested
                hardware plus the miner running our published enclave software, not on a
                cryptographic proof of that software.
              </li>
              <li>
                A model hash with no existing pin is added to the allowlist the first time it is
                hardware-verified (trust on first attestation). An operator-set pin is never
                overridden.
              </li>
              <li>
                Development builds can run with a mock attestation mode. Production defaults require
                TDX and GPU evidence and reject it; if you are evaluating a non-production
                deployment, the dashboard badge and the receipt&apos;s <Code>attestation_mode</Code>{" "}
                field show what was actually verified.
              </li>
              <li>
                A few parts of the verifier are documented in its source as unfinished production
                wiring (for example pinning NVIDIA&apos;s endpoint details and Intel revocation-list
                checks). They are being completed.
              </li>
              <li>
                Metadata (who called, when, which model, how many tokens) is stored, as described in
                section 8, and is what billing and miner scoring are built from.
              </li>
            </ul>
          </Section>
        </div>

        <hr className="rule my-12" />

        <section id="policy" className="scroll-mt-24">
          <p className="eyebrow">12 / Legal</p>
          <h2 className="mt-1 font-display text-[28px] font-medium leading-9 tracking-display">
            Privacy Policy
          </h2>
          <LegalBody updated="October 2026" sections={SECTIONS} />
        </section>
      </main>
      <SiteFooter />
    </div>
  );
}
