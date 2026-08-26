import { test, expect } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';

const JOURNEY = 'mobile-generate';

// Phone-viewport shell contract for the generate page. Guards the class of
// regression where the mobile carousel drifts out of alignment (panels sized
// in vw instead of container %), the page overflows behind the fixed bottom
// tab bar (100vh shells inside a pb-reserved main), or the transport bar /
// install banner stack in the wrong order.
test.use({ viewport: { width: 375, height: 812 }, hasTouch: true });

test('generate page fits and aligns at phone width', async ({ page }) => {
	await loginAsOwner(page);
	await page.goto('/generate');

	// Mobile chrome is up: panel strip + bottom tab bar, no desktop sidebar.
	const panelStrip = page.getByRole('region', { name: 'Swipeable panels' });
	await expect(panelStrip).toBeVisible({ timeout: 15000 });
	await expect(page.locator('nav').filter({ hasText: 'Generate' }).last()).toBeVisible();

	// No page-level scrolling in either axis: horizontal overflow was how every
	// layout bug degraded (main is a scroller), and vertical overflow means the
	// shell slid behind the fixed tab bar. scrollbar-gutter can make scrollWidth
	// come up SHORT of clientWidth, so assert "no overflow", not equality.
	const { overflowX, overflowY } = await page.evaluate(() => {
		const el = document.scrollingElement!;
		return {
			overflowX: el.scrollWidth - el.clientWidth,
			overflowY: el.scrollHeight - el.clientHeight
		};
	});
	expect(overflowX, 'page must not scroll horizontally').toBeLessThanOrEqual(0);
	expect(overflowY, 'page shell must fit the viewport (no scroll behind the tab bar)').toBeLessThanOrEqual(0);

	// The active panel (Generate, index 2) is exactly aligned with the swipe
	// container: same left edge, same width. This is the regression the old
	// 100vw-sized panels failed — they were wider than the container by the
	// scrollbar gutter, bleeding each panel into the next.
	const panels = panelStrip.locator('.mobile-panel');
	await expect(panels).toHaveCount(4);
	const containerBox = await panelStrip.boundingBox();
	const box = await panels.nth(2).boundingBox();
	expect(box, 'active panel has a box').toBeTruthy();
	expect(Math.abs(box!.x - containerBox!.x), 'active panel aligned with container').toBeLessThanOrEqual(1);
	expect(Math.abs(box!.width - containerBox!.width), 'panel spans the container').toBeLessThanOrEqual(1);

	await screenshot(page, JOURNEY, 'generate-panel');

	// Switching to the Form panel keeps alignment and keeps the transport bar
	// (the mobile Generate button) visible.
	await page.getByRole('button', { name: 'Form' }).click();
	await expect(page.getByRole('button', { name: /Generate|Cancel generation|generate/i }).last()).toBeVisible();
	// Let the 300ms slide transition settle before measuring alignment.
	await page.waitForTimeout(500);
	const formBox = await panels.nth(1).boundingBox();
	expect(Math.abs(formBox!.x - containerBox!.x), 'form panel aligned with container').toBeLessThanOrEqual(1);

	await screenshot(page, JOURNEY, 'form-panel');

	// The bottom tab bar must sit fully inside the viewport.
	const tabBar = page.locator('nav.fixed.bottom-0');
	const tabBox = await tabBar.boundingBox();
	expect(tabBox!.y + tabBox!.height, 'tab bar bottom edge on-screen').toBeLessThanOrEqual(812 + 1);
	expect(tabBox!.y, 'tab bar visible above viewport bottom').toBeGreaterThan(700);
});
