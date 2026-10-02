"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, RefreshCw, Trash2, WalletCards } from "lucide-react";
import { toast } from "sonner";
import { useAccount, useSignMessage } from "wagmi";

import { DashboardPageHeader } from "@/components/dashboard-shell";
import { EmptyState, ErrorState, LoadingState } from "@/components/data-state";
import { NoWalletNotice } from "@/components/no-wallet-notice";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { WalletConnectButton } from "@/components/wallet-connect-button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow
} from "@/components/ui/table";
import { apiErrorMessage, walletsApi } from "@/lib/api";
import { formatDateTime, formatNumber, shortenAddress } from "@/lib/format";
import type { BalanceSyncResponse } from "@/lib/types";

export default function WalletPage() {
  const queryClient = useQueryClient();
  const { address, isConnected } = useAccount();
  const { signMessageAsync } = useSignMessage();

  const walletQuery = useQuery({
    queryKey: ["wallet-evm"],
    queryFn: walletsApi.get
  });

  const verifyMutation = useMutation({
    mutationFn: async () => {
      if (!address) throw new Error("Connect a wallet first.");
      const nonce = await walletsApi.nonce(address);
      const signature = await signMessageAsync({ message: nonce.message });
      return walletsApi.verify({
        address: nonce.address,
        message: nonce.message,
        signature
      });
    },
    onSuccess: async () => {
      toast.success("Wallet verified");
      await queryClient.invalidateQueries({ queryKey: ["wallet-evm"] });
      await queryClient.invalidateQueries({ queryKey: ["usage-current"] });
      await queryClient.invalidateQueries({ queryKey: ["usage-allowance"] });
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });

  const syncMutation = useMutation({
    mutationFn: walletsApi.syncBalances,
    onSuccess: async () => {
      toast.success("Balances synced");
      await queryClient.invalidateQueries({ queryKey: ["wallet-evm"] });
      await queryClient.invalidateQueries({ queryKey: ["usage-current"] });
      await queryClient.invalidateQueries({ queryKey: ["usage-allowance"] });
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });

  const unlinkMutation = useMutation({
    mutationFn: walletsApi.unlink,
    onSuccess: async () => {
      toast.success("Wallet unlinked");
      await queryClient.invalidateQueries({ queryKey: ["wallet-evm"] });
    },
    onError: (error) => toast.error(apiErrorMessage(error))
  });

  const syncData = syncMutation.data as BalanceSyncResponse | undefined;

  return (
    <>
      <DashboardPageHeader
        title="Wallet"
        description="Wallet ownership is verified by signing a backend-provided nonce."
        action={<WalletConnectButton />}
      />

      <div className="grid gap-4 lg:grid-cols-[0.9fr_1.1fr]">
        <Card>
          <CardHeader>
            <CardTitle>EVM wallet</CardTitle>
            <CardDescription>
              Connect any wallet (a browser extension or, via WalletConnect, a mobile or
              hardware wallet), then verify it below to link it to your account.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-5">
            <div className="rounded-lg border bg-muted/35 p-4">
              <div className="flex items-center justify-between gap-4">
                <span className="text-sm text-muted-foreground">Connected wallet</span>
                <Badge variant={isConnected ? "success" : "outline"}>
                  {isConnected ? "connected" : "not connected"}
                </Badge>
              </div>
              <p className="mt-2 break-all font-mono text-sm">
                {address ?? "Connect a wallet to continue"}
              </p>
            </div>

            <NoWalletNotice />

            {!isConnected ? (
              <EmptyState
                title="No wallet connected"
                description="Use Connect wallet above to link any EVM wallet: MetaMask, Rabby, and Coinbase Wallet's extension connect directly, and WalletConnect covers mobile and hardware wallets by QR code."
              />
            ) : null}

            {walletQuery.isLoading ? <LoadingState /> : null}
            {walletQuery.isError ? <ErrorState error={walletQuery.error} /> : null}
            {walletQuery.data ? (
              <div className="rounded-lg border p-4 text-sm">
                <div className="mb-3 flex items-center gap-2 font-medium">
                  <WalletCards className="h-4 w-4" />
                  Verified wallet
                </div>
                <div className="space-y-2">
                  <div className="flex justify-between gap-4">
                    <span className="text-muted-foreground">Address</span>
                    <span className="font-mono">{shortenAddress(walletQuery.data.address)}</span>
                  </div>
                  <div className="flex justify-between gap-4">
                    <span className="text-muted-foreground">Verified</span>
                    <span>{formatDateTime(walletQuery.data.verified_at)}</span>
                  </div>
                  <div className="flex justify-between gap-4">
                    <span className="text-muted-foreground">Last balance sync</span>
                    <span>{formatDateTime(walletQuery.data.last_balance_sync_at)}</span>
                  </div>
                </div>
              </div>
            ) : (
              <EmptyState
                title="No verified wallet"
                description="Connect an EVM wallet, request a nonce from the API, and sign it in your browser."
              />
            )}

            <div className="grid gap-2 sm:grid-cols-3">
              <Button
                onClick={() => verifyMutation.mutate()}
                disabled={!isConnected || verifyMutation.isPending}
              >
                {verifyMutation.isPending ? (
                  <Loader2 className="animate-spin" />
                ) : (
                  <WalletCards />
                )}
                Verify
              </Button>
              <Button
                variant="outline"
                onClick={() => syncMutation.mutate()}
                disabled={!walletQuery.data || syncMutation.isPending}
              >
                {syncMutation.isPending ? (
                  <Loader2 className="animate-spin" />
                ) : (
                  <RefreshCw />
                )}
                Sync
              </Button>
              <Button
                variant="outline"
                onClick={() => unlinkMutation.mutate()}
                disabled={!walletQuery.data || unlinkMutation.isPending}
              >
                {unlinkMutation.isPending ? (
                  <Loader2 className="animate-spin" />
                ) : (
                  <Trash2 />
                )}
                Unlink
              </Button>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Balance sync</CardTitle>
            <CardDescription>
              Allowances and balances are displayed from `/wallets/evm/sync-balances`.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {!syncData ? (
              <EmptyState
                title="No balance sync in this session"
                description="Run sync after verifying a wallet to display backend balance snapshots."
              />
            ) : null}
            {syncData ? (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Chain</TableHead>
                    <TableHead>Type</TableHead>
                    <TableHead>Balance</TableHead>
                    <TableHead>Allowance</TableHead>
                    <TableHead>Checked</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {syncData.snapshots.map((snapshot, index) => (
                    <TableRow key={`${snapshot.chain}-${snapshot.token_type}-${index}`}>
                      <TableCell>{snapshot.chain}</TableCell>
                      <TableCell>{snapshot.token_type}</TableCell>
                      <TableCell>{snapshot.normalized_balance}</TableCell>
                      <TableCell>{formatNumber(snapshot.inference_token_allowance)}</TableCell>
                      <TableCell>{formatDateTime(snapshot.checked_at)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            ) : null}
          </CardContent>
        </Card>
      </div>
    </>
  );
}
