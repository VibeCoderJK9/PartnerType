import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "happy-dom",
    include: ["js/__tests__/**/*.test.js"],
    // Every realm gets the locale files, as /locales.js gives the browser.
    setupFiles: ["js/__tests__/helpers/setup-locales.js"],
    coverage: {
      provider: "v8",
      reporter: [["text", { maxCols: 200 }], "html"],
      include: [
        "js/admin-affiliate-settings.js",
        "js/admin-affiliate.js",
        "js/admin-dashboard.js",
        "js/admin-integrations.js",
        "js/admin-leads.js",
        "js/admin-partners.js",
        "js/admin-shell.js",
        "js/api.js",
        "js/app.js",
        "js/auth.js",
        "js/charts.js",
        "js/claude-connect.js",
        "js/lead-fields.js",
        "js/oauth-consent.js",
        "js/partner-affiliate.js",
        "js/partner-earnings.js",
        "js/partner-lead-details.js",
        "js/partner.js",
        "js/pt-ref.js",
        "js/set-password.js",
        "js/utils.js",
      ],
      thresholds: {
        lines: 70,
        statements: 70,
        functions: 65,
        branches: 60,
      },
    },
  },
});
