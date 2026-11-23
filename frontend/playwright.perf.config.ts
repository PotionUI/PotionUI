import { defineConfig, devices } from '@playwright/test';
import { AUTH_STATE_PATH } from './tests/e2e/helpers';

const baseURL = process.env.E2E_BASE_URL || 'http://127.0.0.1:4173';

export default defineConfig({
	testDir: './tests/perf',
	testMatch: '**/*.perf.ts',
	outputDir: './tests/perf/.playwright-artifacts',
	fullyParallel: false,
	workers: 1,
	retries: 0,
	timeout: 600000,
	globalSetup: './tests/e2e/global-setup.ts',
	reporter: [['list']],
	use: {
		baseURL,
		storageState: AUTH_STATE_PATH,
		screenshot: 'off',
		trace: 'off',
		video: 'off',
		viewport: { width: 1440, height: 900 },
		deviceScaleFactor: 1,
		reducedMotion: 'no-preference'
	},
	projects: [
		{
			name: 'chromium',
			use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 }
		}
	]
});
