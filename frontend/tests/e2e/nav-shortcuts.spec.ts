import { test, expect } from '@playwright/test';
import { loginAsOwner } from './helpers';

// Navigation shortcuts are backend-seeded keybindings (keybinding_defaults,
// migration 046 + 106 + later renumbers) matched by the global keydown
// listener in frontend/src/lib/services/keyboard.ts. This spec presses the
// digit keys the sidebar advertises via its `actionId` and asserts each one
// lands on the expected route, catching both a missing seed row and a
// handler that never gets attached.

test('digit shortcuts navigate to their sidebar pages', async ({ page }) => {
	await loginAsOwner(page);

	// The keydown listener attaches only after the keybindings fetch resolves,
	// so a single press fired right after login can land before init and be
	// lost. Re-press until the first navigation proves the handler is live;
	// after that, single presses are deterministic.
	await expect
		.poll(
			async () => {
				await page.keyboard.press('2');
				await page.waitForTimeout(300);
				return new URL(page.url()).pathname;
			},
			{ timeout: 10000 }
		)
		.toMatch(/\/history/);

	await page.keyboard.press('3');
	await page.waitForURL(/\/library/, { timeout: 10000 });

	await page.keyboard.press('4');
	await page.waitForURL(/\/models/, { timeout: 10000 });

	await page.keyboard.press('1');
	await page.waitForURL(/\/generate/, { timeout: 10000 });
});
