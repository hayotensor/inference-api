// A frontend fixture mode, never a backend authentication bypass.
export const UI_DEMO_MODE =
  process.env.NODE_ENV === "development" && process.env.NEXT_PUBLIC_UI_DEMO === "true";

export const DEMO_EMAIL = "dev@example.com";
export const DEMO_PASSWORD = "local-demo";
