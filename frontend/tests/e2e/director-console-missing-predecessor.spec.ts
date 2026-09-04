import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';

// W3 missing-predecessor join (PLAN.md §C W3's E2E line): a chain (Wan) film
// whose second shot continues from the first defaults to a NATIVE
// CONTINUATION join (no runs map: the first shot has never been rendered).
// Checking only the SECOND (downstream) shot is what actually surfaces the
// warning — an unchecked continuation still renders as the normal
// Continue/Fresh-cut toggle (ConsoleJoin.kind stays 'native', never
// 'missing', when nothing checked would hit it) — then proves its two
// actions: "Generate previous + this shot" submits the contiguous span
// (both shots) as ONE render:{scope:'shots'} request, and "Convert to fresh
// cut" flips the join in place (never silently, and never on its own —
// PLAN.md: "never silently convert").
//
// Same idioms as director-console-select-generate.spec.ts: `/api/generations
// /start` is intercepted (no GPU/model needed), and this skips with a clear
// reason when the fixture it needs isn't on the throwaway instance.
//
// NOT run by the agent that wrote it — run with:
// PYTHONPATH=./venv/lib/python3.12/site-packages:. python tests/e2e/ui/run.py
// (or `npx playwright test director-console-missing-predecessor` against an
// already-running throwaway instance).

const JOURNEY = 'director-console-missing-predecessor';

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

async function typeChainShotPrompt(director: ReturnType<Page['locator']>, page: Page, text: string) {
	const editor = director.locator('[role="textbox"][contenteditable="true"]').first();
	await expect(editor).toBeVisible({ timeout: 5000 });
	await editor.click();
	await page.keyboard.type(text);
	await page.waitForTimeout(300);
}

/** Discovers a Wan-style chain preset, builds a 2-shot film with both shots
 * prompted, and returns the `director` locator with shot 1 left collapsed
 * (unchecked) and shot 2 expanded (unchecked) — the shared setup every test
 * below starts from. Calls `test.skip` and returns null when this
 * throwaway instance's fixtures don't support the journey. */
async function setUpTwoShotFilm(page: Page): Promise<ReturnType<Page['locator']> | null> {
	await loginAsOwner(page);
	const token = await ownerToken(page);
	const me = await apiGet(page, '/api/auth/me', token);
	const userId = me.data.id as string;

	const list = await apiGet(page, '/api/presets?include_uninstalled=true', token);
	const presets = (list.data || []) as Array<{ id: string; name: string; engine?: string; category?: string; installed?: boolean }>;

	const wanPreset = presets.find((p) => /wan/i.test(p.id));
	console.log(`[${JOURNEY}] presets=${presets.length} wan=${wanPreset?.id}`);
	if (!wanPreset) {
		test.skip(true, 'No Wan-style chain video preset available on this throwaway instance.');
		return null;
	}

	await ensureAssigned(page, token, userId, wanPreset);

	await page.setViewportSize({ width: 1920, height: 1080 });
	await page.goto('/generate');
	await page.waitForLoadState('networkidle');

	const selected = await selectPresetInPicker(page, wanPreset);
	if (!selected) {
		test.skip(true, `Preset picker never surfaced "${wanPreset.name}" — cannot drive the Director from here.`);
		return null;
	}

	const director = page.locator('section.video-director[aria-label="Video Director"]');
	await expect(director).toBeVisible({ timeout: 15000 });

	const addShotBtn = director.getByRole('button', { name: '+ Add shot' });
	if ((await addShotBtn.count()) === 0 || !(await addShotBtn.isEnabled())) {
		test.skip(true, `${wanPreset.id} never offers "+ Add shot" (canAddShot false) — cannot build a 2-shot film.`);
		return null;
	}
	await addShotBtn.click();
	await page.waitForTimeout(300);
	await expect(director.getByText(/2 shots? · [\d.]+ s/)).toBeVisible({ timeout: 10000 });

	await typeChainShotPrompt(director, page, 'First shot, lanterns in the rain');
	await director.getByRole('button', { name: /^Expand / }).first().click();
	await page.waitForTimeout(300);
	await typeChainShotPrompt(director, page, 'Second shot, steam fills the frame');

	// A freshly-added chain shot defaults to a NATIVE CONTINUATION join (no
	// override, and Wan's continuation capability isn't disabled) -- if this
	// preset's own defaults differ, there's nothing to test here.
	const joinKind = director.locator('.seam, .join-block, [class*="join"]').filter({ hasText: /CONTINUATION|CONTINUES/i });
	const nativeJoinVisible = await director.getByText(/NATIVE CONTINUATION|CONTINUES/i).isVisible({ timeout: 5000 }).catch(() => false);
	if (!nativeJoinVisible) {
		test.skip(true, `${wanPreset.id}'s freshly-added second shot doesn't default to a continuation join — nothing to break here.`);
		return null;
	}
	void joinKind;

	return director;
}

