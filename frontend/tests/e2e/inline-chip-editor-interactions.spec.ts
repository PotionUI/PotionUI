import { test, expect, type Page, type Locator } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';
import { installAndSelectImagePreset } from './presetPreamble';

const JOURNEY = 'inline-chip-editor-interactions';
const BEAT = 350;

// The four InlineChipEditor.svelte behaviors that genuinely
// need a browser (can't be pure-function-characterized) — see chipSegments.ts
// / chipEditorDom.ts / chipEditorHelpers.ts for the pure-logic coverage this
// complements. Uses {a|b}/${name} choice-group and variable-usage chips,
// which chipify client-side on typing — no phrasebook taxonomy fixture
// (models/tests seed data) is needed, only the installed-preset preamble to
// get a segment editor mounted at all.

async function segmentEditor(page: Page): Promise<Locator> {
	const editor = page.locator('.inline-chip-editor[role="textbox"]').first();
	await expect(editor).toBeVisible({ timeout: 15000 });
	return editor;
}

/** Selects all editor content and deletes it, via native contenteditable
 *  editing commands (not the app's chip-aware Backspace handler — Backspace
 *  only special-cases a COLLAPSED selection; a range selection falls through
 *  to the browser's own deleteContentBackward). Used to reset editor state
 *  between scenarios sharing one preset selection. */
async function clearEditor(page: Page, editor: Locator): Promise<void> {
	await editor.click();
	await page.keyboard.press('ControlOrMeta+a');
	await page.keyboard.press('Backspace');
	await expect(editor).toHaveText('');
}

async function openVariableManager(page: Page) {
	const variablesButton = page.getByRole('button', { name: 'Variables' });
	try {
		await variablesButton.waitFor({ state: 'visible', timeout: 10000 });
	} catch {
		const modeButton = page.getByRole('button', { name: /txt2img/i }).first();
		if ((await modeButton.count()) > 0) await modeButton.click();
		await variablesButton.waitFor({ state: 'visible', timeout: 10000 });
	}
	await variablesButton.click();
	const modal = page.getByRole('dialog').filter({ hasText: 'Prompt variables' });
	await expect(modal).toBeVisible({ timeout: 10000 });
	return modal;
}

/** Fills variable manager row `index` (0-based, in `Object.entries(variables)`
 *  order). Row 0 already exists on first open; every later index needs an
 *  "Add variable" click first. */
async function fillVariableRow(modal: Locator, index: number, name: string, value: string) {
	if (index > 0) {
		await modal.getByRole('button', { name: 'Add variable' }).click();
	}
	await modal.getByLabel('Variable name').nth(index).fill(name);
	await modal.getByLabel('Variable value').nth(index).fill(value);
}

