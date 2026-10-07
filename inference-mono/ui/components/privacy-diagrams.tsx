import type { CSSProperties, ReactNode } from "react";

/**
 * Hairline infographics for /privacy (HYP-80).
 *
 * Every diagram is inline SVG drawn with `currentColor` at 1px (see the
 * `.pv-*` rules in globals.css), so light and dark themes both work and no
 * meaning rides on colour alone: "who can read the data" is shown by solid vs
 * dashed outlines AND by text labels. Motion is CSS-only and disabled under
 * `prefers-reduced-motion`, where each diagram rests in a complete state.
 *
 * Each diagram mirrors something the code really does -- the file/function it
 * comes from is named in the section copy on the page, not here.
 */

type Style = CSSProperties & Record<`--${string}`, string | number>;

function Frame({ label, children }: { label: string; children: ReactNode }) {
  return (
    <figure className="my-5 rounded-sm border border-border p-3 sm:p-4">
      <svg
        className="pv-svg"
        viewBox="0 0 640 190"
        role="img"
        aria-label={label}
        xmlns="http://www.w3.org/2000/svg"
      >
        {children}
      </svg>
    </figure>
  );
}

function Box({
  x,
  y,
  w,
  h,
  title,
  sub,
  dashed
}: {
  x: number;
  y: number;
  w: number;
  h: number;
  title: string;
  sub?: string;
  dashed?: boolean;
}) {
  return (
    <g>
      <rect x={x} y={y} width={w} height={h} rx={3} className={dashed ? "pv-dash" : undefined} />
      <text x={x + w / 2} y={y + h / 2 - (sub ? 3 : -3)} textAnchor="middle" className="pv-t">
        {title}
      </text>
      {sub ? (
        <text x={x + w / 2} y={y + h / 2 + 11} textAnchor="middle" className="pv-t pv-m">
          {sub}
        </text>
      ) : null}
    </g>
  );
}

function Travel({ x, y, dx, delay = 0, dur = 3.2 }: { x: number; y: number; dx: number; delay?: number; dur?: number }) {
  const style: Style = { "--pv-dx": `${dx}px`, "--pv-delay": `${delay}s`, "--pv-dur": `${dur}s` };
  return <circle cx={x} cy={y} r={2.5} className="pv-dot pv-travel" style={style} />;
}

/** 1. Request path: who can read the plaintext at each hop. */
export function RequestPathDiagram() {
  return (
    <Frame label="Request path: your client sends over TLS to the Talaris API, which forwards over a certificate-pinned TLS connection through a miner ingress that only relays ciphertext, into the enclave. Client, API and enclave can read the prompt; the ingress cannot.">
      <Box x={8} y={50} w={110} h={54} title="Your client" sub="app / SDK / chat" />
      <Box x={170} y={50} w={130} h={54} title="Talaris API" sub="auth, meter, route" />
      <Box x={352} y={50} w={110} h={54} title="Miner ingress" sub="SNI passthrough" dashed />
      <Box x={514} y={50} w={118} h={54} title="Enclave (TEE)" sub="model + prompt" />

      <line x1={118} y1={77} x2={170} y2={77} className="pv-flow" />
      <line x1={300} y1={77} x2={352} y2={77} className="pv-flow" />
      <line x1={462} y1={77} x2={514} y2={77} className="pv-flow" />
      <Travel x={118} y={77} dx={52} />
      <Travel x={300} y={77} dx={52} delay={0.8} />
      <Travel x={462} y={77} dx={52} delay={1.6} />

      <text x={144} y={44} textAnchor="middle" className="pv-t pv-m">TLS</text>
      <text x={326} y={44} textAnchor="middle" className="pv-t pv-m">TLS, cert-pinned</text>
      <text x={488} y={44} textAnchor="middle" className="pv-t pv-m">same TLS</text>

      <line x1={8} y1={130} x2={632} y2={130} className="pv-faint" />
      <text x={63} y={150} textAnchor="middle" className="pv-t">reads plaintext</text>
      <text x={235} y={150} textAnchor="middle" className="pv-t">reads plaintext</text>
      <text x={407} y={150} textAnchor="middle" className="pv-t">ciphertext only</text>
      <text x={573} y={150} textAnchor="middle" className="pv-t">reads plaintext</text>
      <text x={320} y={178} textAnchor="middle" className="pv-t pv-m">
        solid outline = can read the prompt - dashed outline = cannot
      </text>
    </Frame>
  );
}

