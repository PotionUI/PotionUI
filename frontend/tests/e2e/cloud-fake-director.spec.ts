import { test, expect, type Page } from '@playwright/test';
import { deflateSync } from 'node:zlib';
import { loginAsOwner, ownerToken, screenshot } from './helpers';

const JOURNEY = 'cloud-fake-director';
const BACKEND_NAME = 'E2E Fake Cloud';
const PRESET_NAME = 'Fake Video';
const DIRECTOR = 'Fake Director';
const DIRECTOR_START = 'Fake Director Start';
const DIRECTOR_TEXT = 'Fake Director Text';
const SLUGS = ['fake~fake~director-1', 'fake~fake~director-start-1', 'fake~fake~director-text-1'];
const BEAT = 400;

function crc32(buf: Buffer): number {
	let c = ~0;
	for (const b of buf) {
		c ^= b;
		for (let k = 0; k < 8; k++) c = (c >>> 1) ^ (0xedb88320 & -(c & 1));
	}
	return ~c >>> 0;
}

function chunk(type: string, data: Buffer): Buffer {
	const head = Buffer.alloc(4);
	head.writeUInt32BE(data.length);
	const body = Buffer.concat([Buffer.from(type), data]);
	const tail = Buffer.alloc(4);
	tail.writeUInt32BE(crc32(body));
	return Buffer.concat([head, body, tail]);
}

function fixturePng(width: number, height: number): Buffer {
	const rows: Buffer[] = [];
	for (let y = 0; y < height; y++) {
		const row = Buffer.alloc(1 + width * 3);
		for (let x = 0; x < width; x++) {
			row[1 + x * 3] = 40 + Math.floor((x * 150) / width);
			row[2 + x * 3] = 60 + Math.floor((y * 120) / height);
			row[3 + x * 3] = 150;
		}
		rows.push(row);
	}
	const ihdr = Buffer.alloc(13);
	ihdr.writeUInt32BE(width, 0);
	ihdr.writeUInt32BE(height, 4);
	ihdr[8] = 8;
	ihdr[9] = 2;
	return Buffer.concat([
		Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
		chunk('IHDR', ihdr),
		chunk('IDAT', deflateSync(Buffer.concat(rows))),
		chunk('IEND', Buffer.alloc(0))
	]);
}

async function apiGet(page: Page, url: string, token: string) {
	const res = await page.request.get(url, { headers: { Authorization: `Bearer ${token}` } });
	expect(res.ok(), `GET ${url} -> ${res.status()}`).toBeTruthy();
	return res.json();
}

async function backendId(page: Page, token: string): Promise<string> {
	const list = await apiGet(page, '/api/backends', token);
	const backend = (list.data as Array<{ id: string; name: string }>).find((b) => b.name === BACKEND_NAME);
	expect(backend, `backend "${BACKEND_NAME}" must be seeded`).toBeTruthy();
	return backend!.id;
}

async function setKnob(page: Page, token: string, id: string, knobs: Record<string, unknown>) {
	const current = await apiGet(page, `/api/backends/${id}`, token);
	const res = await page.request.put(`/api/backends/${id}`, {
		headers: { Authorization: `Bearer ${token}` },
		data: { ...current.data, ...knobs }
	});
	expect(res.ok(), `PUT backend -> ${res.status()} ${await res.text()}`).toBeTruthy();
}

async function latestGeneration(page: Page, token: string) {
	const list = await apiGet(page, '/api/admin/generations?limit=5&sort_by=created_at&sort_dir=desc', token);
	return (list.data.generations as Array<Record<string, any>>)[0];
}

const field = (page: Page, name: string) => page.locator(`[data-field-name="${name}"]`);
const director = (page: Page) => page.locator('section.video-director[aria-label="Video Director"]');
const limits = (page: Page) => page.locator('[data-model-limits]');
const generateButton = (page: Page) => page.getByRole('button', { name: 'Generate', exact: true });
const cancelButton = (page: Page) => page.getByRole('button', { name: 'Cancel generation' });
const isDirectorSubmit = (candidate: { method(): string; postData(): string | null }) =>
	candidate.method() === 'POST' && (candidate.postData() ?? '').includes('"video_director"');
const submittedFilm = (candidate: { postData(): string | null }) => JSON.parse(candidate.postData() ?? '{}').form_data.video_director;

