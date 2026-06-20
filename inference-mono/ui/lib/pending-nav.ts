"use client";

import * as React from "react";

export function navPathMatches(pathname: string, href: string, rootHref: string) {
  return pathname === href || (href !== rootHref && pathname.startsWith(`${href}/`));
}

export function usePendingNavPath(pathname: string) {
  const [pendingHref, setPendingHref] = React.useState<string | null>(null);

  React.useEffect(() => {
    setPendingHref(null);
  }, [pathname]);

  const markPendingHref = React.useCallback(
    (href: string, event: React.MouseEvent<HTMLAnchorElement>) => {
      if (
        event.defaultPrevented ||
        event.button !== 0 ||
        event.metaKey ||
        event.altKey ||
        event.ctrlKey ||
        event.shiftKey ||
        (event.currentTarget.target && event.currentTarget.target !== "_self")
      ) {
        return;
      }

      if (href === pathname) {
        return;
      }

      setPendingHref(href);
    },
    [pathname]
  );

  return {
    activePath: pendingHref ?? pathname,
    isPending: pendingHref !== null,
    markPendingHref
  };
}