/** 2. What a trusted execution environment is. */
export function EnclaveDiagram() {
  return (
    <Frame label="A miner host with an untrusted operating system and operator, containing a hardware-isolated enclave. Reads from the host into enclave memory are blocked by the hardware.">
      <rect x={8} y={10} width={624} height={170} rx={4} className="pv-dash" />
      <text x={20} y={28} className="pv-t pv-m">MINER HOST - operator, OS, hypervisor: not trusted</text>

      <g className="pv-pulse">
        <rect x={300} y={44} width={320} height={124} rx={4} />
      </g>
      <text x={312} y={62} className="pv-t pv-m">ENCLAVE - Intel TDX VM + NVIDIA confidential GPU</text>
      <Box x={316} y={76} w={140} h={36} title="model weights" />
      <Box x={466} y={76} w={140} h={36} title="prompt + output" />
      <Box x={316} y={122} w={290} h={34} title="engine (vLLM / SGLang) + tee_wrapper" />

      <Box x={24} y={76} w={120} h={36} title="host process" sub="debugger / dump" dashed />
      <line x1={144} y1={94} x2={270} y2={94} className="pv-flow" />
      <Travel x={144} y={94} dx={110} dur={2.4} />
      <line x1={278} y1={84} x2={292} y2={104} />
      <line x1={292} y1={84} x2={278} y2={104} />
      <text x={150} y={80} className="pv-t pv-m">memory read</text>
      <text x={150} y={128} className="pv-t pv-m">blocked by hardware</text>
    </Frame>
  );
}

/** 3. The remote-attestation handshake. */
export function AttestationDiagram() {
  const s = (k: number): Style => ({ "--pv-dur": "8s", "--pv-delay": `${k * 2}s` });
  return (
    <Frame label="Remote attestation in four steps: the platform sends a fresh random nonce; the enclave returns a document with a hardware quote and GPU evidence that bind the nonce, model hash and TLS key; the platform verifies the vendor signature chains; if everything matches the allowlist the platform seals credentials to the enclave.">
      <Box x={20} y={44} w={150} h={100} title="Platform" sub="verifier" />
      <Box x={470} y={44} w={150} h={100} title="Enclave" sub="producer" />

      <g className="pv-step" style={s(0)}>
        <line x1={170} y1={62} x2={470} y2={62} />
        <path d="M462 58 L470 62 L462 66" />
        <text x={320} y={55} textAnchor="middle" className="pv-t">1 fresh 32-byte nonce</text>
      </g>
      <g className="pv-step" style={s(1)}>
        <line x1={470} y1={88} x2={170} y2={88} />
        <path d="M178 84 L170 88 L178 92" />
        <text x={320} y={81} textAnchor="middle" className="pv-t">2 report_data + TDX quote + GPU evidence</text>
      </g>
      <g className="pv-step" style={s(2)}>
        <rect x={176} y={100} width={288} height={18} rx={2} className="pv-dash" />
        <text x={320} y={113} textAnchor="middle" className="pv-t">3 verify Intel + NVIDIA chains, allowlist</text>
      </g>
      <g className="pv-step" style={s(3)}>
        <line x1={170} y1={134} x2={470} y2={134} />
        <path d="M462 130 L470 134 L462 138" />
        <text x={320} y={127} textAnchor="middle" className="pv-t">4 seal credentials to enclave key</text>
      </g>
      <text x={320} y={174} textAnchor="middle" className="pv-t pv-m">
        report_data binds: nonce, enclave key, TLS key, model hash, launch args, GPU set, usage chain
      </text>
    </Frame>
  );
}

