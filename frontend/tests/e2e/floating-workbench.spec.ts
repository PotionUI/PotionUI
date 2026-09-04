import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';

// The floating workbench (`toggle_floating_workbench` keybinding,
// default `w`) is a centered window above the Generation Panel (bottom-
// [73px]), sized from the workbench's own settings — width from the inline
// pane's measured width, height from `tab.workbenchMaxHeight` (the same
// value the workbench's own height slider sets) plus this window's header —
// not a full-bleed overlay. This spec asserts that sizing, drags the
// corner resize handle and checks the window and the slider both grew, and
// keeps the original not-covering-the-Generation-Panel assertion.
//
// GenerationPanels (and the floating workbench mounted inside it) only
// renders once a preset AND mode are selected — an empty/unselected tab
// renders a placeholder instead — so this installs and selects a real preset
// first, the same way images-stepper.spec.ts does.

const JOURNEY = 'floating-workbench';
const SIZE_TOLERANCE_PX = 8;

// The default viewport is short enough that the window's `max-height: 100%`
// safety clamp kicks in even at the default `workbenchMaxHeight` (600px) —
// correct floating-window behavior, but it breaks this spec's exact-size
// assertions for reasons that have nothing to do with the floating workbench. A taller
// viewport keeps the window unclamped so the size math is testable.
test.use({ viewport: { width: 1400, height: 1000 } });

async function apiGet(page: Page, url: string, token: string) {
	const res = await page.request.get(url, { headers: { Authorization: `Bearer ${token}` } });
	expect(res.ok(), `GET ${url} -> ${res.status()}`).toBeTruthy();
	return res.json();
}

async function apiPost(page: Page, url: string, token: string, data?: unknown) {
	const res = await page.request.post(url, {
		headers: { Authorization: `Bearer ${token}` },
		data: data ?? {}
	});
	expect(res.ok(), `POST ${url} -> ${res.status()}`).toBeTruthy();
	return res.json();
}

/** The workbench's own height-slider readout ("NNNpx"), present in the DOM
 *  (if not always visually opaque) once the workbench has rendered. */
async function readSliderHeight(page: Page): Promise<number> {
	const label = page.locator('span.font-mono.tabular-nums', { hasText: /^\d+px$/ }).first();
	await expect(label).toBeAttached({ timeout: 10000 });
	const text = await label.textContent();
	return parseInt(text ?? '0', 10);
}

