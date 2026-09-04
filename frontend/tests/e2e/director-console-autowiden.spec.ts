import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';

// Video Director auto-widen (PLAN.md §C W4) — an HTTP-only check can't see a
// real layout reflow, so this drives a real browser: three-pane layout, an
// LTX preset (Director-active) widens the prompts pane to
// `GenerationPanels.svelte`'s own max-width formula, and switching to a
// plain (non-Director) preset restores the pre-activation width. Deliberately
// environment-tolerant, matching director-console-render.spec.ts's own
// discovery idiom — it skips with a clear reason rather than failing when a
// fixture this needs isn't on the throwaway instance.
//
// NOT run by the agent that wrote it. Run with:
// PYTHONPATH=./venv/lib/python3.12/site-packages:. python tests/e2e/ui/run.py
// (or `npx playwright test director-console-autowiden` against an
// already-running throwaway instance).

const JOURNEY = 'director-console-autowiden';

// Mirrors GenerationPanels.svelte's own constants — this is a black-box
// check of the rendered layout, not a re-import of the component's
// internals, so the formula is duplicated deliberately.
// GenerationPanels bounds the prompts pane with `max-width: calc(100% - form - 328px)`
// (workbench minimum 320 + 8px of handle/gutter), which is 4px tighter than the
// drag clamp — the rendered maximum follows the CSS bound.
const WORKBENCH_BOUND_WIDTH = 328;
const MAX_WIDTH_TOLERANCE_PX = 2;

interface DiscoveredPreset {
	id: string;
	name: string;
	installed?: boolean;
}

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

async function ensureAssigned(page: Page, token: string, userId: string, preset: DiscoveredPreset) {
	if (!preset.installed) await apiPost(page, `/api/presets/${preset.id}/install`, token);
	await apiPost(page, `/api/presets/${preset.id}/assign`, token, { user_ids: [userId] });
}

/** Copied from director-console-render.spec.ts (same header note there on why
 * this isn't shared with presetPreamble.ts's image-only picker helper): picks
 * `preset` through the real picker on an already-loaded /generate page, and
 * lands on the "Video" mode if a segmented image/video split shows up. */
async function selectPresetInPicker(page: Page, preset: DiscoveredPreset): Promise<boolean> {
	const pickerTrigger = page.locator('button[aria-haspopup="dialog"]').first();
	await pickerTrigger.click();
	const presetList = page.getByRole('listbox', { name: 'Presets' });
	const opened = await presetList.isVisible({ timeout: 15000 }).catch(() => false);
	if (!opened) return false;
	const entry = presetList.getByText(preset.name, { exact: true });
	if ((await entry.count()) === 0) return false;
	await entry.click();
	await page.getByRole('button', { name: /Use this preset|Keep selected/ }).click();
	await page.waitForTimeout(1500);
	const videoModeBtn = page.locator('button[title="Video"]');
	if ((await videoModeBtn.count()) > 0) {
		await videoModeBtn.click();
		await page.waitForTimeout(500);
	}
	return true;
}

/** Switches the active tab to three-pane layout via the tabs-row "…" overflow
 * menu's view-layout picker (`TabsOverflowMenu` -> `GenerationLayoutPicker`).
 * A no-op (returns true immediately) if the tab is already three-pane. */
async function switchToThreePaneLayout(page: Page): Promise<void> {
	const promptsPane = page.getByTestId('prompts-pane');
	if (await promptsPane.isVisible({ timeout: 500 }).catch(() => false)) return;
	await page.getByRole('button', { name: 'More view options' }).click();
	await page.getByRole('option', { name: /Three panes/ }).click();
	await expect(promptsPane).toBeVisible({ timeout: 10000 });
}

/** Reads the live layout geometry `GenerationPanels.svelte` computes from:
 * the panels root's right edge and the prompts pane's own bounding box. */
async function readPromptPaneGeometry(page: Page) {
	return page.evaluate(() => {
		const root = document.querySelector('[data-testid="generation-panels-root"]');
		const pane = document.querySelector('[data-testid="prompts-pane"]');
		if (!root || !pane) return null;
		const rootRect = root.getBoundingClientRect();
		const paneRect = pane.getBoundingClientRect();
		return { panelRight: rootRect.right, promptLeft: paneRect.left, promptWidth: paneRect.width };
	});
}