/** 4. Sealed credentials + TLS pinning: how the platform talks to an enclave. */
export function SealPinDiagram() {
  return (
    <Frame label="After attestation the platform seals a credential to the enclave's public key so only the enclave can open it, and records the SHA-256 fingerprint of the enclave's TLS certificate. Every later connection must present exactly that certificate or it is aborted.">
      <Box x={20} y={30} w={130} h={50} title="Platform" sub="holds fingerprint" />
      <Box x={490} y={30} w={130} h={50} title="Enclave" sub="holds private key" />

      <rect x={226} y={44} width={30} height={22} rx={2} />
      <path d="M232 44 v-6 a9 9 0 0 1 18 0 v6" />
      <text x={241} y={95} textAnchor="middle" className="pv-t">sealed box</text>
      <line x1={150} y1={55} x2={226} y2={55} className="pv-flow" />
      <line x1={256} y1={55} x2={490} y2={55} className="pv-flow" />
      <Travel x={150} y={55} dx={76} dur={3} />
      <Travel x={256} y={55} dx={230} dur={3} delay={1.5} />
      <text x={320} y={44} className="pv-t pv-m">/provision: only the enclave can open it</text>

      <line x1={8} y1={116} x2={632} y2={116} className="pv-faint" />

      <Box x={20} y={132} w={130} h={44} title="sha256(cert)" sub="== attested?" />
      <path d="M150 154 H490" className="pv-draw" style={{ "--pv-len": 340, "--pv-dur": "5s" } as Style} />
      <text x={320} y={146} textAnchor="middle" className="pv-t pv-m">every connection: compare leaf cert fingerprint</text>
      <Box x={490} y={132} w={130} h={44} title="TLS leaf cert" sub="fresh per boot" />
      <text x={320} y={172} textAnchor="middle" className="pv-t">mismatch = connection aborted, miner marked stale</text>
    </Frame>
  );
}

/** 5. A per-response signed receipt. */
export function ReceiptDiagram() {
  return (
    <Frame label="A response and its signed receipt. The platform signs a statement naming the request id, model, enclave key, TLS fingerprint and the time of the last hardware verification, with the same Ed25519 key that signs the model allowlist. Anyone can check it against the public key.">
      <Box x={20} y={40} w={150} h={46} title="Response" sub="your completion" />
      <rect x={250} y={24} width={200} height={140} rx={3} />
      <text x={262} y={44} className="pv-t">inference-receipt/v1</text>
      <line x1={250} y1={52} x2={450} y2={52} className="pv-faint" />
      <text x={262} y={70} className="pv-t pv-m">request_id</text>
      <text x={262} y={86} className="pv-t pv-m">model_id, model_hash</text>
      <text x={262} y={102} className="pv-t pv-m">enclave_verify_key</text>
      <text x={262} y={118} className="pv-t pv-m">tls_cert_fingerprint</text>
      <text x={262} y={134} className="pv-t pv-m">attestation_verified_at</text>
      <text x={262} y={154} className="pv-t">ed25519 signature</text>
      <line x1={170} y1={63} x2={250} y2={63} className="pv-flow" />
      <Travel x={170} y={63} dx={80} dur={2.4} />

      <Box x={500} y={40} w={124} h={46} title="You verify" sub="pinned /attest key" />
      <line x1={450} y1={63} x2={500} y2={63} className="pv-flow" />
      <Travel x={450} y={63} dx={50} dur={2.4} delay={1.2} />
      <text x={562} y={116} textAnchor="middle" className="pv-t pv-m">no prompt text</text>
      <text x={562} y={130} textAnchor="middle" className="pv-t pv-m">in the receipt</text>
    </Frame>
  );
}

/** 6. What E2EE would be, versus what this system does. */
export function EncryptionBoundaryDiagram() {
  return (
    <Frame label="Encryption boundaries. Connection one, client to Talaris API, is TLS and ends at the API. Connection two, API to enclave, is a separate TLS session. Plaintext exists at the API for the moment it re-encrypts the request, so this is not end-to-end encryption from your device to the enclave.">
      <Box x={12} y={52} w={110} h={46} title="Your device" />
      <Box x={265} y={52} w={110} h={46} title="Talaris API" sub="plaintext in RAM" />
      <Box x={518} y={52} w={110} h={46} title="Enclave" />

      <rect x={122} y={66} width={143} height={18} rx={9} className="pv-dash" />
      <text x={193} y={79} textAnchor="middle" className="pv-t">TLS session 1</text>
      <rect x={375} y={66} width={143} height={18} rx={9} className="pv-dash" />
      <text x={446} y={79} textAnchor="middle" className="pv-t">TLS session 2</text>

      <line x1={320} y1={110} x2={320} y2={132} />
      <path d="M314 126 L320 134 L326 126" />
      <text x={320} y={150} textAnchor="middle" className="pv-t">decrypt, meter, re-encrypt</text>
      <text x={320} y={174} textAnchor="middle" className="pv-t pv-m">
        two encrypted hops joined at the API - not end-to-end
      </text>
      <Travel x={122} y={75} dx={143} dur={3.4} />
      <Travel x={375} y={75} dx={143} dur={3.4} delay={1.7} />
    </Frame>
  );
}

