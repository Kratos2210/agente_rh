import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
  {
    rules: {
      // Session guards read localStorage on mount and set state synchronously
      // (login/Shell/observabilidad). The pattern runs once and is intentional;
      // restructuring it for this rule adds churn without user-visible benefit.
      "react-hooks/set-state-in-effect": "off",
    },
  },
]);

export default eslintConfig;