async function openFakeVideo(page: Page, mode: 'txt2video' | 'img2video' = 'txt2video') {
	await page.addInitScript(() => localStorage.setItem('potionui-form-audience', 'advanced'));
	await page.goto('/generate');
	const choose = page.getByRole('button', { name: 'Choose a preset' });
	const needsPreset = await choose.waitFor({ state: 'visible', timeout: 10000 }).then(() => true, () => false);
	if (needsPreset) {
		await choose.click();
		await page.getByText(PRESET_NAME, { exact: true }).first().click();
		await page.getByRole('button', { name: /Use this preset|Keep selected/ }).click();
	}
	if (mode === 'img2video') {
		await page.getByTestId('preset-header-mode').getByRole('button').first().click();
		await page.getByRole('option', { name: /img2video|image.?to.?video/i }).first().click();
	}
	await expect(director(page)).toBeVisible({ timeout: 20000 });
	await expect(field(page, 'model')).toBeVisible({ timeout: 20000 });
}

async function pickModel(page: Page, label: string) {
	const swap = field(page, 'model').locator('button:visible', { hasText: 'Swap' }).first();
	if (await swap.isVisible().catch(() => false)) await swap.click();
	await field(page, 'model').locator('input').first().click();
	await page.getByText(label, { exact: true }).last().click();
	await expect(limits(page).locator('[data-model-name]')).toHaveText(label, { timeout: 15000 });
	await page.waitForTimeout(BEAT);
}

async function typeInShot(page: Page, text: string) {
	const root = director(page);
	const editor = root.locator('[role="textbox"]').first();
	const found = await editor.waitFor({ state: 'visible', timeout: 5000 }).then(() => true, () => false);
	if (!found) {
		const collapsed = root.locator('[role="button"][aria-label^="Expand "]').first();
		if (await collapsed.count()) {
			await collapsed.click();
			await page.waitForTimeout(250);
		}
	}
	if (!(await editor.isVisible().catch(() => false))) {
		const seen = (await root.innerText().catch(() => '')).replace(/\s+/g, ' ').slice(0, 1200);
		throw new Error(`The shot has no prompt editor. The Director shows: ${seen}`);
	}
	await editor.click();
	await page.keyboard.type(text);
	await page.waitForTimeout(BEAT);
}

async function setShotSeconds(page: Page, seconds: number) {
	const input = director(page).locator('input[aria-label="Shot duration in seconds"]');
	await input.fill(String(seconds));
	await input.press('Enter');
	await page.waitForTimeout(BEAT);
}

async function buildFilm(page: Page, prompts: string[], seconds: number) {
	for (let i = 1; i < prompts.length; i++) {
		await director(page).getByRole('button', { name: '+ Add shot' }).click();
		await page.waitForTimeout(150);
	}
	await typeInShot(page, prompts[0]);
	await setShotSeconds(page, seconds);
	for (let i = 1; i < prompts.length; i++) {
		const collapsed = director(page).locator('[role="button"][aria-label^="Expand "]');
		await (i === 1 ? collapsed.first() : collapsed.last()).click();
		await page.waitForTimeout(250);
		await typeInShot(page, prompts[i]);
		await setShotSeconds(page, seconds);
	}
}

async function runStates(page: Page): Promise<string[]> {
	return director(page).locator('[data-run-state]').evaluateAll((nodes) => nodes.map((node) => node.getAttribute('data-run-state') ?? ''));
}

async function noHorizontalOverflow(page: Page) {
	const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
	expect(overflow, 'page must not scroll sideways').toBeLessThanOrEqual(0);
}

async function playableVideos(page: Page): Promise<number> {
	return page.locator('video').evaluateAll(
		(videos) => videos.filter((video) => ((video as HTMLVideoElement).currentSrc || (video as HTMLVideoElement).src) && video.getBoundingClientRect().width > 150).length
	);
}

test.describe.configure({ mode: 'serial' });

test.afterAll(async ({ browser }, testInfo) => {
	const context = await browser.newContext({ baseURL: testInfo.project.use.baseURL });
	const page = await context.newPage();
	try {
		await loginAsOwner(page);
		const token = await ownerToken(page);
		const id = await backendId(page, token);
		await setKnob(page, token, id, { fail_from_shot: 0, duration_seconds: 2 });
		const res = await page.request.post(`/api/cloud/backends/${id}/catalog/disable`, {
			headers: { Authorization: `Bearer ${token}` },
			data: { slugs: SLUGS }
		});
		expect(res.ok(), `disable -> ${res.status()} ${await res.text()}`).toBeTruthy();
	} finally {
		await context.close();
	}
});

