// HYP-93: short referral links (`/r/<code>`) forward to the signup page's
// existing `?ref=` handling, so `/signup?ref=<code>` links shared before this
// keep working unchanged. The backend matches codes case-insensitively and
// ignores unknown ones, so anything shaped like a code is passed through;
// anything else just lands on a plain signup.
const REFERRAL_CODE = /^[A-Za-z0-9_-]{1,32}$/;

export function referralSignupPath(rawCode: string) {
  let code: string;
  try {
    code = decodeURIComponent(rawCode);
  } catch {
    return "/signup";
  }
  return REFERRAL_CODE.test(code) ? `/signup?ref=${encodeURIComponent(code)}` : "/signup";
}
