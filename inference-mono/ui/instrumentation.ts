import { UI_DEMO_MODE } from "@/lib/demo-mode";

// HYP-58: start the status-history sampler when the server boots, so uptime
// is recorded even when nobody has the /status page open.
export async function register() {
  if (UI_DEMO_MODE) return;
  if (process.env.NEXT_RUNTIME === "nodejs") {
    const { ensureSampler } = await import("@/lib/server/status-history");
    await ensureSampler();
  }
}
