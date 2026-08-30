import { test, expect } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';

const JOURNEY = 'mobile-modals';

// Phone-viewport contract for the app's modals/overlays: BaseModal already
// goes full-screen below md, but content inside modals (and a few overlay
// wrappers) used desktop-sized boxes that could still push the document
// wider than the viewport. Guards the class of regression where a modal or
// slide-over renders correctly on desktop but overflows or gets clipped at
// phone width.
test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

test('AI chat sheet spans the full viewport width on phones with no page overflow', async ({ page }) => {
	// GlobalChatPanel (lib/components/GlobalChatPanel.svelte) is the surface
	// this journey was written to cover, and its width fix
	// (`w-full md:w-[1000px] md:max-w-[90vw]`) lives entirely in that file.
	// It is NOT reachable through the UI at this viewport though: its only
	// mount point, routes/+layout.svelte, wraps it in `<div class="hidden
	// md:block">` unconditionally (line ~258) — the desktop-only Sidebar AI
	// Chat trigger is the only thing that ever opens it, and Sidebar itself
	// is wrapped the same way. That gate is route-independent (not specific
	// to /generate) and lives outside this task's assigned files, so it was
	// left untouched. The mobile Studio shell has its own separate chat
	// entry point (StudioTopBar's "Open AI chat" button -> StudioChatSheet),
	// which wraps the same UnifiedAIChat surface GlobalChatPanel does and is
	// the only chat surface actually reachable on a phone today — this test
	// exercises that one instead.
	await loginAsOwner(page);
	await page.goto('/generate');

	await expect(page.locator('.studio-dock')).toBeVisible({ timeout: 15000 });
	await page.getByRole('button', { name: 'Open AI chat' }).click();

	const chatSheet = page.getByRole('dialog', { name: 'AI chat' });
	await expect(chatSheet).toBeVisible({ timeout: 5000 });

	const sheetBox = await chatSheet.boundingBox();
	expect(sheetBox, 'chat sheet has a box').toBeTruthy();
	expect(Math.abs(sheetBox!.x), 'chat sheet flush with viewport left edge').toBeLessThanOrEqual(1);
	expect(Math.abs(sheetBox!.width - 390), 'chat sheet spans the full viewport width').toBeLessThanOrEqual(1);

	const { overflowX } = await page.evaluate(() => {
		const el = document.scrollingElement!;
		return { overflowX: el.scrollWidth - el.clientWidth };
	});
	expect(overflowX, 'page must not scroll horizontally with the chat sheet open').toBeLessThanOrEqual(0);

	await screenshot(page, JOURNEY, 'ai-chat-sheet');
});

test('a modal opened from a Studio sheet does not overflow the phone viewport', async ({ page }) => {
	// The preset picker (lib/components/preset/PresetPicker.svelte, a BaseModal
	// consumer) is reachable from a fresh throwaway instance with no seeded
	// generations or history — it renders even with zero installed presets
	// (the Pane empty state). Same reachability path as the sibling
	// mobile-generate.spec.ts overlay test, but asserting document overflow
	// rather than the backdrop's own box.
	await loginAsOwner(page);
	await page.goto('/generate');

	await expect(page.locator('.studio-dock')).toBeVisible({ timeout: 15000 });
	await page.getByRole('button', { name: 'Open preset and session' }).click();
	const presetSheet = page.getByRole('dialog', { name: 'Preset and session' });
	await expect(presetSheet).toBeVisible({ timeout: 5000 });

	await presetSheet.locator('button[aria-haspopup="dialog"]').first().click();

	const pickerDialog = page.getByRole('dialog').filter({ hasText: 'Choose preset' });
	await expect(pickerDialog).toBeVisible({ timeout: 5000 });

	const { overflowX } = await page.evaluate(() => {
		const el = document.scrollingElement!;
		return { overflowX: el.scrollWidth - el.clientWidth };
	});
	expect(overflowX, 'page must not scroll horizontally with the preset picker open').toBeLessThanOrEqual(0);

	const dialogBox = await pickerDialog.boundingBox();
	expect(dialogBox, 'picker dialog has a box').toBeTruthy();
	expect(dialogBox!.x + dialogBox!.width, 'picker dialog right edge stays on-screen').toBeLessThanOrEqual(390 + 1);

	await screenshot(page, JOURNEY, 'preset-picker-modal');
});
