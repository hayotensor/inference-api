"use client";

import { ConnectButton } from "@rainbow-me/rainbowkit";
import { WalletCards } from "lucide-react";

import { Button, type ButtonProps } from "@/components/ui/button";

/**
 * RainbowKit's stock `<ConnectButton />` ships its own pill-shaped, blue/
 * gradient styling that clashes with the brand kit's monochrome buttons (see
 * app/globals.css). `ConnectButton.Custom` hands back the same connect/
 * account/chain-switch modal triggers as bare functions, so we render them
 * through the app's own `Button` instead. Shared by the dashboard overview's
 * wallet tile and the dedicated Wallet page so both open the same modal.
 */
export function WalletConnectButton({
  size,
  className
}: Pick<ButtonProps, "size" | "className"> = {}) {
  return (
    <ConnectButton.Custom>
      {({ account, chain, openAccountModal, openChainModal, openConnectModal, mounted }) => {
        const ready = mounted;
        const connected = ready && account && chain;

        return (
          <div
            aria-hidden={!ready}
            style={!ready ? { opacity: 0, pointerEvents: "none", userSelect: "none" } : undefined}
          >
            {!connected ? (
              <Button size={size} className={className} onClick={openConnectModal}>
                <WalletCards />
                Connect wallet
              </Button>
            ) : chain.unsupported ? (
              <Button size={size} className={className} variant="outline" onClick={openChainModal}>
                <WalletCards />
                Wrong network
              </Button>
            ) : (
              <Button
                size={size}
                className={className}
                onClick={openAccountModal}
                title={account.address}
              >
                <WalletCards />
                Switch wallet ({account.displayName})
              </Button>
            )}
          </div>
        );
      }}
    </ConnectButton.Custom>
  );
}
