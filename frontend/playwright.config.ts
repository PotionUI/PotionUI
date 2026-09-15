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
		screenshot: process.env.CI ? 'only-on-failure' : 'on',
		trace: 'retain-on-failure',
		video: process.env.CI ? 'retain-on-failure' : 'on'
	},
	projects: [
		{
			name: 'chromium',
			use: { ...devices['Desktop Chrome'] }
		}
	]
});
