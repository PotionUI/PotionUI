import { test, expect } from '@playwright/test';
import { OWNER } from './helpers';

// Regression for the login-panel flash: after a successful submit, the login
// form must disappear at most once (when the shell leaves the public route)
// and never come back before /generate has taken over. A MutationObserver
// records every visibility transition of #username so the assertion holds
// regardless of how fast/slow the post-login navigation happens to be in this
// environment - a flash that only shows up under a slow chunk load is still
// a real bug.
test('login form does not reappear between successful submit and the app shell', async ({ page }) => {
	await page.goto('/login');
	await page.locator('#username').fill(OWNER.username);
	await page.locator('#password').fill(OWNER.password);

	await page.evaluate(() => {
		(window as any).__loginFlashSeen = false;
		let sawGone = false;
		const observer = new MutationObserver(() => {
			const present = !!document.getElementById('username');
			if (!present) {
				sawGone = true;
			} else if (sawGone) {
				(window as any).__loginFlashSeen = true;
			}
		});
		observer.observe(document.body, { childList: true, subtree: true });
		(window as any).__loginFlashObserver = observer;
	});

	await page.getByRole('button', { name: 'Sign In' }).click();
	await page.waitForURL(/\/generate/, { timeout: 15000 });

	const flashSeen = await page.evaluate(() => (window as any).__loginFlashSeen);
	expect(flashSeen, 'login form reappeared after disappearing once during the post-login redirect').toBe(
		false
	);
});
