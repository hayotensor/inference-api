"use client";

import { Loader2, WalletCards } from "lucide-react";

import { Button, type ButtonProps } from "@/components/ui/button";
import { UI_DEMO_MODE } from "@/lib/demo-mode";
import { shortenAddress } from "@/lib/format";
import { useWallet } from "@/lib/wallet";

export function WalletConnectButton({
  size,
  className
}: Pick<ButtonProps, "size" | "className"> = {}) {
  const { address, isConnected, unsupported, loading, openWallet } = useWallet();
  if (UI_DEMO_MODE) {
    return <Button size={size} className={className} disabled>Wallet connection unavailable in demo</Button>;
  }
  return (
    <Button
      size={size}
      className={className}
      variant={isConnected ? "outline" : "default"}
      onClick={openWallet}
      disabled={loading}
      title={address}
    >
      {loading ? <Loader2 className="animate-spin" /> : <WalletCards />}
      {unsupported ? "Wrong network" : isConnected && address ? `Switch wallet (${shortenAddress(address)})` : "Connect wallet"}
    </Button>
  );
}
