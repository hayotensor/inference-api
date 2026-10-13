import { connectorsForWallets, getDefaultConfig } from "@rainbow-me/rainbowkit";
import { injectedWallet } from "@rainbow-me/rainbowkit/wallets";
import { createConfig, custom, http } from "wagmi";
import { mainnet } from "wagmi/chains";
import { UI_DEMO_MODE } from "@/lib/demo-mode";

const projectId = UI_DEMO_MODE ? "" : process.env.NEXT_PUBLIC_WALLETCONNECT_PROJECT_ID ?? "";

if (!UI_DEMO_MODE && !projectId && typeof window !== "undefined") {
  // eslint-disable-next-line no-console
  console.warn(
    "NEXT_PUBLIC_WALLETCONNECT_PROJECT_ID is unset -- falling back to browser-extension " +
      "wallets only. Get a free WalletConnect Cloud / Reown project id at " +
      "https://cloud.reown.com, set NEXT_PUBLIC_WALLETCONNECT_PROJECT_ID in .env.local, " +
      "and every mobile/hardware wallet lights up too."
  );
}

/**
 * RainbowKit + wagmi client config, shared by the dashboard's wallet page
 * (linking, `/wallets/evm/*`) and the login page's sign-in-with-wallet
 * (`/auth/wallet/*`). This is the "connect any wallet" layer -- injected
 * extensions (MetaMask, Rabby, Coinbase) AND WalletConnect's much larger set
 * of mobile/hardware wallets via QR code -- that used to be bypassed by
 * talking to `window.ethereum` directly.
 *
 * Only mainnet is configured: every wallet interaction here is `personal_sign`
 * over an opaque backend-issued nonce (see app/wallets/service.py,
 * app/auth/wallet.py), never a transaction, so chain choice doesn't change
 * what gets signed -- it just needs to match `wallet_siwe_chain_id`'s default
 * of 1 (app/core/config.py) for RainbowKit's chain switcher to make sense.
 *
 * `getDefaultConfig`'s curated wallet list (MetaMask, Rainbow, Coinbase, ...)
 * builds each entry's WalletConnect connector EAGERLY, so it throws at config
 * construction -- not at connect-click -- the moment `projectId` is empty,
 * which took down every page's prerender. Until a real project id is set,
 * fall back to RainbowKit's own `injectedWallet` (EIP-6963 multi-injected
 * discovery: every installed extension still shows up by name); it never
 * touches WalletConnect, so it needs no project id.
 */
export const wagmiConfig = projectId
  ? getDefaultConfig({
      appName: "Talaris",
      projectId,
      chains: [mainnet],
      ssr: true
    })
  : createConfig({
      chains: [mainnet],
      // Disabling connectors alone still lets wagmi hydrate/write its normal
      // localStorage entry. Demo must leave the real wallet session alone.
      ...(UI_DEMO_MODE ? { storage: null } : {}),
      multiInjectedProviderDiscovery: !UI_DEMO_MODE,
      connectors: UI_DEMO_MODE ? [] : connectorsForWallets([{ groupName: "Installed", wallets: [injectedWallet] }], {
        appName: "Talaris",
        projectId: ""
      }),
      transports: {
        [mainnet.id]: UI_DEMO_MODE
          ? custom({ request: async () => { throw new Error("Blockchain connections are disabled in the local UI demo."); } }, { retryCount: 0 })
          : http()
      },
      ssr: true
    });
