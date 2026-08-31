"use client";

import * as React from "react";
import { toast } from "sonner";

import { apiErrorMessage } from "@/lib/api";

type EthereumProvider = {
  request: (args: { method: string; params?: unknown[] }) => Promise<unknown>;
  on?: (event: string, listener: (...args: unknown[]) => void) => void;
  removeListener?: (event: string, listener: (...args: unknown[]) => void) => void;
};

declare global {
  interface Window {
    ethereum?: EthereumProvider;
  }
}

function firstAccount(value: unknown): string | null {
  if (!Array.isArray(value)) return null;
  const [account] = value;
  return typeof account === "string" && account ? account : null;
}

/**
 * Browser-wallet connection over window.ethereum (EIP-1193). Shared by the
 * dashboard wallet page (association) and the login page (sign-in-with-wallet).
 */
export function useBrowserWallet() {
  const [address, setAddress] = React.useState<string | null>(null);
  const [isConnecting, setIsConnecting] = React.useState(false);
  const [hasProvider, setHasProvider] = React.useState(false);

  React.useEffect(() => {
    const provider = window.ethereum;
    setHasProvider(Boolean(provider));
    if (!provider) return;

    void provider
      .request({ method: "eth_accounts" })
      .then((accounts) => setAddress(firstAccount(accounts)))
      .catch(() => setAddress(null));

    const handleAccountsChanged = (accounts: unknown) => {
      setAddress(firstAccount(accounts));
    };
    provider.on?.("accountsChanged", handleAccountsChanged);
    return () => provider.removeListener?.("accountsChanged", handleAccountsChanged);
  }, []);

  const connect = React.useCallback(async () => {
    const provider = window.ethereum;
    if (!provider) {
      toast.error("No browser wallet detected");
      return;
    }
    setIsConnecting(true);
    try {
      const accounts = await provider.request({ method: "eth_requestAccounts" });
      setAddress(firstAccount(accounts));
    } catch (error) {
      toast.error(apiErrorMessage(error));
    } finally {
      setIsConnecting(false);
    }
  }, []);

  const signMessage = React.useCallback(
    async (message: string) => {
      const provider = window.ethereum;
      if (!provider || !address) throw new Error("Connect a wallet first.");
      const signature = await provider.request({
        method: "personal_sign",
        params: [message, address]
      });
      if (typeof signature !== "string") throw new Error("Wallet did not return a signature.");
      return signature;
    },
    [address]
  );

  return {
    address,
    connect,
    hasProvider,
    isConnected: Boolean(address),
    isConnecting,
    signMessage
  };
}