test('floating workbench opens at the docked size and resizes from its own handles', async ({ page }) => {
	await loginAsOwner(page);
	const token = await ownerToken(page);

	const me = await apiGet(page, '/api/auth/me', token);
	const userId = me.data.id as string;

	const list = await apiGet(page, '/api/presets?include_uninstalled=true', token);
	const presets = (list.data || []) as Array<{
		id: string;
		name: string;
		engine?: string;
		category?: string;
		installed?: boolean;
	}>;
	const preset =
		presets.find((p) => /sdxl/i.test(p.name)) ||
		presets.find((p) => p.engine === 'native' && p.category === 'image');

	if (!preset) {
		test.skip(true, 'No native image preset available on this throwaway instance.');
		return;
	}

	if (!preset.installed) {
		await apiPost(page, `/api/presets/${preset.id}/install`, token);
	}
	await apiPost(page, `/api/presets/${preset.id}/assign`, token, { user_ids: [userId] });

	await page.goto('/generate');
	await page.getByRole('button', { name: 'Choose a preset' }).click();
	await page.getByText(preset.name, { exact: true }).first().click();
	await page.getByRole('button', { name: /Use this preset|Keep selected/ }).click();

	const tablist = page.locator('[role="tablist"]').first();
	await expect(tablist).toBeVisible({ timeout: 20000 });

	// The mark's accessible name depends on whether a prompt is filled in
	// (canGenerate/disabledReason) — this spec cares about screen position,
	// not that copy, so it targets the generate control's own class
	// (GenerateMark.svelte, the panel template's `.generate-button`).
	const generateMark = page.locator('button.generate-button');
	await expect(generateMark).toBeVisible({ timeout: 10000 });

	const dockedPane = page.locator('[data-testid="workbench-pane"]');
	await expect(dockedPane).toBeVisible({ timeout: 10000 });
	const dockedBox = await dockedPane.boundingBox();
	expect(dockedBox).not.toBeNull();
	const dockedSliderHeight = await readSliderHeight(page);

	await page.keyboard.press('w');

	const overlay = page.getByRole('dialog', { name: 'Workbench' });
	await expect(overlay).toBeVisible({ timeout: 10000 });
	await screenshot(page, JOURNEY, '01-opened');

	// Width: the window opens at the inline pane's own measured width.
	const openedBox = await overlay.boundingBox();
	expect(openedBox).not.toBeNull();
	expect(Math.abs(openedBox!.width - dockedBox!.width)).toBeLessThanOrEqual(SIZE_TOLERANCE_PX);

	// Height: the window wraps `workbenchMaxHeight` (read off the workbench's
	// own height-slider label) plus its own header chrome — not the full
	// overlay height.
	const header = page.locator('[data-testid="floating-workbench-header"]');
	await expect(header).toBeVisible();
	const headerBox = await header.boundingBox();
	expect(headerBox).not.toBeNull();
	// The stage keeps its set height unless the window would not fit above
	// the panel, in which case it is reduced through the same setting.
	const openedSliderHeight = await readSliderHeight(page);
	expect(openedSliderHeight).toBeLessThanOrEqual(dockedSliderHeight);
	expect(openedBox!.height).toBeGreaterThanOrEqual(openedSliderHeight + headerBox!.height - SIZE_TOLERANCE_PX);

	// Never a scrollbar inside the window: it takes the workbench's full
	// natural height, and the whole window sits above the Generation Panel.
	const scroll = await overlay.evaluate((el) => {
		const body = el.querySelector('[data-testid="floating-workbench-header"] + div') as HTMLElement;
		return { scrollHeight: body.scrollHeight, clientHeight: body.clientHeight, overflowY: getComputedStyle(body).overflowY };
	});
	expect(scroll.overflowY).not.toBe('auto');
	expect(scroll.scrollHeight).toBeLessThanOrEqual(scroll.clientHeight + 1);
	const viewport = page.viewportSize()!;
	expect(openedBox!.y + openedBox!.height).toBeLessThanOrEqual(viewport.height - 73);

	// The overlay must stop above the Generation Panel (bottom-[73px]), so at
	// the mark's own screen position nothing from the overlay (its backdrop
	// or its dialog) sits in the hit-test stack — real page content is there
	// instead. (The mark itself may be a disabled <button>, which browsers
	// exclude from hit-testing entirely, so this checks the stack rather than
	// asserting the mark is the top hit.)
	await expect(generateMark).toBeVisible();
	const markBox = await generateMark.boundingBox();
	expect(markBox).not.toBeNull();
	const stackLabels = await page.evaluate(
		({ x, y }) =>
			document
				.elementsFromPoint(x, y)
				.map((el) => (el as HTMLElement).getAttribute('aria-label') || ''),
		{ x: markBox!.x + markBox!.width / 2, y: markBox!.y + markBox!.height / 2 }
	);
	expect(stackLabels).not.toContain('Close floating workbench');
	expect(stackLabels).not.toContain('Workbench');

	// Drag the corner resize handle and confirm both the window and the
	// workbench's own height slider grew to match.
	const cornerHandle = overlay.getByRole('button', { name: 'Resize workbench', exact: true });
	await expect(cornerHandle).toBeVisible();
	const cornerBox = await cornerHandle.boundingBox();
	expect(cornerBox).not.toBeNull();
	const startX = cornerBox!.x + cornerBox!.width / 2;
	const startY = cornerBox!.y + cornerBox!.height / 2;

	await page.mouse.move(startX, startY);
	await page.mouse.down();
	await page.mouse.move(startX + 120, startY + 80, { steps: 8 });
	await page.mouse.up();
	await screenshot(page, JOURNEY, '02-resized');

	const resizedBox = await overlay.boundingBox();
	expect(resizedBox).not.toBeNull();
	expect(resizedBox!.width).toBeGreaterThan(openedBox!.width + 40);
	expect(resizedBox!.height).toBeGreaterThan(openedBox!.height + 20);

	const resizedSliderHeight = await readSliderHeight(page);
	expect(resizedSliderHeight).toBeGreaterThan(openedSliderHeight);

	await page.keyboard.press('Escape');
	await expect(overlay).not.toBeVisible({ timeout: 10000 });
});
