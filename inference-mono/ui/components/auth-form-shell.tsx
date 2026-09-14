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
    <main className="min-h-dvh bg-background">
      <div className="page-shell flex min-h-dvh flex-col items-center justify-center py-10">
        <AppLogo className="mb-10" />
        <Card className="w-full max-w-md">
          <CardHeader>
            <CardTitle>{title}</CardTitle>
            <CardDescription>{description}</CardDescription>
          </CardHeader>
          <CardContent>{children}</CardContent>
        </Card>
        {footer ? (
          <div className="mt-8 text-center text-sm text-muted-foreground">
            {footer}
          </div>
        ) : (
          <Link
            href="/"
            className="focus-ring mt-8 text-[11px] font-medium uppercase tracking-label text-muted-foreground transition-colors duration-fast ease-standard hover:text-foreground"
          >
            Back to home
          </Link>
        )}
      </div>
    </main>
  );
}
