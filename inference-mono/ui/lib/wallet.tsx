"use client";

import * as React from "react";
import { toast } from "sonner";

import { loadWalletRuntime } from "@/lib/wallet-loader";

export type WalletState = {
  address?: `0x${string}`;
  isConnected: boolean;
  unsupported: boolean;
  openWallet?: () => void;
  signMessageAsync?: (args: { message: string }) => Promise<`0x${string}`>;
};

export type WalletRuntimeProps = { onChange: (state: WalletState) => void };

type WalletContextValue = Omit<WalletState, "openWallet" | "signMessageAsync"> & {
  loading: boolean;
  openWallet: () => void;
  signMessageAsync: (args: { message: string }) => Promise<`0x${string}`>;
};

const WalletContext = React.createContext<WalletContextValue | undefined>(undefined);

// The SDK renders beside the app after the first wallet interaction. Loading
// it never replaces/remounts the page, auth provider, or query cache.
export function WalletProvider({ children }: { children: React.ReactNode }) {
  const [Runtime, setRuntime] = React.useState<React.ComponentType<WalletRuntimeProps> | null>(null);
  const [state, setState] = React.useState<WalletState>({ isConnected: false, unsupported: false });
  const [opening, setOpening] = React.useState(false);
  const importPending = React.useRef(false);
  const { openWallet: openLoadedWallet, signMessageAsync: signLoadedMessage } = state;

  const openWallet = React.useCallback(() => {
    if (openLoadedWallet) {
      openLoadedWallet();
      return;
    }
    setOpening(true);
    if (Runtime || importPending.current) return;
    importPending.current = true;
    void loadWalletRuntime()
      .then((module) => setRuntime(() => module.WalletRuntime))
      .catch(() => {
        setOpening(false);
        toast.error("Could not load wallet connection. Please try again.");
      })
      .finally(() => { importPending.current = false; });
  }, [Runtime, openLoadedWallet]);

  React.useEffect(() => {
    if (!opening || !openLoadedWallet) return;
    setOpening(false);
    openLoadedWallet();
  }, [opening, openLoadedWallet]);

  const signMessageAsync = React.useCallback(async (args: { message: string }) => {
    if (!signLoadedMessage) throw new Error("Connect a wallet first.");
    return signLoadedMessage(args);
  }, [signLoadedMessage]);

  const value = React.useMemo(() => ({
    address: state.address,
    isConnected: state.isConnected,
    unsupported: state.unsupported,
    loading: opening,
    openWallet,
    signMessageAsync
  }), [state.address, state.isConnected, state.unsupported, opening, openWallet, signMessageAsync]);

  return (
    <WalletContext.Provider value={value}>
      {children}
      {Runtime ? <Runtime onChange={setState} /> : null}
    </WalletContext.Provider>
  );
}

export function useWallet() {
  const wallet = React.useContext(WalletContext);
  if (!wallet) throw new Error("useWallet must be used inside WalletProvider");
  return wallet;
}