test.describe('Video Director for a cloud video preset', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	let token = '';
	let id = '';

	test('an admin enables the video models', async ({ page }) => {
		test.setTimeout(120000);
		await loginAsOwner(page);
		token = await ownerToken(page);
		id = await backendId(page, token);
		const res = await page.request.post(`/api/cloud/backends/${id}/catalog/enable`, {
			headers: { Authorization: `Bearer ${token}` },
			data: { slugs: SLUGS }
		});
		expect(res.ok(), `enable -> ${res.status()} ${await res.text()}`).toBeTruthy();
	});

	test('the editor appears and its limits follow the chosen model', async ({ page }) => {
		test.setTimeout(180000);
		await loginAsOwner(page);
		await openFakeVideo(page);
		await expect(limits(page)).toHaveCount(0);
		await screenshot(page, JOURNEY, 'editor-no-model-1440');

		await pickModel(page, DIRECTOR);
		await expect(limits(page)).toContainText('Starts from a picture');
		await expect(limits(page)).toContainText('No end picture');
		await expect(director(page).locator('[data-model-notices]')).toHaveCount(0);
		await expect(limits(page)).toContainText('Shots of 2, 4, 6 s');
		await expect(limits(page)).toContainText('Up to 4 shots');
		await screenshot(page, JOURNEY, 'limits-director-1440');

		await pickModel(page, DIRECTOR_TEXT);
		await expect(limits(page)).toContainText('No start picture');
		await expect(limits(page)).toContainText('Shots of 5 s');
		await expect(limits(page)).toContainText('Up to 6 shots');
		await expect(director(page).locator('.kf-anchor')).toHaveCount(0);

		await pickModel(page, DIRECTOR_START);
		await expect(limits(page)).toContainText('Starts from a picture');
		await expect(limits(page)).toContainText('Shots of 3, 5 s');
		await expect(director(page).locator('.kf-anchor')).toHaveCount(1);

		await page.getByTestId('preset-header-mode').getByRole('button').first().click();
		await page.getByRole('option', { name: /img2video|image.?to.?video/i }).first().click();
		await expect(director(page)).toBeVisible();
		await pickModel(page, DIRECTOR);
		await expect(limits(page)).toContainText('Ends on a picture');
		await expect(director(page).locator('.kf-anchor')).toHaveCount(2);
		await screenshot(page, JOURNEY, 'limits-director-img2video-1440');
	});

	test('what does not fit the model is explained and can be fixed', async ({ page }) => {
		test.setTimeout(180000);
		await loginAsOwner(page);
		await openFakeVideo(page);
		await pickModel(page, DIRECTOR_TEXT);

		for (let i = 0; i < 4; i++) {
			await director(page).getByRole('button', { name: '+ Add shot' }).click();
			await page.waitForTimeout(150);
		}
		await expect(director(page).getByText(/5 shots? · [\d.]+ s/)).toBeVisible({ timeout: 10000 });
		await expect(director(page).locator('[data-model-notices]')).toHaveCount(0);
		await setShotSeconds(page, 5);

		await pickModel(page, DIRECTOR);
		const notices = director(page).locator('[data-model-notices]');
		await expect(notices).toContainText('Fake Director makes up to 4 shots in one film. Your film has 5, so shot 5 would not be made.');
		await expect(notices).toContainText('Fake Director renders shots of 2, 4 and 6 s.');
		await expect(page.getByText('Change these, or pick another model, to generate.')).toBeVisible();
		await expect(page.getByText("Can't generate yet")).toBeVisible();
		await expect(generateButton(page)).toHaveCount(0);
		await noHorizontalOverflow(page);
		await screenshot(page, JOURNEY, 'does-not-fit-1440');

		await director(page).getByRole('button', { name: 'Use lengths it renders' }).click();
		await expect(notices).not.toContainText('renders shots of');
		await expect(notices).toContainText('makes up to 4 shots');
		await expect(director(page).getByRole('button', { name: 'Use lengths it renders' })).toHaveCount(0);
		await screenshot(page, JOURNEY, 'fixed-lengths-1440');
	});

	test('one shot from a start picture brews to a video', async ({ page }) => {
		test.setTimeout(240000);
		await loginAsOwner(page);
		token = await ownerToken(page);
		await openFakeVideo(page, 'img2video');
		await pickModel(page, DIRECTOR_START);

		await typeInShot(page, 'the lantern flickers in the wind');
		await setShotSeconds(page, 3);
		await director(page).locator('.kf-anchor').first().click();
		await director(page)
			.locator('input[type="file"]')
			.first()
			.setInputFiles({ name: 'start.png', mimeType: 'image/png', buffer: fixturePng(96, 64) });
		await expect(director(page).locator('.kf-anchor[style*="background-image"]').first()).toBeVisible({ timeout: 20000 });
		await expect(generateButton(page)).toBeEnabled({ timeout: 10000 });
		await expect(page.locator('[data-cost-note]')).toHaveCount(0);
		await screenshot(page, JOURNEY, 'single-shot-ready-1440');

		const request = page.waitForRequest((candidate) => candidate.method() === 'POST' && (candidate.postData() ?? '').includes('"video_director"'));
		await generateButton(page).click();
		const sent = JSON.parse((await request).postData() ?? '{}');
		const doc = sent.form_data.video_director;
		expect(doc.media.some((entry: { role: string }) => entry.role === 'first')).toBe(true);
		expect(doc.segments).toHaveLength(1);

		await expect.poll(async () => (await latestGeneration(page, token)).status, { timeout: 90000 }).toBe('completed');
		await expect(generateButton(page)).toBeVisible({ timeout: 30000 });
		await expect.poll(() => playableVideos(page), { timeout: 20000 }).toBeGreaterThan(0);
		await screenshot(page, JOURNEY, 'single-shot-result-1440');
	});

	test('three shots brew one after another into the stitched film', async ({ page }) => {
		test.setTimeout(300000);
		await loginAsOwner(page);
		token = await ownerToken(page);
		id = await backendId(page, token);
		await openFakeVideo(page);
		await pickModel(page, DIRECTOR);
		await buildFilm(page, ['a lighthouse at dusk', 'the lamp turns on', 'waves under the beam'], 2);

		const note = page.locator('[data-cost-note]');
		await expect(note).toHaveText('About $0.60 for 3 shots', { timeout: 15000 });
		await noHorizontalOverflow(page);
		await screenshot(page, JOURNEY, 'three-shots-ready-1440');

		await generateButton(page).click();
		await expect(cancelButton(page)).toBeVisible({ timeout: 15000 });
		await expect.poll(async () => (await runStates(page)).includes('generating'), { timeout: 60000 }).toBe(true);
		await screenshot(page, JOURNEY, 'three-shots-progress-1440');
		await expect.poll(async () => (await runStates(page)).join(','), { timeout: 180000 }).toBe('done,done,done');
		await expect.poll(async () => (await latestGeneration(page, token)).status, { timeout: 60000 }).toBe('completed');
		await expect(generateButton(page)).toBeVisible({ timeout: 30000 });
		await expect.poll(() => playableVideos(page), { timeout: 20000 }).toBeGreaterThan(0);
		await expect(director(page).locator('[data-run-message]')).toHaveCount(0);
		await screenshot(page, JOURNEY, 'three-shots-stitched-1440');
	});

	test('a shot that fails stops the run, says why, and retries from there', async ({ page }) => {
		test.setTimeout(300000);
		await loginAsOwner(page);
		token = await ownerToken(page);
		id = await backendId(page, token);
		await setKnob(page, token, id, { fail_from_shot: 2 });
		try {
			await openFakeVideo(page);
			await pickModel(page, DIRECTOR);
			await buildFilm(page, ['a harbour at night', 'a boat leaves', 'the boat disappears'], 2);

			const firstSubmit = page.waitForRequest(isDirectorSubmit);
			await generateButton(page).click();
			const shotIds: string[] = submittedFilm(await firstSubmit).segments.map((segment: { id: string }) => segment.id);
			expect(shotIds).toHaveLength(3);
			await expect.poll(async () => (await runStates(page)).join(','), { timeout: 180000 }).toBe('done,failed,failed');
			await expect(generateButton(page)).toBeVisible({ timeout: 30000 });
			const messages = director(page).locator('[data-run-message]');
			await expect(messages).toHaveCount(2);
			await expect(messages.last()).not.toBeEmpty();
			await expect(director(page).getByRole('button', { name: 'Retry from here' })).toHaveCount(2);
			await noHorizontalOverflow(page);
		await screenshot(page, JOURNEY, 'failed-shot-1440');

			await setKnob(page, token, id, { fail_from_shot: 0 });
			const retrySubmit = page.waitForRequest(isDirectorSubmit);
			await director(page).getByRole('button', { name: 'Retry from here' }).first().click();
			const retried = submittedFilm(await retrySubmit);
			expect(retried.render).toMatchObject({ scope: 'shots', shot_ids: shotIds.slice(1) });
			await expect.poll(async () => (await runStates(page)).join(','), { timeout: 180000 }).toBe('done,done,done');
			await expect(director(page).locator('[data-run-message]')).toHaveCount(0);
			await expect.poll(async () => (await latestGeneration(page, token)).status, { timeout: 60000 }).toBe('completed');
			const retryRun = await latestGeneration(page, token);
			const detail = await apiGet(page, `/api/admin/generations/${retryRun.id}`, token);
			expect(detail.data.cost.items, 'only the retried shots were paid for').toHaveLength(2);
			await screenshot(page, JOURNEY, 'retried-1440');
		} finally {
			await setKnob(page, token, id, { fail_from_shot: 0 });
		}
	});

	test('cancelling stops the run and the unfinished shots say so', async ({ page }) => {
		test.setTimeout(240000);
		await loginAsOwner(page);
		token = await ownerToken(page);
		id = await backendId(page, token);
		await setKnob(page, token, id, { duration_seconds: 120 });
		try {
			await openFakeVideo(page);
			await pickModel(page, DIRECTOR);
			await buildFilm(page, ['a slow tide', 'the tide turns'], 2);
			await generateButton(page).click();
			await expect(cancelButton(page)).toBeVisible({ timeout: 15000 });
			await expect.poll(async () => (await runStates(page)).includes('generating'), { timeout: 60000 }).toBe(true);
			await page.waitForTimeout(1500);
			await cancelButton(page).click();
			await expect(generateButton(page)).toBeVisible({ timeout: 30000 });
			await expect.poll(async () => (await runStates(page)).join(','), { timeout: 30000 }).toBe('failed,failed');
			await expect(director(page).locator('[data-run-message]').first()).toContainText(/stopped|cancel/i);
			await expect.poll(async () => (await latestGeneration(page, token)).status, { timeout: 30000 }).toBe('cancelled');
			await screenshot(page, JOURNEY, 'cancelled-1440');
		} finally {
			await setKnob(page, token, id, { duration_seconds: 2 });
		}
	});

	test('a model that cannot be priced says so', async ({ page }) => {
		test.setTimeout(120000);
		await loginAsOwner(page);
		await openFakeVideo(page);
		await pickModel(page, DIRECTOR_TEXT);
		await buildFilm(page, ['a quiet street', 'the street at dawn'], 5);
		await expect(page.locator('[data-cost-note]')).toHaveText('Price unknown for 2 shots', { timeout: 15000 });
		await screenshot(page, JOURNEY, 'price-unknown-1440');
	});
});

