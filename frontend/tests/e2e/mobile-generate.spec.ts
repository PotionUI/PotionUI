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

	// Regression: the browser fires touchcancel (not touchend) when it takes a
	// gesture over mid-swipe — long-press selection, notification shade, the
	// screenshot gesture. The stranded drag delta must not freeze the carousel
	// at a fractional offset, and tapping the panel strip (which lives outside
	// the swipe container) must always land exactly aligned.
	await page.evaluate(() => {
		const region = document.querySelector('[aria-label="Swipeable panels"]')!;
		const touch = (x: number) =>
			new Touch({ identifier: 1, target: region, clientX: x, clientY: 300 });
		const fire = (type: string, touches: Touch[]) =>
			region.dispatchEvent(
				new TouchEvent(type, { bubbles: true, cancelable: true, touches, changedTouches: touches })
			);
		fire('touchstart', [touch(300)]);
		fire('touchmove', [touch(100)]); // 200px left drag — swipe latched
		fire('touchcancel', []);
	});
	await page.getByRole('button', { name: 'Generate', exact: true }).first().click();
	await page.waitForTimeout(500);
	const recoveredBox = await panels.nth(2).boundingBox();
	expect(
		Math.abs(recoveredBox!.x - containerBox!.x),
		'carousel realigned after touchcancel + strip tap'
	).toBeLessThanOrEqual(1);

	// The bottom tab bar must sit fully inside the viewport.
	const tabBar = page.locator('nav.fixed.bottom-0');
	const tabBox = await tabBar.boundingBox();
	expect(tabBox!.y + tabBox!.height, 'tab bar bottom edge on-screen').toBeLessThanOrEqual(812 + 1);
	expect(tabBox!.y, 'tab bar visible above viewport bottom').toBeGreaterThan(700);
});

test('carousel recovers from a stray scrollLeft on the swipe container', async ({ page }) => {
	// Regression: the off-screen LLM chat panel autofocuses its composer on
	// mount, and a browser's default focus scroll-into-view sets scrollLeft on
	// the nearest clipping ancestor even though that container has no visible
	// scrollbar and is never meant to scroll under user control. That offset
	// used to latch forever because the carousel's translateX math never
	// accounted for it. Simulates the same effect directly (any focus deep in
	// an off-screen panel would set scrollLeft the same way).
	await loginAsOwner(page);
	await page.goto('/generate');

	const panelStrip = page.getByRole('region', { name: 'Swipeable panels' });
	await expect(panelStrip).toBeVisible({ timeout: 15000 });
	const panels = panelStrip.locator('.mobile-panel');
	await expect(panels).toHaveCount(4);
	const containerBox = await panelStrip.boundingBox();

	await page.evaluate(() => {
		const region = document.querySelector('[aria-label="Swipeable panels"]')!;
		region.scrollLeft = 200;
	});
	await page.waitForTimeout(200);

	const scrollLeft = await page.evaluate(
		() => document.querySelector('[aria-label="Swipeable panels"]')!.scrollLeft
	);
	expect(scrollLeft, 'swipe container scroll position resets itself').toBe(0);

	// Active panel (Generate, index 2, the default) still lines up exactly.
	const box = await panels.nth(2).boundingBox();
	expect(Math.abs(box!.x - containerBox!.x), 'active panel realigned after stray scroll').toBeLessThanOrEqual(1);
});
