import { test, expect, type Page, type WebSocketRoute } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';

// W3 per-shot generation (PLAN.md §C W3's E2E line): checks two rows in a
// chain-style (Wan) film, drives the PAGE's own Generate control (the
// console itself never gains one — ConsoleHeader.svelte's maintainer-ruling
// comment), and proves the checked set actually scopes the submission: ONE
// `/api/generations/start` request, carrying `render: {scope: 'shots',
// shot_ids: [<both checked ids>]}` — never a request per checked shot for a
// chain profile (that's the timeline/LTX shape, covered by
// director-console-ltx-shots.spec.ts). A synthetic WebSocket frame stream
// then drives both checked rows through Queued -> Generating % -> Done.
//
// Same idioms as director-console-ltx-shots.spec.ts: `/api/generations/start`
// is intercepted (no GPU/model needed), and this skips with a clear reason
// when the fixture it needs isn't on the throwaway instance rather than
// failing.
//
// WebSocket simulation: `page.routeWebSocket` proxies the real `/ws/generation`
// connection (so auth/heartbeat/connection-status keeps working exactly as
// on a real page) while additionally letting this spec push synthetic
// `generation_status`/`gallery_update`/`generation_complete` frames for the
// FAKE generation id `/api/generations/start` returned — the real backend
// has never heard of that id, so it never emits anything for it on its own.
//
// NOT run by the agent that wrote it (git/Playwright-execution constraints,
// same as every other Director console spec) — run with:
// PYTHONPATH=./venv/lib/python3.12/site-packages:. python tests/e2e/ui/run.py
// (or `npx playwright test director-console-select-generate` against an
// already-running throwaway instance).

const JOURNEY = 'director-console-select-generate';

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

/** Types `text` into the currently-expanded chain shot's prompt editor — a
 * chain shot IS its prompt (one full-span beat, PLAN.md D3), so
 * ShotConsole.svelte's own default-selection effect already shows the
 * segments editor with nothing further to click, unlike the LTX/timeline
 * flow in director-console-ltx-shots.spec.ts (which has to add/click a beat
 * first). */
async function typeChainShotPrompt(director: ReturnType<Page['locator']>, page: Page, text: string) {
	const editor = director.locator('[role="textbox"][contenteditable="true"]').first();
	await expect(editor).toBeVisible({ timeout: 5000 });
	await editor.click();
	await page.keyboard.type(text);
	await page.waitForTimeout(300);
}

