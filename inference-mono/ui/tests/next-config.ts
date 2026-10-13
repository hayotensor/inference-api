import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import type { NextConfig } from "next";
import ts from "typescript";

// Evaluate the actual config with fresh environment values, without loading
// Next.js, sharing module caches across modes, or starting any services.
export function loadNextConfig(env: Record<string, string | undefined>): NextConfig {
  const source = ts.transpileModule(readFileSync("next.config.mjs", "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS }
  }).outputText;
  const sandbox = { exports: {} as { default: NextConfig }, process: { env } };
  runInNewContext(source, sandbox);
  return sandbox.exports.default;
}