test.describe('Video Director shot console: missing-predecessor join', () => {
	test('checking only the downstream shot surfaces the warning; "Generate previous + this shot" submits the span as one request', async ({
		page
	}) => {
		const director = await setUpTwoShotFilm(page);
		if (!director) return;

		// Shot 2 is expanded (ShotCard) -- check ONLY it, leaving shot 1
		// unchecked. Nothing has ever rendered, so shot 1 (the predecessor) has
		// no run: the join must now read as missing.
		await director.getByRole('button', { name: 'Select for generation' }).nth(1).click();
		await page.waitForTimeout(150);
		await expect(director.getByText('1 selected')).toBeVisible({ timeout: 5000 });

		await expect(director.getByText('MISSING PREDECESSOR')).toBeVisible({ timeout: 10000 });
		await expect(director.getByText('Shot 01 has no output yet.')).toBeVisible();

		const captured: any[] = [];
		await page.route('**/api/generations/start', async (route) => {
			captured.push(route.request().postDataJSON());
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({
					success: true,
					data: { generation_id: 'fake-gen-span', status: { status: 'queued' }, queue_position: 0, backend: null }
				})
			});
		});

		await director.getByRole('button', { name: 'Generate previous + this shot' }).click();

		await expect.poll(() => captured.length, { timeout: 15000, message: 'expected exactly 1 /api/generations/start request' }).toBe(1);
		const doc = captured[0].form_data?.video_director;
		expect(doc?.render?.scope).toBe('shots');
		// The span reaches back to shot 1 (the nearest fresh cut) -- exactly
		// "previous + this shot", never just the checked id alone.
		expect(doc.render.shot_ids).toHaveLength(2);

		await screenshot(page, JOURNEY, '01-generate-previous-and-this');
	});

	test('"Convert to fresh cut" flips the join without generating anything', async ({ page }) => {
		const director = await setUpTwoShotFilm(page);
		if (!director) return;

		await director.getByRole('button', { name: 'Select for generation' }).nth(1).click();
		await page.waitForTimeout(150);
		await expect(director.getByText('MISSING PREDECESSOR')).toBeVisible({ timeout: 10000 });

		let startRequests = 0;
		await page.route('**/api/generations/start', async (route) => {
			startRequests += 1;
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({ success: true, data: { generation_id: 'unused', status: { status: 'queued' }, queue_position: 0, backend: null } })
			});
		});

		await director.getByRole('button', { name: 'Convert to fresh cut' }).click();
		await page.waitForTimeout(300);

		// The join is now a hard cut -- no more warning, no toggle "Continue"
		// half stays active, and never a generation as a side effect of the
		// conversion itself (PLAN.md: "never silently convert" cuts both ways —
		// converting is explicit, but converting is ALSO not a generate action).
		await expect(director.getByText('MISSING PREDECESSOR')).toHaveCount(0);
		await expect(director.getByText('HARD CUT')).toBeVisible({ timeout: 5000 });
		expect(startRequests).toBe(0);

		await screenshot(page, JOURNEY, '02-converted-to-fresh-cut');
	});
});
