"use client";

import * as React from "react";
import "@rainbow-me/rainbowkit/styles.css";
import { RainbowKitProvider, useAccountModal, useChainModal, useConnectModal } from "@rainbow-me/rainbowkit";
import { QueryClientProvider, useQueryClient } from "@tanstack/react-query";
import { WagmiProvider, useAccount, useConfig, useSignMessage } from "wagmi";

import { rainbowKitDarkTheme, rainbowKitLightTheme } from "@/lib/rainbowkit-theme";
import { useTheme } from "@/lib/theme";
import { wagmiConfig } from "@/lib/wagmi";
import type { WalletRuntimeProps } from "@/lib/wallet";

export function WalletRuntime({ onChange }: WalletRuntimeProps) {
  const queryClient = useQueryClient();
  const { theme } = useTheme();
  return (
    <WagmiProvider config={wagmiConfig}>
      <QueryClientProvider client={queryClient}>
        <RainbowKitProvider theme={theme === "dark" ? rainbowKitDarkTheme : rainbowKitLightTheme}>
          <WalletObserver onChange={onChange} />
        </RainbowKitProvider>
      </QueryClientProvider>
    </WagmiProvider>
  );
}

function WalletObserver({ onChange }: WalletRuntimeProps) {
  const { address, isConnected, chainId } = useAccount();
  const { chains } = useConfig();
  const { signMessageAsync } = useSignMessage();
  const { openConnectModal } = useConnectModal();
  const { openAccountModal } = useAccountModal();
  const { openChainModal } = useChainModal();
  const unsupported = isConnected && !chains.some((chain) => chain.id === chainId);
  const openWallet = isConnected
    ? unsupported ? openChainModal : openAccountModal
    : openConnectModal;

  React.useEffect(() => {
    onChange({ address, isConnected, unsupported, openWallet, signMessageAsync });
  }, [address, isConnected, unsupported, openWallet, signMessageAsync, onChange]);

  return null;
}
