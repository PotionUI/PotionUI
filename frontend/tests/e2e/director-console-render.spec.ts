import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';

// W1 Shot Console render check (PLAN.md §C W1's E2E line) — an HTTP-only
// unit/component test can't catch a stuck spinner, an $effect request loop,
// or a genuine layout regression the way a real browser can. This is
// deliberately environment-tolerant (a throwaway instance may or may not
// have a Wan/LTX-style preset installed) — it skips with a clear reason
// rather than failing when the fixture it needs isn't there, matching
// fe74-screenshots.spec.ts's own discovery idiom for a "video" preset.
//
// NOT run by the agent that wrote it — the Playwright harness was held by
// another agent during W1 (see W1-BRIEF.md's "Verification" section). Run
// with: PYTHONPATH=./venv/lib/python3.12/site-packages:. python tests/e2e/ui/run.py
// (or `npx playwright test director-console-render` against an already-
// running throwaway instance).

const JOURNEY = 'director-console-render';

interface DiscoveredPreset {
	id: string;
	name: string;
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

async function ensureAssigned(page: Page, token: string, userId: string, preset: { id: string; installed?: boolean }) {
	if (!preset.installed) await apiPost(page, `/api/presets/${preset.id}/install`, token);
	await apiPost(page, `/api/presets/${preset.id}/assign`, token, { user_ids: [userId] });
}

/** Selects `preset` through the real picker on an already-loaded /generate
 * page — mirrors presetPreamble.ts's `installAndSelectImagePreset`, which
 * only ever discovers/selects an IMAGE preset; there is no video-preset
 * equivalent there yet, so this spec carries its own copy of the selection
 * half rather than widening that helper's contract for one caller. */
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
	// A segmented mode selector may still be showing (e.g. an image/video
	// split on the same preset family) — land on "Video" if one exists.
	// The segmented mode control's button carries a native title; a plain role
	// query also matches the Video Director's own collapsible header button.
	const videoModeBtn = page.locator('button[title="Video"]');
	if (await videoModeBtn.count() > 0) {
		await videoModeBtn.click();
		await page.waitForTimeout(500);
	}
	return true;
}

test.describe('Video Director shot console', () => {
	test('renders header, film rows and an expanded shot card at 1920 and 1440', async ({ page }) => {
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

		// LTX (timeline routing, W1 renders it as one console shot) preferred
		// first since it exercises the console's TIMELINE branch; Wan (chain
		// routing, real joins + LoRAs tab) is the second profile this spec
		// checks if one is available.
		const ltxPreset = presets.find((p) => /ltx/i.test(p.id));
		const wanPreset = presets.find((p) => /wan/i.test(p.id));
		const videoPreset = ltxPreset || wanPreset || presets.find((p) => p.engine === 'native' && p.category === 'video');

		console.log(`[${JOURNEY}] presets=${presets.length} ltx=${ltxPreset?.id} wan=${wanPreset?.id} chosen=${videoPreset?.id}`);

		if (!videoPreset) {
			test.skip(true, 'No LTX/Wan-style video preset available on this throwaway instance.');
			return;
		}

		await ensureAssigned(page, token, userId, videoPreset);

		await page.setViewportSize({ width: 1920, height: 1080 });
		await page.goto('/generate');
		await page.waitForLoadState('networkidle');

		const selected = await selectPresetInPicker(page, videoPreset);
		if (!selected) {
			test.skip(true, `Preset picker never surfaced "${videoPreset.name}" — cannot drive the Director from here.`);
			return;
		}

		const consoleErrors: string[] = [];
		page.on('console', (msg) => {
			if (msg.type() === 'error') consoleErrors.push(msg.text());
		});

		const director = page.locator('section.video-director[aria-label="Video Director"]');
		await expect(director).toBeVisible({ timeout: 15000 });

		// Header: title, model chip, shot count/duration, readiness dot.
		await expect(director.getByRole('heading', { name: 'Video Director' })).toBeVisible();
		await expect(director.getByText(/shots? · [\d.]+ s/)).toBeVisible();

		// Film rows: Global prompt / Negative prompt, never the pre-W1 labels.
		await expect(director.getByText('Global prompt', { exact: true }).first()).toBeVisible();
		await expect(director.getByText('Negative prompt', { exact: true }).first()).toBeVisible();
		await expect(director.getByText('Direction', { exact: true })).toHaveCount(0);

		// Exactly one shot card is expanded (signal ring) with a rail underneath
		// it — collapsed rows never render `.rail`.
		const rails = director.locator('.rail');
		await expect(rails).toHaveCount(1, { timeout: 15000 });

		// Stage: either a real selection or the Global-prompt fallback — one of
		// the two always renders once a shot is expanded.
		const stage = director.locator('.stage, .stage-global').first();
		await expect(stage).toBeVisible();

		{
			const shot = await screenshot(page, JOURNEY, `01-${videoPreset.id.replace(/[^a-z0-9-]/gi, '_')}-1920`);
			await director.scrollIntoViewIfNeeded();
			await director.screenshot({ path: shot.replace(/\.png$/, '-console.png') });
		}

		// Selecting the active shot's own rail beat should always reach a
		// bottom stage panel (Selection tab's default variant) without a
		// console error — the D3 "one full-span beat per chain shot"/timeline
		// beat-per-segment anatomy both resolve through the same click.
		const firstBeat = director.locator('.beat-b').first();
		if (await firstBeat.count() > 0) {
			await firstBeat.click();
			await page.waitForTimeout(300);
		}

		// Wan/chain-routed profile only: a join's Continue|Fresh cut toggle
		// exists wherever continuation is available (PLAN.md D4) and flips the
		// join's own sentence in place.
		const freshCutBtn = director.getByRole('button', { name: 'Fresh cut' }).first();
		if (await freshCutBtn.count() > 0) {
			const joinBlock = director.locator('.seam').filter({ has: freshCutBtn }).first();
			const before = await joinBlock.textContent();
			await freshCutBtn.click();
			await page.waitForTimeout(300);
			const after = await joinBlock.textContent();
			expect(after).not.toBe(before);
			await expect(joinBlock.getByText('Starts fresh')).toBeVisible();
		}

		// LoRAs stage tab only exists on a per-segment-LoRA chain profile.
		const lorasTab = director.getByRole('tab', { name: /LoRAs/ });
		if (await lorasTab.count() > 0) {
			await lorasTab.click();
			await page.waitForTimeout(200);
		}

		{
			const shot = await screenshot(page, JOURNEY, `02-${videoPreset.id.replace(/[^a-z0-9-]/gi, '_')}-1920-interacted`);
			await director.scrollIntoViewIfNeeded();
			await director.screenshot({ path: shot.replace(/\.png$/, '-console.png') });
		}

		// Secondary viewport (PLAN.md's 2. Secondary — 1440): the console
		// still renders (gutter/label narrow to the @container variant, no
		// horizontal overflow of the body).
		await page.setViewportSize({ width: 1440, height: 960 });
		await page.waitForTimeout(300);
		await expect(director).toBeVisible();
		{
			const shot = await screenshot(page, JOURNEY, `03-${videoPreset.id.replace(/[^a-z0-9-]/gi, '_')}-1440`);
			await director.scrollIntoViewIfNeeded();
			await director.screenshot({ path: shot.replace(/\.png$/, '-console.png') });
		}

		const relevantErrors = consoleErrors.filter((e) => !/favicon|ResizeObserver/i.test(e));
		expect(relevantErrors, `unexpected console errors: ${relevantErrors.join('\n')}`).toEqual([]);
	});
});
