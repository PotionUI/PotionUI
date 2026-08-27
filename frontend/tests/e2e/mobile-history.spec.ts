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

// Desktop width: the "More actions" overflow menu lives in the sticky header
// (HistoryToolbar.svelte), which is its own stacking context — a z-index
// inside it cannot out-rank a z-40 element that lives outside that context
// entirely (e.g. GenerationCard.svelte's per-card overlays), no matter how
// high it's set. The only assertion that actually proves paint order is
// `elementFromPoint`; a visibility/boundingBox check would pass even with the
// menu fully hidden underneath something opaque.
test.describe('history more-actions menu stacking', () => {
	test.use({ viewport: { width: 1280, height: 900 }, hasTouch: false });

	test('More actions menu paints above a z-40 sibling', async ({ page }) => {
		await loginAsOwner(page);
		await page.goto('/history');

		const moreButton = page.getByRole('button', { name: 'More actions' });
		await expect(moreButton).toBeVisible();

		// Open once to find where the first menu item lands, then close so the
		// probe can be sized against real coordinates instead of a guess.
		await moreButton.click();
		const firstItem = page.getByRole('menuitem', { name: 'Upload generations' });
		const itemBox = (await firstItem.boundingBox())!;
		expect(itemBox, 'menu item has a box').toBeTruthy();
		await page.keyboard.press('Escape');
		await expect(firstItem).toBeHidden();

		// The history grid may or may not have real generations in this
		// throwaway instance, so the z-40 competitor is simulated directly —
		// this reproduces the exact paint contest GenerationCard's z-40
		// overlays create without depending on seeded data.
		await page.evaluate((box) => {
			const probe = document.createElement('div');
			probe.id = 'z40-stacking-probe';
			probe.style.cssText = `position: fixed; z-index: 40; top: ${box.y}px; left: ${box.x}px; width: ${box.width}px; height: ${box.height}px; background: red;`;
			document.body.appendChild(probe);
		}, itemBox);

		await moreButton.click();
		await expect(firstItem).toBeVisible();

		const center = { x: itemBox!.x + itemBox!.width / 2, y: itemBox!.y + itemBox!.height / 2 };
		const hit = await page.evaluate(({ x, y }) => {
			const el = document.elementFromPoint(x, y);
			return {
				isProbe: el?.id === 'z40-stacking-probe',
				resolvesToMenuItem: !!el?.closest('[role="menuitem"]')
			};
		}, center);

		expect(hit.isProbe, 'the z-40 probe must not occlude the menu item').toBe(false);
		expect(hit.resolvesToMenuItem, 'the point must resolve to the menu item itself').toBe(true);

		await screenshot(page, JOURNEY, 'more-menu-above-z40-probe');
	});
});
