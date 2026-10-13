// Keep the environment check at the import boundary so webpack can omit the
// entire wallet dependency graph from the local demo compilation.
export const loadWalletRuntime =
  process.env.NODE_ENV === "development" && process.env.NEXT_PUBLIC_UI_DEMO === "true"
    ? async () => { throw new Error("Wallet connections are disabled in the local UI demo."); }
    : () => import("@/components/wallet-runtime");
