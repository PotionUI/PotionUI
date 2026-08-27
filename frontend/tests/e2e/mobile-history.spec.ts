import { test, expect } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';

const JOURNEY = 'mobile-history';

// Phone-viewport shell contract for the history page header. Guards the class
// of regression where a desktop-only flex row (title + count + search +
// filter controls, none of it wrapping) is wider than the viewport at 375px.
// The app's root <main> carries `overflow-x-hidden` as a page-scroll safety
// net, so this bug does NOT show up as document horizontal scroll — it shows
// up as controls silently clipped off the right edge (the search-mode toggle
// rendered ~75px past the viewport and vanished). scrollWidth/clientWidth is
// checked too, but the descendant-rect walk below is what actually catches it.
test.use({ viewport: { width: 375, height: 812 }, hasTouch: true });

test('history header fits and stays usable at phone width', async ({ page }) => {
	await loginAsOwner(page);
	await page.goto('/history');

	await expect(page.locator('nav').filter({ hasText: 'History' }).last()).toBeVisible();

	const { overflowX } = await page.evaluate(() => {
		const el = document.scrollingElement!;
		return { overflowX: el.scrollWidth - el.clientWidth };
	});
	expect(overflowX, 'page must not scroll horizontally').toBeLessThanOrEqual(0);

	// The header's own bounding box must not exceed the viewport width either
	// — a flex-shrink-0 row can overflow its sticky parent even when some
	// outer ancestor still reports scrollWidth == clientWidth.
	const header = page.locator('header').first();
	const headerBox = await header.boundingBox();
	expect(headerBox, 'header has a box').toBeTruthy();
	expect(headerBox!.width, 'header does not exceed viewport width').toBeLessThanOrEqual(375);

	// No control inside the header renders past the viewport's right edge —
	// this is the assertion that actually catches the clipped-toggle
	// regression, since <main>'s overflow-x-hidden hides it from scrollWidth.
	const maxRight = await page.evaluate(() => {
		const el = document.querySelector('header');
		let max = 0;
		el?.querySelectorAll('*').forEach((node) => {
			const r = node.getBoundingClientRect();
			if (r.width > 0) max = Math.max(max, r.right);
		});
		return max;
	});
	expect(maxRight, 'no header control extends past the viewport').toBeLessThanOrEqual(375 + 1);

	// The search input — the toolbar's primary control — is visible and
	// fully inside the viewport.
	const search = page.getByPlaceholder('Search generations...');
	await expect(search).toBeVisible();
	const searchBox = await search.boundingBox();
	expect(searchBox, 'search input has a box').toBeTruthy();
	expect(searchBox!.x + searchBox!.width, 'search input right edge on-screen').toBeLessThanOrEqual(375 + 1);

	await screenshot(page, JOURNEY, 'history-header');

	// The grid area is present beneath the header.
	await expect(page.locator('main, [class*="grid"]').first()).toBeVisible();

	// The bottom tab bar sits fully inside the viewport, same contract as
	// mobile-generate.
	const tabBar = page.locator('nav.fixed.bottom-0');
	const tabBox = await tabBar.boundingBox();
	expect(tabBox!.y + tabBox!.height, 'tab bar bottom edge on-screen').toBeLessThanOrEqual(812 + 1);
});
