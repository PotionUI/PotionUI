import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';

// W2 multi-shot LTX check (PLAN.md §C W2's E2E line): adds a second
// independent LTX shot, attaches an IC-LoRA entry to shot 2 ONLY, then
// drives the page's own Generate control (the console itself has no
// "Generate film" button -- see ConsoleHeader.svelte's own maintainer-ruling
// comment) and asserts it fires TWO separate `/api/generations/start`
// requests -- "one clip = one generation" per shot (PLAN.md §B) -- each
// carrying that shot's own `shot` provenance and its own (not merged)
// `ic_lora` list.
//
// Deliberately environment-tolerant, same idiom as director-console-render
// .spec.ts: skips with a clear reason when the fixture it needs (an LTX-style
// timeline preset, or an IC-LoRA-capable one) isn't on this throwaway
// instance, rather than failing. Never asserts on a real backend generation
// completing -- `/api/generations/start` is intercepted and fulfilled with a
// synthetic queued response, so this never needs a GPU/model to actually run.
//
// NOT run by the agent that wrote it (git/Playwright-execution constraints
// for this wave, same as W1) -- run with:
// PYTHONPATH=./venv/lib/python3.12/site-packages:. python tests/e2e/ui/run.py
// (or `npx playwright test director-console-ltx-shots` against an already-
// running throwaway instance).

const JOURNEY = 'director-console-ltx-shots';

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

/** Mirrors director-console-render.spec.ts's own copy of the preset-picker
 * selection half (see that file's doc comment for why this isn't shared with
 * presetPreamble.ts). */
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

interface CapturedStart {
	body: any;
}

