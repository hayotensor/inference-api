import { redirect } from "next/navigation";

import { referralSignupPath } from "@/lib/referral-link";

// HYP-93: `/r/<code>` short referral link -> `/signup?ref=<code>`.
export default function ReferralShortLinkPage({ params }: { params: { code: string } }) {
  redirect(referralSignupPath(params.code));
}