/** 7. What the platform records, and what is absent. */
export function StorageDiagram() {
  const rows: Array<[string, string, boolean]> = [
    ["request id, timestamp", "recorded", true],
    ["model, token counts, charge", "recorded", true],
    ["which miner served it, latency, outcome", "recorded", true],
    ["API key id, endpoint, HTTP status", "recorded", true],
    ["prompt text", "not in any table", false],
    ["response text", "not in any table", false]
  ];
  return (
    <Frame label="Per-request record. The platform stores a request id, timestamp, model, token counts, charge, serving miner, latency, outcome, API key id, endpoint and status. Prompt text and response text have no column in any table.">
      {rows.map(([name, state, kept], i) => {
        const y = 14 + i * 28;
        return (
          <g key={name}>
            <rect x={12} y={y} width={616} height={22} rx={2} className={kept ? undefined : "pv-dash"} />
            <text x={24} y={y + 15} className="pv-t">{name}</text>
            <text x={616} y={y + 15} textAnchor="end" className={kept ? "pv-t" : "pv-t pv-m"}>
              {kept ? "[x] " : "[ ] "}
              {state}
            </text>
            {!kept ? (
              <line x1={20} y1={y + 11} x2={300} y2={y + 11} className="pv-draw" style={{ "--pv-len": 290, "--pv-dur": "5s", "--pv-delay": `${i * 0.3}s` } as Style} />
            ) : null}
          </g>
        );
      })}
    </Frame>
  );
}

/** 8. Attestation cadence timeline. */
export function CadenceDiagram() {
  const ticks = Array.from({ length: 13 }, (_, i) => 40 + i * 46);
  return (
    <Frame label="Attestation cadence over a day. Enclave re-attestation by the platform roughly hourly by default. Validators challenge a sample of miners every subnet epoch. The browser badge re-checks the signed model list every six hours while open. Each response carries a signed receipt.">
      <text x={8} y={22} className="pv-t pv-m">TIME -&gt; (about 12 hours shown)</text>
      <line x1={32} y1={42} x2={632} y2={42} />
      {ticks.map((x, i) => (
        <g key={x}>
          <line x1={x} y1={38} x2={x} y2={46} />
          {i % 2 === 0 ? <text x={x} y={60} textAnchor="middle" className="pv-t pv-m">{i}h</text> : null}
        </g>
      ))}

      <text x={8} y={84} className="pv-t">platform re-attests each enclave (default TTL 1h)</text>
      {ticks.map((x) => (
        <circle key={`a${x}`} cx={x} cy={96} r={3.5} className="pv-pulse" style={{ "--pv-delay": `${(x % 5) * 0.2}s` } as Style} />
      ))}

      <text x={8} y={122} className="pv-t">browser badge re-checks signed allowlist (6h)</text>
      {[40, 316, 592].map((x) => (
        <rect key={`b${x}`} x={x - 3.5} y={129} width={7} height={7} className="pv-pulse" />
      ))}

      <text x={8} y={160} className="pv-t">each response: signed receipt - each connection: TLS pin check</text>
      <line x1={32} y1={172} x2={632} y2={172} className="pv-flow" />
    </Frame>
  );
}

/** 9. Where the chat app keeps data. */
export function ChatStorageDiagram() {
  return (
    <Frame label="Chat app storage. By default conversations are saved only in your browser's own storage on your device. You can opt in per chat to save to your account, with optional passphrase encryption done in your browser before upload. The inference request itself always passes through the Talaris API.">
      <Box x={12} y={20} w={150} h={56} title="Your browser" sub="default: saved here" />
      <Box x={246} y={20} w={150} h={56} title="Your account" sub="opt-in, per chat" dashed />
      <Box x={480} y={20} w={148} h={56} title="Talaris API" sub="inference only" />

      <line x1={162} y1={48} x2={246} y2={48} className="pv-dash" />
      <text x={204} y={40} textAnchor="middle" className="pv-t pv-m">opt-in</text>
      <line x1={396} y1={48} x2={480} y2={48} className="pv-faint" />
      <text x={438} y={40} textAnchor="middle" className="pv-t pv-m">no history</text>

      <rect x={246} y={100} width={150} height={26} rx={2} />
      <text x={321} y={117} textAnchor="middle" className="pv-t">enc:v1 AES-GCM</text>
      <line x1={321} y1={76} x2={321} y2={100} className="pv-flow" />
      <text x={321} y={146} textAnchor="middle" className="pv-t pv-m">optional: sealed in the browser first,</text>
      <text x={321} y={160} textAnchor="middle" className="pv-t pv-m">key derived from your passphrase (PBKDF2)</text>
    </Frame>
  );
}