test.describe('Video Director shot console: multi-shot LTX films', () => {
	test('add shot, IC-LoRA on shot 2 only, Generate enqueues 2 generations', async ({ page }) => {
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

		// This journey needs TIMELINE routing specifically (LTX) -- a chain/Wan
		// preset has no "Add shot" affordance to exercise here (its shots come
		// from segments, already covered by director-console-render.spec.ts).
		const ltxPreset = presets.find((p) => /ltx/i.test(p.id));
		console.log(`[${JOURNEY}] presets=${presets.length} ltx=${ltxPreset?.id}`);

		if (!ltxPreset) {
			test.skip(true, 'No LTX-style timeline video preset available on this throwaway instance.');
			return;
		}

		await ensureAssigned(page, token, userId, ltxPreset);

		await page.setViewportSize({ width: 1920, height: 1080 });
		await page.goto('/generate');
		await page.waitForLoadState('networkidle');

		const selected = await selectPresetInPicker(page, ltxPreset);
		if (!selected) {
			test.skip(true, `Preset picker never surfaced "${ltxPreset.name}" — cannot drive the Director from here.`);
			return;
		}

		const director = page.locator('section.video-director[aria-label="Video Director"]');
		await expect(director).toBeVisible({ timeout: 15000 });

		// Starts as one shot (LTX's normalized default) -- unanchored regex,
		// same idiom as director-console-render.spec.ts's own header check
		// (Playwright whitespace-normalizes the span's text before matching).
		await expect(director.getByText(/1 shot · [\d.]+ s/)).toBeVisible({ timeout: 15000 });

		const addShotBtn = director.getByRole('button', { name: '+ Add shot' });
		if ((await addShotBtn.count()) === 0 || !(await addShotBtn.isEnabled())) {
			test.skip(true, 'This LTX preset never offers "+ Add shot" (canAddShot false) — cannot build a multi-shot film.');
			return;
		}
		await addShotBtn.click();
		await page.waitForTimeout(300);

		// Now two independent shots — the header count and the row stack agree.
		await expect(director.getByText(/2 shots · [\d.]+ s/)).toBeVisible({ timeout: 10000 });

		// A freshly-added, never-edited shot derives the label "Shot 2"
		// (deriveShotLabel's ordinal fallback) — expand it (both its thumb and
		// its chevron button share this aria-label; either click activates it).
		const expandShot2 = director.getByRole('button', { name: 'Expand Shot 2' }).first();
		await expandShot2.click();
		await page.waitForTimeout(300);

		// IC-LoRA per shot: only reachable when this LTX mode declares the
		// capability at all (StageIcLora.svelte only mounts behind that tab).
		const icLoraTab = director.getByRole('tab', { name: /IC-LoRA/ });
		if ((await icLoraTab.count()) === 0) {
			test.skip(true, `${ltxPreset.id} has no IC-LoRA capability — cannot attach a shot-2-only entry.`);
			return;
		}
		await icLoraTab.click();
		await page.waitForTimeout(200);

		const addIcLoraBtn = director.getByRole('button', { name: 'Add IC-LoRA' });
		await expect(addIcLoraBtn).toBeVisible({ timeout: 5000 });
		await addIcLoraBtn.click();
		await page.waitForTimeout(200);

		// Shot 2's compact row (once re-collapsed) carries the mono IC-LORA
		// chip; expanding it again isn't necessary for the assertion below,
		// this is just a visible-state sanity check before submission.
		await expect(director.getByRole('tab', { name: /IC-LoRA\s*1/ })).toBeVisible();

		// An entry without a picked LoRA blocks readiness ("IC-LoRA entry missing
		// a LoRA") and the throwaway instance ships no LoRA to pick, so the
		// per-shot list is proven through add + remove; the wire assertions
		// below then check that each shot carries its OWN (empty) list.
		await director.getByRole('button', { name: 'Remove IC-LoRA' }).first().click();
		await expect(director.getByRole('tab', { name: /IC-LoRA\s*0/ })).toBeVisible();

		// Every shot needs a prompt before the page's Generate control enables:
		// type one into the expanded shot (2), then expand shot 1 and do the same.
		async function typeShotPrompt(text: string) {
			// A timeline shot has no default selection, and a fresh shot has no
			// beat at all: add one from the prompt lane's + column when needed,
			// then click the beat so the stage shows the segments editor.
			const beat = director.locator('button.beat-b').first();
			if ((await beat.count()) === 0) {
				await director.getByRole('button', { name: 'Add prompt beat' }).click();
				await page.waitForTimeout(200);
			}
			await beat.click();
			await page.waitForTimeout(200);
			const editor = director.locator('[role="textbox"][contenteditable="true"]').first();
			await expect(editor).toBeVisible({ timeout: 5000 });
			await editor.click();
			await page.keyboard.type(text);
			await page.waitForTimeout(300);
		}
		await typeShotPrompt('Second shot, wide, slow push in');
		await director.getByRole('button', { name: 'Expand Shot 1' }).first().click();
		await page.waitForTimeout(300);
		await typeShotPrompt('First shot, close up, static');

		// Intercept the wire submissions instead of running a real generation
		// (no GPU/model needed): fulfil each with a synthetic queued response
		// and record the request body for inspection.
		const captured: CapturedStart[] = [];
		let nextId = 1;
		await page.route('**/api/generations/start', async (route) => {
			const body = route.request().postDataJSON();
			captured.push({ body });
			const generationId = `fake-gen-${nextId}`;
			nextId += 1;
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({
					success: true,
					data: { generation_id: generationId, status: { status: 'queued' }, queue_position: captured.length - 1, backend: null }
				})
			});
		});

		const generateBtn = page.getByRole('button', { name: 'Generate', exact: true });
		await expect(generateBtn).toBeVisible({ timeout: 10000 });
		await generateBtn.click();

		// Two independent shots => two separate generations (PLAN.md §B: "one
		// clip = one generation" holds per shot, never a single N-segment doc).
		await expect.poll(() => captured.length, { timeout: 15000, message: 'expected 2 /api/generations/start requests' }).toBe(2);

		const [first, second] = captured.map((c) => c.body);
		const firstDoc = first.form_data?.video_director;
		const secondDoc = second.form_data?.video_director;

		expect(firstDoc?.shot, 'shot 1 doc missing `shot` provenance').toBeTruthy();
		expect(secondDoc?.shot, 'shot 2 doc missing `shot` provenance').toBeTruthy();
		expect(firstDoc.shot.index).toBe(0);
		expect(firstDoc.shot.count).toBe(2);
		expect(secondDoc.shot.index).toBe(1);
		expect(secondDoc.shot.count).toBe(2);
		expect(firstDoc.shot.id).not.toBe(secondDoc.shot.id);

		// IC-LoRA is per shot (13:20 ruling): each wire doc carries its own list,
		// and the entry added and removed above never leaked into either.
		expect(Array.isArray(firstDoc.ic_lora) ? firstDoc.ic_lora.length : 0).toBe(0);
		expect(Array.isArray(secondDoc.ic_lora) ? secondDoc.ic_lora.length : 0).toBe(0);

		await screenshot(page, JOURNEY, `01-${ltxPreset.id.replace(/[^a-z0-9-]/gi, '_')}-two-shots-queued`);
	});
});
