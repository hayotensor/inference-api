"use client";

import Link from "next/link";
import { XCircle } from "lucide-react";

import { PublicNav } from "@/components/public-nav";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

export default function BillingCancelPage() {
  return (
    <div className="min-h-screen bg-muted/30">
      <PublicNav />
      <main className="page-shell flex min-h-[calc(100vh-4rem)] items-center justify-center py-10">
        <Card className="w-full max-w-xl">
          <CardContent className="p-6 text-center">
            <XCircle className="mx-auto h-10 w-10 text-amber-600" />
            <h1 className="mt-4 text-2xl font-semibold tracking-normal">Checkout canceled</h1>
            <p className="mt-2 text-sm text-muted-foreground">
              No subscription changes were applied from this checkout attempt.
            </p>
            <div className="mt-6 flex flex-col justify-center gap-3 sm:flex-row">
              <Button asChild>
                <Link href="/pricing">Return to pricing</Link>
              </Button>
              <Button variant="outline" asChild>
                <Link href="/dashboard/billing">Open billing</Link>
              </Button>
            </div>
          </CardContent>
        </Card>
      </main>
    </div>
  );
}
