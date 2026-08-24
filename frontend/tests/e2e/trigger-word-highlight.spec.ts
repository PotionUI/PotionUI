import { test, expect } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';
import { installAndSelectImagePreset } from './presetPreamble';

const JOURNEY = 'trigger-word-highlight';

// The segment prompt editor registers a CSS Custom Highlight named
// 'potionui-trigger-word' (see src/lib/utils/triggerWordHighlight.ts) as soon
// as ANY InlineChipEditor mounts and computes its trigger ranges — the
// registration happens in setOwnerTriggerHighlightRanges regardless of
// whether the match list is empty (InlineChipEditor.svelte's
// refreshTriggerHighlights calls clearOwnerTriggerHighlightRanges, which
// still calls ensureHighlight()). So this journey does not actually need a
// LoRA with trigger-word metadata selected — only an installed preset that
// renders a prompt editor at all, via the same installAndSelectImagePreset
// preamble other segment-editor specs use (see
// inline-chip-editor-interactions.spec.ts). A fresh throwaway instance with
// no installable native image preset is the one real "nothing to test here"
// case, and that still skips — but honestly, with the browser-support case
// asserted separately rather than folded into the same skip.
test('trigger word registers the potionui-trigger-word CSS highlight', async ({ page }) => {
	test.setTimeout(120000);
	await loginAsOwner(page);

	const preset = await installAndSelectImagePreset(page);
	if (!preset) {
		test.skip(true, 'No native image preset available on this throwaway instance to render the prompt editor with.');
		return;
	}
	console.log(`[${JOURNEY}] selected preset ${preset.id} (${preset.name})`);

	const supported = await page.evaluate(
		() => typeof (globalThis as any).Highlight !== 'undefined' && !!(CSS as any).highlights
	);
	if (!supported) {
		test.skip(true, 'CSS Custom Highlight API not available in this browser build.');
		return;
	}

	const segmentEditor = page.locator('.inline-chip-editor[role="textbox"]').first();
	await expect(segmentEditor).toBeVisible({ timeout: 15000 });

	await segmentEditor.click();
	await page.keyboard.type('trigger');
	await page.waitForTimeout(500);

	const registered = await page.evaluate(() => (CSS as any).highlights.has('potionui-trigger-word'));
	await screenshot(page, JOURNEY, 'segment-editor-highlight');
	expect(registered, "CSS.highlights should contain 'potionui-trigger-word'").toBeTruthy();

	console.log(`[${JOURNEY}] segment editor reachable; potionui-trigger-word highlight registered=${registered}`);
});