test.describe('Video Director shot console: per-shot generation', () => {
	test('checking two rows scopes the page Generate control to one render:{scope:"shots"} request, then drives both rows Queued -> Generating -> Done', async ({
		page
	}) => {
		await loginAsOwner(page);
		const token = await ownerToken(page);
		const me = await apiGet(page, '/api/auth/me', token);
		const userId = me.data.id as string;

		const list = await apiGet(page, '/api/presets?include_uninstalled=true', token);
		const presets = (list.data || []) as Array<{ id: string; name: string; engine?: string; category?: string; installed?: boolean }>;

		// This journey needs CHAIN routing specifically (Wan/H3 video) -- the
		// checked span compiles server-side into ONE request; a timeline (LTX)
		// film's checked shots are already independent docs, covered instead by
		// director-console-ltx-shots.spec.ts.
		const wanPreset = presets.find((p) => /wan/i.test(p.id));
		console.log(`[${JOURNEY}] presets=${presets.length} wan=${wanPreset?.id}`);

		if (!wanPreset) {
			test.skip(true, 'No Wan-style chain video preset available on this throwaway instance.');
			return;
		}

		await ensureAssigned(page, token, userId, wanPreset);

		// Proxy the real WebSocket connection so auth/heartbeat/subscription
		// bookkeeping keeps working exactly as on a real page; captured so
		// synthetic frames can be pushed into the client after the fake
		// generation id is known (below).
		let wsRoute: WebSocketRoute | null = null;
		await page.routeWebSocket('**/ws/generation**', (ws) => {
			wsRoute = ws;
			const server = ws.connectToServer();
			ws.onMessage((message) => server.send(message));
			server.onMessage((message) => ws.send(message));
		});

		await page.setViewportSize({ width: 1920, height: 1080 });
		await page.goto('/generate');
		await page.waitForLoadState('networkidle');

		const selected = await selectPresetInPicker(page, wanPreset);
		if (!selected) {
			test.skip(true, `Preset picker never surfaced "${wanPreset.name}" — cannot drive the Director from here.`);
			return;
		}

		const director = page.locator('section.video-director[aria-label="Video Director"]');
		await expect(director).toBeVisible({ timeout: 15000 });

		const addShotBtn = director.getByRole('button', { name: '+ Add shot' });
		if ((await addShotBtn.count()) === 0 || !(await addShotBtn.isEnabled())) {
			test.skip(true, `${wanPreset.id} never offers "+ Add shot" (canAddShot false) — cannot build a 2-shot film.`);
			return;
		}
		await addShotBtn.click();
		await page.waitForTimeout(300);
		await expect(director.getByText(/2 shots? · [\d.]+ s/)).toBeVisible({ timeout: 10000 });

		// Every segment needs a prompt before the page's Generate control
		// enables (validateDirector's chain branch) — shot 1 starts expanded.
		await typeChainShotPrompt(director, page, 'First shot, lanterns in the rain');
		await director.getByRole('button', { name: /^Expand / }).first().click();
		await page.waitForTimeout(300);
		await typeChainShotPrompt(director, page, 'Second shot, steam fills the frame');

		// Every row carries its checkbox in stack order (01 then 02), whether
		// collapsed or expanded: check shot 2 then shot 1.
		const checkboxes = director.getByRole('button', { name: 'Select for generation' });
		await checkboxes.nth(1).click();
		await page.waitForTimeout(150);
		await checkboxes.nth(0).click();
		await page.waitForTimeout(150);

		await expect(director.getByText('2 selected')).toBeVisible({ timeout: 5000 });

		const captured: any[] = [];
		const FAKE_GENERATION_ID = 'fake-gen-shots-span';
		await page.route('**/api/generations/start', async (route) => {
			captured.push(route.request().postDataJSON());
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({
					success: true,
					data: { generation_id: FAKE_GENERATION_ID, status: { status: 'queued' }, queue_position: 0, backend: null }
				})
			});
		});

		const generateBtn = page.getByRole('button', { name: 'Generate', exact: true });
		await expect(generateBtn).toBeVisible({ timeout: 10000 });
		await generateBtn.click();

		await expect.poll(() => captured.length, { timeout: 15000, message: 'expected exactly 1 /api/generations/start request' }).toBe(1);

		const doc = captured[0].form_data?.video_director;
		expect(doc?.render, 'chain submission missing `render` (checked-scope) key').toBeTruthy();
		expect(doc.render.scope).toBe('shots');
		expect(doc.render.shot_ids).toHaveLength(2);
		// The FULL segment list still rides along -- compile_shot_plan needs the
		// whole film's context server-side (never filter `segments` client-side,
		// PLAN.md's own bite-check).
		expect(Array.isArray(doc.segments) ? doc.segments.length : 0).toBe(2);

		// Both checked rows queue immediately.
		await expect(director.getByText('Queued')).toHaveCount(2, { timeout: 10000 });

		expect(wsRoute, 'WebSocket route never connected').toBeTruthy();
		wsRoute!.send(
			JSON.stringify({ type: 'generation_status', generation_id: FAKE_GENERATION_ID, status: 'running', progress: 0.4 })
		);
		await expect(director.getByText('Generating 40%')).toHaveCount(2, { timeout: 10000 });

		wsRoute!.send(
			JSON.stringify({
				type: 'gallery_update',
				generation_id: FAKE_GENERATION_ID,
				videos: [{ path: '/api/media/generations/fake-gen-shots-span/0.mp4' }],
				video_urls_list: [{ path: '/api/media/generations/fake-gen-shots-span/0.mp4' }]
			})
		);
		wsRoute!.send(JSON.stringify({ type: 'generation_complete', data: { id: FAKE_GENERATION_ID } }));

		await expect(director.getByText(/Done · \d{2}:\d{2}/)).toHaveCount(2, { timeout: 10000 });

		await screenshot(page, JOURNEY, `01-${wanPreset.id.replace(/[^a-z0-9-]/gi, '_')}-both-shots-done`);
	});
});