test.describe('Video Director prompts-pane auto-widen', () => {
	test('widens to the max on activation at 1920, restores on deactivation', async ({ page }) => {
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

		// LTX (timeline routing) is the profile PLAN.md names for this spec —
		// director-console-render.spec.ts's own preference order for the same
		// reason (exercises the TIMELINE branch).
		const ltxPreset = presets.find((p) => /ltx/i.test(p.id));
		const imagePreset =
			presets.find((p) => /sdxl/i.test(p.id)) ||
			presets.find((p) => p.engine === 'native' && p.category === 'image');

		console.log(`[${JOURNEY}] presets=${presets.length} ltx=${ltxPreset?.id} image=${imagePreset?.id}`);

		if (!ltxPreset || !imagePreset) {
			test.skip(true, 'No LTX video preset and/or plain image preset available on this throwaway instance.');
			return;
		}

		await ensureAssigned(page, token, userId, ltxPreset);
		await ensureAssigned(page, token, userId, imagePreset);

		await page.setViewportSize({ width: 1920, height: 1080 });
		await page.goto('/generate');
		await page.waitForLoadState('networkidle');

		// Land on the plain (non-Director) preset first and switch to
		// three-pane layout — this is the "before" width the widen must
		// stash and the deactivation must restore back to.
		const selectedImage = await selectPresetInPicker(page, imagePreset);
		if (!selectedImage) {
			test.skip(true, `Preset picker never surfaced "${imagePreset.name}".`);
			return;
		}
		await switchToThreePaneLayout(page);

		const before = await readPromptPaneGeometry(page);
		expect(before, 'prompts pane must be measurable in three-pane layout').not.toBeNull();

		// Activate the Director by switching to the LTX preset.
		const selectedLtx = await selectPresetInPicker(page, ltxPreset);
		if (!selectedLtx) {
			test.skip(true, `Preset picker never surfaced "${ltxPreset.name}".`);
			return;
		}
		const director = page.locator('section.video-director[aria-label="Video Director"]');
		await expect(director).toBeVisible({ timeout: 15000 });
		// The widen runs after a DOM tick following activation; give it a beat.
		await page.waitForTimeout(500);

		const widened = await readPromptPaneGeometry(page);
		expect(widened, 'prompts pane must be measurable once the Director is active').not.toBeNull();
		const expectedMax = Math.max(
			0,
			widened!.panelRight - widened!.promptLeft - WORKBENCH_BOUND_WIDTH
		);
		expect(
			Math.abs(widened!.promptWidth - expectedMax),
			`widened prompts-pane width ${widened!.promptWidth} should equal the formula's maximum ${expectedMax}`
		).toBeLessThanOrEqual(MAX_WIDTH_TOLERANCE_PX);
		// And it actually grew from the pre-activation width — otherwise the
		// "before" width already happened to equal the maximum and this
		// assertion would pass vacuously.
		expect(widened!.promptWidth).toBeGreaterThan(before!.promptWidth);

		await screenshot(page, JOURNEY, `01-${ltxPreset.id.replace(/[^a-z0-9-]/gi, '_')}-widened-1920`);

		// Deactivate the Director by switching back to the plain preset.
		const reselectedImage = await selectPresetInPicker(page, imagePreset);
		if (!reselectedImage) {
			test.skip(true, `Preset picker never surfaced "${imagePreset.name}" on reselect.`);
			return;
		}
		await expect(director).toHaveCount(0, { timeout: 15000 });
		await page.waitForTimeout(300);

		const restored = await readPromptPaneGeometry(page);
		expect(restored, 'prompts pane must be measurable after restoring').not.toBeNull();
		expect(
			Math.abs(restored!.promptWidth - before!.promptWidth),
			`restored prompts-pane width ${restored!.promptWidth} should equal the pre-activation width ${before!.promptWidth}`
		).toBeLessThanOrEqual(MAX_WIDTH_TOLERANCE_PX);

		await screenshot(page, JOURNEY, `02-${imagePreset.id.replace(/[^a-z0-9-]/gi, '_')}-restored-1920`);
	});
});
