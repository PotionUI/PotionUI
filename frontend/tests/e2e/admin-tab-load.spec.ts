import { test, expect } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';

const JOURNEY = 'admin-tab-load';

// Repro for: Admin -> Stats and Admin -> Documentation both render
// "Unable to load this admin tool." (LazyAdminTab.svelte's catch-all error
// state) instead of their content. The catch swallows the real error, so this
// spec surfaces it via console/pageerror listeners and fails while the tabs
// stay broken - a real regression test, not a passive probe.
test('Stats and Documentation admin tabs load without error', async ({ page }) => {
	const consoleErrors: string[] = [];
	const pageErrors: string[] = [];
	const chunk404s: string[] = [];

	page.on('console', (msg) => {
		if (msg.type() === 'error') {
			const text = `[console.error] ${msg.text()}`;
			consoleErrors.push(text);
			console.log(`[${JOURNEY}] ${text}`);
		}
	});
	page.on('pageerror', (err) => {
		const text = `[pageerror] ${err.message}\n${err.stack ?? ''}`;
		pageErrors.push(text);
		console.log(`[${JOURNEY}] ${text}`);
	});
	page.on('response', (res) => {
		if (res.status() >= 400) {
			console.log(`[${JOURNEY}] [http ${res.status()}] ${res.request().method()} ${res.url()}`);
			if (res.url().includes('/_app/immutable/')) {
				chunk404s.push(`${res.status()} ${res.url()}`);
			}
		}
	});
	page.on('requestfailed', (req) => {
		console.log(`[${JOURNEY}] [requestfailed] ${req.method()} ${req.url()} - ${req.failure()?.errorText}`);
		if (req.url().includes('/_app/immutable/')) {
			chunk404s.push(`FAILED ${req.url()} - ${req.failure()?.errorText}`);
		}
	});

	await loginAsOwner(page);

	const errorState = page.getByText('Unable to load this admin tool.', { exact: true });
	const nav = page.getByRole('main').locator('nav');

	// Land on the default (settings) tab via a normal navigation, then click
	// through the nav like a real user would - client-side routing, not a
	// full page reload each time. Visit a couple of other tabs first so the
	// router/module cache is warm, matching a realistic session.
	await page.goto('/admin');
	await expect(page.getByText('Unable to load this admin tool.')).toHaveCount(0);

	await nav.getByRole('link', { name: 'Models', exact: true }).click();
	await page.waitForTimeout(500);
	await nav.getByRole('link', { name: 'Presets', exact: true }).click();
	await page.waitForTimeout(500);

	// --- Stats tab ---
	await nav.getByRole('link', { name: 'Stats' }).click();
	await page.waitForTimeout(2000);
	await screenshot(page, JOURNEY, 'stats-tab');
	console.log(`[${JOURNEY}] stats tab console errors:\n${consoleErrors.join('\n')}`);
	console.log(`[${JOURNEY}] stats tab page errors:\n${pageErrors.join('\n')}`);
	await expect(errorState, 'Stats tab should not show the generic load-failure message').toHaveCount(0);

	consoleErrors.length = 0;
	pageErrors.length = 0;

	// --- Documentation tab ---
	await nav.getByRole('link', { name: 'Documentation' }).click();
	await page.waitForTimeout(2000);
	await screenshot(page, JOURNEY, 'docs-tab');
	console.log(`[${JOURNEY}] docs tab console errors:\n${consoleErrors.join('\n')}`);
	console.log(`[${JOURNEY}] docs tab page errors:\n${pageErrors.join('\n')}`);
	await expect(errorState, 'Documentation tab should not show the generic load-failure message').toHaveCount(0);

	consoleErrors.length = 0;
	pageErrors.length = 0;

	// --- System Settings tab (sectioned master-detail rebuild) ---
	await nav.getByRole('link', { name: 'System Settings' }).click();
	await page.waitForTimeout(1500);
	await screenshot(page, JOURNEY, 'system-settings-tab');
	console.log(`[${JOURNEY}] system settings tab console errors:\n${consoleErrors.join('\n')}`);
	console.log(`[${JOURNEY}] system settings tab page errors:\n${pageErrors.join('\n')}`);
	await expect(errorState, 'System Settings tab should not show the generic load-failure message').toHaveCount(0);

	for (const section of ['Content Safety', 'Prompt Search']) {
		await page.getByRole('option', { name: section }).click();
		await page.waitForTimeout(500);
		await expect(
			page.getByRole('heading', { name: section, level: 3 }),
			`${section} section should render its detail panel`
		).toBeVisible();
	}

	// --- Reload straight onto each tab too (covers a fresh full navigation) ---
	await page.goto('/admin?tab=stats');
	await page.waitForTimeout(1500);
	await screenshot(page, JOURNEY, 'stats-tab-direct-load');
	await expect(errorState, 'Stats tab (direct load) should not show the generic load-failure message').toHaveCount(0);

	await page.goto('/admin?tab=docs');
	await page.waitForTimeout(1500);
	await screenshot(page, JOURNEY, 'docs-tab-direct-load');
	await expect(errorState, 'Documentation tab (direct load) should not show the generic load-failure message').toHaveCount(0);

	// --- Reload in place on the docs tab, then re-open stats via SPA nav ---
	// (per lead's request: exercises the "tab open across a rebuild" shape -
	// a hard reload picking up whatever chunk manifest is live right now,
	// then a client-side nav back to stats using the reloaded module graph).
	await page.reload();
	await page.waitForTimeout(1500);
	await screenshot(page, JOURNEY, 'docs-tab-after-reload');
	await expect(errorState, 'Documentation tab (after page.reload()) should not show the generic load-failure message').toHaveCount(0);

	await nav.getByRole('link', { name: 'Stats' }).click();
	await page.waitForTimeout(1500);
	await screenshot(page, JOURNEY, 'stats-tab-after-reload-nav');
	await expect(errorState, 'Stats tab (SPA nav after reload) should not show the generic load-failure message').toHaveCount(0);

	console.log(`[${JOURNEY}] chunk 404s/failures on /_app/immutable/**: ${chunk404s.length ? chunk404s.join(' | ') : '(none)'}`);
	expect(chunk404s, `expected no failed chunk fetches, got: ${chunk404s.join(' | ')}`).toHaveLength(0);
});
