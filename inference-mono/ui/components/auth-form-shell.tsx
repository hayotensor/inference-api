import Link from "next/link";

import { AppLogo } from "@/components/app-logo";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle
} from "@/components/ui/card";

export function AuthFormShell({
  title,
  description,
  children,
  footer
}: {
  title: string;
  description: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
}) {
  return (
    <main className="min-h-screen bg-muted/35">
      <div className="page-shell flex min-h-screen flex-col items-center justify-center py-10">
        <AppLogo className="mb-8" />
        <Card className="w-full max-w-md">
          <CardHeader>
            <CardTitle>{title}</CardTitle>
            <CardDescription>{description}</CardDescription>
          </CardHeader>
          <CardContent>{children}</CardContent>
        </Card>
        {footer ? (
          <div className="mt-6 text-center text-sm text-muted-foreground">
            {footer}
          </div>
        ) : (
          <Link href="/" className="mt-6 text-sm text-muted-foreground hover:text-foreground">
            Back to home
          </Link>
        )}
      </div>
    </main>
  );
}