test.describe('Video Director for a cloud video preset on a phone', () => {
	test.use({ viewport: { width: 390, height: 844 } });

	test('the model limits and the film fit the screen', async ({ page }) => {
		test.setTimeout(180000);
		await loginAsOwner(page);
		await page.addInitScript(() => localStorage.setItem('potionui-form-audience', 'advanced'));
		await page.goto('/generate');
		await page.getByRole('button', { name: 'Open preset and session' }).click();
		const sheet = page.getByRole('dialog', { name: 'Preset and session' });
		await expect(sheet).toBeVisible({ timeout: 5000 });
		const trigger = sheet.locator('button[aria-haspopup="dialog"]', { hasText: 'Choose a preset' });
		const needsPreset = await trigger.waitFor({ state: 'visible', timeout: 8000 }).then(() => true, () => false);
		if (needsPreset) {
			await trigger.click();
			await page.locator('[role="listbox"][aria-label="Presets"]').getByText(PRESET_NAME, { exact: true }).click();
			await page.getByRole('button', { name: /Use this preset|Keep selected/ }).click();
		}
		await page.keyboard.press('Escape');
		await page.getByRole('button', { name: 'Settings', exact: true }).click();
		await expect(page.getByRole('dialog', { name: 'Settings' })).toBeVisible({ timeout: 5000 });
		await page.waitForTimeout(1200);
		await field(page, 'model').locator('input').first().click();
		await page.getByText(DIRECTOR, { exact: true }).last().click();
		await page.waitForTimeout(BEAT);
		await page.keyboard.press('Escape');

		await page.getByRole('button', { name: 'Prompt', exact: true }).click();
		await expect(director(page)).toBeVisible({ timeout: 10000 });
		await expect(limits(page)).toContainText(DIRECTOR);
		for (let i = 0; i < 3; i++) {
			await director(page).getByRole('button', { name: '+ Add shot' }).click();
			await page.waitForTimeout(150);
		}
		await setShotSeconds(page, 5);
		await expect(director(page).locator('[data-model-notices]')).toBeVisible();
		await noHorizontalOverflow(page);
		const box = (await limits(page).boundingBox())!;
		expect(box.x + box.width).toBeLessThanOrEqual(390);
		await screenshot(page, JOURNEY, 'limits-and-notice-390');
	});
});
