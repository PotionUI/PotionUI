import { defineConfig, devices } from '@playwright/test';

// The browser-UI E2E layer is driven by tests/e2e/ui/run.py, which boots
// a throwaway backend, serves the built frontend with `vite preview`, and exports
// the base URL + owner credentials through these env vars. Running
// `npx playwright test` on its own (without that bridge) has no server to hit and
// will fail fast at the first navigation — that is expected.
const baseURL = process.env.E2E_BASE_URL || 'http://127.0.0.1:4173';

export default defineConfig({
	testDir: './tests/e2e',
	outputDir: './tests/e2e/.playwright-artifacts',
	fullyParallel: false,
	workers: 1,
	forbidOnly: !!process.env.CI,
	retries: 0,
	reporter: [['list']],
	use: {
		baseURL,
		// Screenshots retained on both pass and failure — the maintainer reviews
		// them as evidence. Specs also save labeled screenshots of decisive states
		// into E2E_ARTIFACTS_DIR via page.screenshot().
		screenshot: 'on',
		trace: 'retain-on-failure',
		// Video for every test (pass, fail, or skip) — the run.py bridge renames
		// each clip to artifacts/<journey>/<journey>.webm as reviewable evidence.
		video: 'on'
	},
	projects: [
		{
			name: 'chromium',
			use: { ...devices['Desktop Chrome'] }
		}
	]
});
