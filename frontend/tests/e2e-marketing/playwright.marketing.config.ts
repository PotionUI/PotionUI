import { defineConfig, devices } from '@playwright/test';

// Dedicated config for marketing-capture scenes (scripts/marketing_capture.py).
// Deliberately its own testDir/outputDir - separate from
// frontend/playwright.config.ts (the frontend/tests/e2e/ regression specs
// tests/e2e/ui/run.py drives) so a bare `npx playwright test` from either
// harness never picks up the other's specs by accident.
const baseURL = process.env.E2E_BASE_URL || 'http://127.0.0.1:4173';

export default defineConfig({
	testDir: '.',
	outputDir: './.playwright-artifacts',
	fullyParallel: false,
	workers: 1,
	forbidOnly: !!process.env.CI,
	retries: 0,
	timeout: 90_000,
	reporter: [['list']],
	use: {
		baseURL,
		// One fixed viewport across every scene so the recordings feel like one
		// coherent product (shot_list.md's "capture environment notes").
		viewport: { width: 1440, height: 900 },
		video: {
			mode: 'on',
			size: { width: 1440, height: 900 }
		},
		screenshot: 'off',
		trace: 'off'
	},
	projects: [
		{
			name: 'chromium',
			use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } }
		}
	]
});
