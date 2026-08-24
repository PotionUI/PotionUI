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

	await page.keyboard.press('2');
	await page.waitForURL(/\/history/, { timeout: 10000 });

	await page.keyboard.press('3');
	await page.waitForURL(/\/library/, { timeout: 10000 });

	await page.keyboard.press('4');
	await page.waitForURL(/\/models/, { timeout: 10000 });

	await page.keyboard.press('1');
	await page.waitForURL(/\/generate/, { timeout: 10000 });
});