test.describe('InlineChipEditor browser interactions', () => {
	test.beforeEach(async ({ page }) => {
		test.setTimeout(120000);
		await loginAsOwner(page);
		const preset = await installAndSelectImagePreset(page);
		if (!preset) {
			test.skip(true, 'No native image preset available on this throwaway instance to render the prompt editor with.');
			return;
		}
		console.log(`[${JOURNEY}] selected preset ${preset.id} (${preset.name})`);
	});

	test('paste inserts plain text at the cursor and leaves the caret after it', async ({ page }) => {
		const editor = await segmentEditor(page);
		await clearEditor(page, editor);

		await editor.click();
		await page.keyboard.type('before ');

		// Synthetic paste event (not OS clipboard permissions): handlePaste reads
		// e.clipboardData directly and never touches the system clipboard, so this
		// exercises the exact same code path a real Ctrl+V would.
		await editor.evaluate((el) => {
			const dt = new DataTransfer();
			dt.setData('text/plain', 'PASTED');
			const evt = new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true });
			el.dispatchEvent(evt);
		});
		// Typing immediately after proves the caret was left AFTER the pasted
		// text (handlePaste's range.setStartAfter(textNode)), not before/inside it.
		await page.keyboard.type('X');

		await expect(editor).toHaveText('before PASTEDX');
		await page.waitForTimeout(BEAT);
		await screenshot(page, JOURNEY, 'paste-inserted-at-cursor');
	});

	test('Backspace immediately after a choice-group chip removes it as a unit', async ({ page }) => {
		const editor = await segmentEditor(page);
		await clearEditor(page, editor);

		await editor.click();
		await page.keyboard.type('a {red|blue}');
		// The closing '}' synchronously chipifies the group (chipifyNewTokens).
		await expect(editor.locator('.choice-group-container')).toHaveCount(1);
		await expect(editor.locator('.choice-group-chip')).toBeVisible();
		await page.waitForTimeout(BEAT);
		await screenshot(page, JOURNEY, 'group-chip-before-backspace');

		// Caret was restored to right after the newly-mounted chip (the
		// Range.setStartAfter boundary case — see the caret-offset
		// round-trip bug this boundary also exposes on the READ side).
		await page.keyboard.press('Backspace');

		await expect(editor.locator('.choice-group-container')).toHaveCount(0);
		await expect(editor).toHaveText('a ');
		await page.waitForTimeout(BEAT);
		await screenshot(page, JOURNEY, 'group-chip-after-backspace');
	});

	// GAP (documented, not endorsed): grep confirms neither InlineChipEditor nor
	// ChatChipInput has a 'Delete' branch in handleKeyDown — forward-delete of an
	// adjacent chip is entirely unhandled JS-side.
	//
	// FIRST DRAFT of this test put the caret in the TRAILING text node after
	// typing a character there and arrowing left by one — a real run showed
	// that lands INSIDE that text node (offset 0), not at the chip's boundary,
	// so Delete just ate the ordinary trailing character and never touched the
	// chip at all (confirmed via screenshot: chip and "a {red|blue}" both
	// intact, count stayed 1). That was a flawed test construction, not a
	// finding about the editor.
	//
	// Corrected construction: Home + two ArrowRight moves the caret from the
	// start of the LEADING text node ("a ") to its end — still an ordinary
	// intra-text-node move, not atomic-chip traversal — landing exactly at the
	// chip's leading boundary. Delete from there is the real test.
	test('Delete immediately before a choice-group chip (forward-delete gap, unhandled)', async ({ page }) => {
		const editor = await segmentEditor(page);
		await clearEditor(page, editor);

		await editor.click();
		await page.keyboard.type('a {red|blue}');
		await expect(editor.locator('.choice-group-container')).toHaveCount(1);
		await page.keyboard.press('Home');
		await page.keyboard.press('ArrowRight'); // past 'a'
		await page.keyboard.press('ArrowRight'); // past the space, now right before the chip
		await page.waitForTimeout(BEAT);
		await screenshot(page, JOURNEY, 'group-chip-before-delete');

		await page.keyboard.press('Delete');
		await page.waitForTimeout(BEAT);
		await screenshot(page, JOURNEY, 'group-chip-after-delete');

		// PREDICTION (corrected construction, not yet re-verified): forward-
		// delete immediately before the contenteditable="false" atomic span
		// removes it as a single unit, mirroring Backspace's native behavior
		// immediately after one.
		await expect(editor.locator('.choice-group-container')).toHaveCount(0);
		await expect(editor).toHaveText('a ');
	});

	test('the $ variable picker supports Arrow/Enter keyboard navigation', async ({ page }) => {
		const modal = await openVariableManager(page);
		await fillVariableRow(modal, 0, 'style', 'oil painting, warm light');
		await fillVariableRow(modal, 1, 'mood', 'dramatic lighting');
		await page.waitForTimeout(BEAT);
		await screenshot(page, JOURNEY, 'two-variables-defined');
		await modal.getByRole('button', { name: 'Done' }).click();
		await expect(modal).toBeHidden();

		const editor = await segmentEditor(page);
		await clearEditor(page, editor);
		await editor.click();
		await page.keyboard.type('$');

		const options = page.locator('[role="option"]');
		await expect(options).toHaveCount(2, { timeout: 5000 });
		await expect(options.nth(0)).toHaveAttribute('aria-selected', 'true');
		await expect(options.nth(1)).toHaveAttribute('aria-selected', 'false');
		await page.waitForTimeout(BEAT);
		await screenshot(page, JOURNEY, 'variable-picker-open');

		await page.keyboard.press('ArrowDown');
		await expect(options.nth(0)).toHaveAttribute('aria-selected', 'false');
		await expect(options.nth(1)).toHaveAttribute('aria-selected', 'true');
		await page.waitForTimeout(BEAT);
		await screenshot(page, JOURNEY, 'variable-picker-arrow-down');

		// One more ArrowDown wraps back to index 0 ((selectedIndex + 1) % length).
		await page.keyboard.press('ArrowDown');
		await expect(options.nth(0)).toHaveAttribute('aria-selected', 'true');

		// ArrowUp from index 0 wraps to the last index.
		await page.keyboard.press('ArrowUp');
		await expect(options.nth(1)).toHaveAttribute('aria-selected', 'true');

		const knownNames = ['style', 'mood'];
		const secondOptionText = (await options.nth(1).textContent()) ?? '';
		const expectedName = knownNames.find((n) => secondOptionText.includes(n));
		expect(expectedName, 'the currently-selected option should be one of the two defined variables').toBeTruthy();

		// Enter's insertion goes through parseValueToSegments' variable-token
		// pass immediately (unlike a hand-typed `{a|b}` group, which only
		// chipifies reactively on the closing '}' via handleInput) — the
		// literal "${name}" text never appears in the DOM at all, it renders
		// straight to a chip. Confirmed via a real run: asserting the raw
		// substring (the original version of this test) fails even on
		// success, because the chip's rendered text is "$name", no braces.
		await page.keyboard.press('Enter');
		await expect(editor.locator('.variable-usage-container')).toHaveCount(1);
		await expect(editor.locator('.variable-usage-chip')).toContainText(expectedName!);
		await page.waitForTimeout(BEAT);
		await screenshot(page, JOURNEY, 'variable-picker-enter-inserted');
	});

	test('a variable-usage chip mounts, remounts on a stale-props update, and unmounts on removal', async ({
		page
	}) => {
		const modal = await openVariableManager(page);
		await fillVariableRow(modal, 0, 'style', 'oil painting, warm light');
		await modal.getByRole('button', { name: 'Done' }).click();
		await expect(modal).toBeHidden();

		const editor = await segmentEditor(page);
		await clearEditor(page, editor);
		await editor.click();
		await page.keyboard.type('${style}');

		// mount(): the usage chip renders as soon as the closing '}' chipifies it.
		await expect(editor.locator('.variable-usage-container')).toHaveCount(1);
		const chipButton = editor.locator('.variable-usage-chip button').first();
		await expect(chipButton).toBeVisible();
		await page.waitForTimeout(BEAT);
		await screenshot(page, JOURNEY, 'variable-chip-mounted');

		await chipButton.click();
		// InlinePopoverChip renders its popover as a `role="dialog"` div carrying
		// `aria-label={popoverLabel}` ("Variable <name>") — there is no
		// `.variable-usage-popover` class anywhere in the component (confirmed by
		// grep). The stale class selector made this assertion time out even
		// though the popover was visibly open with the right value (seen in a
		// real run's failure screenshot).
		const popover = page.getByRole('dialog', { name: 'Variable style' });
		await expect(popover).toBeVisible();
		await expect(popover).toContainText('oil painting, warm light');
		await page.waitForTimeout(BEAT);
		await screenshot(page, JOURNEY, 'variable-chip-popover-original-value');
		await page.keyboard.press('Escape');
		await expect(popover).toBeHidden();

		// Remount-on-stale-props: editing the SAME variable's definition from the
		// Variable Manager round-trips a new `variables` prop back down into the
		// still-mounted editor. InlineChipEditor's stepVariablesHash reactive
		// block (see chipEditorHelpers.ts hashVariableRolls sibling logic) must
		// notice this and call remountAllVariableChips() — a stale mount would
		// keep showing the OLD value in its popover forever.
		const modal2 = await openVariableManager(page);
		await modal2.getByLabel('Variable value').nth(0).fill('watercolor, soft light');
		await modal2.getByRole('button', { name: 'Done' }).click();
		await expect(modal2).toBeHidden();

		await chipButton.click();
		await expect(popover).toBeVisible();
		await expect(popover).toContainText('watercolor, soft light');
		await page.waitForTimeout(BEAT);
		await screenshot(page, JOURNEY, 'variable-chip-popover-after-remount');
		await page.keyboard.press('Escape');
		await expect(popover).toBeHidden();

		// unmount(): removing the chip tears the mounted component down.
		await editor.locator('.variable-usage-chip').locator('button[title="Remove this usage"]').click();
		await expect(editor.locator('.variable-usage-container')).toHaveCount(0);
		await page.waitForTimeout(BEAT);
		await screenshot(page, JOURNEY, 'variable-chip-unmounted');
	});
});
