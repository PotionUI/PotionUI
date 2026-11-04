import { test, expect, type Locator, type Page } from '@playwright/test';
import { screenshot } from './helpers';
import {
	api,
	brewStatus,
	headers,
	noHorizontalScroll,
	openElixir,
	prepare,
	problems,
	removeContracts,
	seedElixir,
	setKnobs,
	writeContracts
} from './elixirs-fixtures';

const JOURNEY = 'elixirs-brew';

async function headerShots(page: Page, editor: Locator, state: string) {
	for (const [width, height] of [[1440, 900], [1024, 768], [390, 844]]) {
		await page.setViewportSize({ width, height });
		const name = editor.getByLabel('Elixir name');
		await expect(name).toBeVisible();
		await expect(name).toHaveValue(/\S/);
		await expect(editor.locator('.elixir-header')).toHaveCount(1);
		await expect.poll(async () => (await name.boundingBox())?.width ?? 0).toBeGreaterThanOrEqual(width === 390 ? 120 : 160);
		await expect.poll(() => editor.locator('.elixir-header').evaluate((row) => {
			const out: string[] = [];
			for (const el of row.querySelectorAll('button, input, [role="status"], [role="group"]')) {
				const rect = el.getBoundingClientRect();
				if (rect.width <= 1 || rect.height <= 1) continue;
				if (rect.left < 0 || rect.right > window.innerWidth + 0.5) out.push(`${el.getAttribute('aria-label') || el.textContent?.trim()} ${Math.round(rect.left)}-${Math.round(rect.right)}`);
			}
			if (row.scrollWidth > row.clientWidth + 1) out.push('row overflows');
			return out;
		}), { timeout: 5000 }).toEqual([]);
		await screenshot(page, JOURNEY, `header-${state}-${width}`);
	}
	await page.setViewportSize({ width: 1440, height: 900 });
	await expect(editor.getByLabel('Elixir name')).toBeVisible();
	await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
	await expect.poll(() => editor.locator('.elixir-header').evaluate((row) => row.scrollWidth <= row.clientWidth + 1)).toBe(true);
}

async function noTinyText(root: Locator) {
	const smallest = await root.evaluate((element) =>
		Math.min(
			...[...element.querySelectorAll('*')]
				.filter((el) => el.childNodes.length && [...el.childNodes].some((node) => node.nodeType === 3 && node.textContent?.trim()))
				.map((el) => parseFloat(getComputedStyle(el).fontSize))
		)
	);
	expect(smallest).toBeGreaterThanOrEqual(12);
}

test.describe.configure({ mode: 'serial' });

test.beforeAll(() => {
	writeContracts();
});

test.afterAll(() => {
	removeContracts();
});

test('an automatic brew runs the photo, sends it into the edit and shows both results', async ({ page }) => {
	test.setTimeout(180000);
	await page.setViewportSize({ width: 1440, height: 900 });
	const { token, modelRef } = await prepare(page);
	const elixirId = await seedElixir(page, token, modelRef, 'automatic');

	const editor = await openElixir(page, elixirId);
	await expect(page.locator('.elixir-header')).toHaveCount(1);
	await expect(editor.getByRole('button', { name: 'Back to all elixirs' })).toHaveCount(0);
	await expect(problems(editor)).toContainText('0', { timeout: 30000 });
	await expect(editor.getByRole('article', { name: 'Potion photo, preset step' }).getByText('Not brewed yet')).toBeVisible();
	await screenshot(page, JOURNEY, 'idle-1440');
	await headerShots(page, editor, 'idle');
	await editor.locator('.elixir-header').getByRole('button', { name: 'Brew', exact: true }).click();
	await expect(brewStatus(editor)).toContainText(/Brewing|Starting/, { timeout: 15000 });
	const strip = editor.getByLabel('Brew progress');
	await expect(strip).toContainText('Brew 1');
	await expect(editor.getByRole('article', { name: 'Blue restyle, preset step' }).getByText('Waiting for Potion photo')).toBeVisible();
	await screenshot(page, JOURNEY, 'automatic-brewing-1440');
	await expect(strip).toContainText(/Brew 1 · \d\/2/);
	await headerShots(page, editor, 'brewing');
	await expect(brewStatus(editor)).toHaveText('Done', { timeout: 90000 });

	const photo = editor.getByRole('article', { name: 'Potion photo, preset step' });
	const restyleNode = editor.getByRole('article', { name: 'Blue restyle, preset step' });
	await expect(photo.getByText('Done · 1 image')).toBeVisible();
	await expect(restyleNode.getByText('Done · 1 image')).toBeVisible();
	await expect(strip).toContainText(/Brew 1 · 2\/2 · \d\d:\d\d/);
	await expect(restyleNode.locator('button.pick').first()).toContainText('Potion photo');
	await expect(editor.locator('[data-lane-node="photo"] [data-card-id="restyle"]')).toHaveCount(1);
	const restyleOutputs = editor.getByRole('list', { name: 'Blue restyle outputs' });
	await expect(restyleOutputs.getByRole('listitem')).toHaveCount(1);
	await expect(restyleOutputs.locator('img')).toHaveCount(1);
	await screenshot(page, JOURNEY, 'automatic-done-1440');
	await headerShots(page, editor, 'done');
	await restyleOutputs.getByRole('button', { name: /^Open .+\.png$/ }).click();
	const viewer = page.getByRole('dialog', { name: 'Generation Details' });
	await expect(viewer).toBeVisible({ timeout: 15000 });
	await expect(viewer.locator('img').first()).toBeVisible();
	await screenshot(page, JOURNEY, 'automatic-viewer-1440');
	await page.keyboard.press('Escape');
	await expect(viewer).toHaveCount(0);
	await expect(restyleOutputs.getByRole('button', { name: /^Open .+\.png$/ })).toBeFocused();
	const runs = await api(page, token, 'get', `/api/elixirs/${elixirId}/runs`);
	const detail = await api(page, token, 'get', `/api/elixirs/runs/${runs.runs[0].id}`);
	const restyle = detail.invocations.find((item: any) => item.node_id === 'restyle');
	expect(restyle.input_key).toMatch(/^history:/);
	expect(restyle.status).toBe('completed');
	await expect(editor.locator('.elixir-header').getByRole('button', { name: 'Brew again', exact: true })).toBeVisible();
	await expect(editor.getByTestId('elixir-dock')).toHaveCount(0);

	await page.setViewportSize({ width: 390, height: 844 });
	const cards = editor.locator('.lane-wrap');
	await expect(cards).toBeVisible();
	await expect(cards.getByText('Done · 1 image').first()).toBeVisible();
	await expect(cards.getByRole('list', { name: 'Blue restyle outputs' }).locator('img')).toHaveCount(1);
	await expect(editor.getByTestId('elixir-dock').getByRole('button', { name: 'Brew again', exact: true })).toBeVisible();
	await expect(editor.locator('.elixir-header').getByRole('button', { name: /^Brew( again)?$/ })).toHaveCount(0);
	await noTinyText(cards);
	await noHorizontalScroll(page);
	await screenshot(page, JOURNEY, 'automatic-done-390');
});

test('choosing outputs between presets happens on the cards and waits for Continue on the next step', async ({ page }) => {
	test.setTimeout(180000);
	await page.setViewportSize({ width: 1440, height: 900 });
	const { token, modelRef } = await prepare(page);
	const elixirId = await seedElixir(page, token, modelRef, 'review');

	const editor = await openElixir(page, elixirId);
	await expect(problems(editor)).toContainText('0', { timeout: 30000 });
	await editor.locator('.elixir-header').getByRole('button', { name: 'Brew', exact: true }).click();
	await expect(brewStatus(editor)).toHaveText('Waiting for your choice', { timeout: 90000 });

	const photo = editor.getByRole('article', { name: 'Potion photo, preset step' });
	await expect(editor.getByRole('complementary', { name: /settings$/ })).toHaveCount(0);
	await expect(photo.getByText('Waiting for your choice')).toBeVisible();
	await expect(photo.getByText('Pick for')).toBeVisible();
	await expect(photo.getByRole('group', { name: 'Which step you are picking for' }).getByRole('button', { name: 'Blue restyle 1/1' })).toHaveAttribute('aria-pressed', 'true');
	const tick = photo.getByRole('checkbox', { name: /^Send .+ to Blue restyle$/ });
	await expect(tick).toHaveAttribute('aria-checked', 'true');
	const restyle = editor.getByRole('article', { name: 'Blue restyle, preset step' });
	const continueButton = restyle.getByRole('button', { name: 'Continue' });
	await expect(restyle.getByText('Waiting for your choice')).toBeVisible();
	await expect(restyle.locator('.continue')).toContainText(/1\s+of\s+1\s+picked/);
	await expect(brewStatus(editor)).toContainText('Waiting for your choice');
	await screenshot(page, JOURNEY, 'review-choose-1440');
	await headerShots(page, editor, 'waiting');

	await tick.click();
	await expect(tick).toHaveAttribute('aria-checked', 'false');
	await expect(continueButton).toBeDisabled();
	await expect(restyle.locator('.continue')).toContainText(/0\s+of\s+1\s+picked/);
	await photo.getByRole('button', { name: 'Select all' }).click();
	await expect(continueButton).toBeEnabled();

	await photo.getByRole('list', { name: 'Potion photo outputs' }).getByRole('button', { name: /^Open .+\.png$/ }).click();
	const viewer = page.getByRole('dialog', { name: 'Generation Details' });
	await expect(viewer).toBeVisible({ timeout: 15000 });
	const sendTo = viewer.getByRole('switch', { name: 'Blue restyle' });
	await expect(sendTo).toBeChecked();
	await sendTo.uncheck();
	await expect(tick).toHaveAttribute('aria-checked', 'false');
	await sendTo.check();
	await expect(tick).toHaveAttribute('aria-checked', 'true');
	await page.keyboard.press('Escape');
	await expect(viewer).toHaveCount(0);

	await page.setViewportSize({ width: 390, height: 844 });
	await expect(photo.getByRole('checkbox', { name: /^Send .+ to Blue restyle$/ })).toHaveAttribute('aria-checked', 'true');
	await expect(continueButton).toBeEnabled();
	await expect(editor.getByRole('separator')).toHaveCount(0);
	await noHorizontalScroll(page);
	await screenshot(page, JOURNEY, 'review-choose-390');
	await page.setViewportSize({ width: 1440, height: 900 });

	await continueButton.click();
	await expect(continueButton).toHaveCount(0, { timeout: 15000 });
	await expect(tick).toHaveCount(0);
	await expect(brewStatus(editor)).toHaveText('Done', { timeout: 90000 });
	await expect(restyle.getByText('Done · 1 image')).toBeVisible();

	await page.setViewportSize({ width: 390, height: 844 });
	await screenshot(page, JOURNEY, 'review-done-390');
});

test('a brew can be stopped, failed work retried, and an older brew viewed', async ({ page }) => {
	test.setTimeout(240000);
	await page.setViewportSize({ width: 1440, height: 900 });
	const { token, modelRef } = await prepare(page);

	await setKnobs(page, token, { duration_seconds: 8.0 });
	const stoppedId = await seedElixir(page, token, modelRef, 'automatic', 'Stopped brew');
	const stopped = await openElixir(page, stoppedId);
	await expect(problems(stopped)).toContainText('0', { timeout: 30000 });
	await stopped.locator('.elixir-header').getByRole('button', { name: 'Brew', exact: true }).click();
	await expect(brewStatus(stopped)).toContainText(/Brewing|Starting/, { timeout: 15000 });
	await expect(stopped.getByRole('article', { name: 'Potion photo, preset step' }).getByRole('progressbar')).toBeVisible();
	await stopped.locator('.elixir-header').getByRole('button', { name: 'Stop', exact: true }).click();
	await expect(brewStatus(stopped)).toHaveText('Stopped', { timeout: 60000 });
	await screenshot(page, JOURNEY, 'stopped-1440');

	await setKnobs(page, token, { duration_seconds: 1.0, fail_kind: 'failed' });
	const failingId = await seedElixir(page, token, modelRef, 'automatic', 'Failing brew');
	const editor = await openElixir(page, failingId);
	await expect(problems(editor)).toContainText('0', { timeout: 30000 });
	await editor.locator('.elixir-header').getByRole('button', { name: 'Brew', exact: true }).click();
	await expect(brewStatus(editor)).toHaveText('Needs attention', { timeout: 90000 });
	const photo = editor.getByRole('article', { name: 'Potion photo, preset step' });
	await expect(photo.getByRole('alert')).toBeVisible();
	await expect(photo.getByText('Failed', { exact: true })).toBeVisible();
	await screenshot(page, JOURNEY, 'failed-1440');
	await setKnobs(page, token, { fail_kind: '' });
	await photo.getByRole('button', { name: 'Retry failed' }).click();
	await expect(brewStatus(editor)).toHaveText('Done', { timeout: 90000 });

	await editor.locator('.elixir-header').getByRole('button', { name: 'Brew again', exact: true }).click();
	await expect(editor.getByLabel('Brew progress')).toContainText('Brew 2', { timeout: 15000 });
	await expect(brewStatus(editor)).toHaveText('Done', { timeout: 90000 });
	await editor.getByRole('button', { name: /^Brews/ }).click();
	await page.locator('#elixir-brews').getByRole('button', { name: /Brew 1/ }).click();
	await expect(editor.getByText(/Viewing Brew 1/)).toBeVisible();
	await expect(photo.getByRole('button', { name: /^Run Potion photo/ })).toHaveCount(0);
	await screenshot(page, JOURNEY, 'older-brew-1440');
	await editor.getByRole('button', { name: 'Back to the latest brew' }).click();
	await expect(editor.getByText(/Viewing Brew/)).toHaveCount(0);
	await expect(editor.getByLabel('Brew progress')).toContainText('Brew 2');
});

async function makeClip(page: Page) {
	const base64 = await page.evaluate(async () => {
		const canvas = document.createElement('canvas');
		canvas.width = 160;
		canvas.height = 120;
		const context = canvas.getContext('2d')!;
		const stream = canvas.captureStream(24);
		const recorder = new MediaRecorder(stream, { mimeType: 'video/webm' });
		const chunks: Blob[] = [];
		recorder.ondataavailable = (event) => chunks.push(event.data);
		const stopped = new Promise((resolve) => (recorder.onstop = resolve));
		recorder.start(100);
		const started = performance.now();
		await new Promise<void>((resolve) => {
			const draw = () => {
				const t = (performance.now() - started) / 1500;
				context.fillStyle = `hsl(${Math.round(200 + t * 120)}, 60%, 40%)`;
				context.fillRect(0, 0, 160, 120);
				context.fillStyle = 'white';
				context.fillRect(10 + t * 120, 50, 20, 20);
				if (t < 1) requestAnimationFrame(draw);
				else resolve();
			};
			draw();
		});
		recorder.stop();
		await stopped;
		const buffer = await new Blob(chunks, { type: 'video/webm' }).arrayBuffer();
		let binary = '';
		for (const byte of new Uint8Array(buffer)) binary += String.fromCharCode(byte);
		return btoa(binary);
	});
	return Buffer.from(base64, 'base64');
}

async function makeStill(page: Page) {
	const base64 = await page.evaluate(() => {
		const canvas = document.createElement('canvas');
		canvas.width = 128;
		canvas.height = 128;
		const context = canvas.getContext('2d')!;
		const gradient = context.createLinearGradient(0, 0, 128, 128);
		gradient.addColorStop(0, 'rgb(180, 140, 80)');
		gradient.addColorStop(1, 'rgb(60, 40, 120)');
		context.fillStyle = gradient;
		context.fillRect(0, 0, 128, 128);
		return canvas.toDataURL('image/png').split(',')[1];
	});
	return Buffer.from(base64, 'base64');
}

async function uploadTo(page: Page, token: string, name: string, mimeType: string, buffer: Buffer, mediaType: string) {
	const upload = await page.request.post('/api/media/upload', { headers: headers(token), multipart: { file: { name, mimeType, buffer } } });
	expect(upload.ok(), `upload ${name} -> ${upload.status()}`).toBeTruthy();
	const listed = await api(page, token, 'get', `/api/media/uploads?media_type=${mediaType}&limit=50`);
	const item = listed.data.uploads.find((entry: any) => entry.original_filename === name);
	expect(item, `${name} is not in the library`).toBeTruthy();
	return { kind: 'library', item_id: item.id, media_type: mediaType, original_filename: name };
}

test('video files show their first frame and play while hovered', async ({ page }) => {
	test.setTimeout(120000);
	await page.setViewportSize({ width: 1440, height: 900 });
	const { token } = await prepare(page);
	const clip = await uploadTo(page, token, 'orbit-clip.webm', 'video/webm', await makeClip(page), 'video');
	const still = await uploadTo(page, token, 'label-still.png', 'image/png', await makeStill(page), 'image');
	const created = await api(page, token, 'post', '/api/elixirs', {
		name: 'Mixed media',
		run_mode: 'review',
		definition: {
			nodes: [
				{ id: 'stills', kind: 'media', title: 'Label stills', position: { x: 40, y: 80 }, media_type: 'image', assets: [still] },
				{ id: 'clips', kind: 'media', title: 'Orbit clips', position: { x: 440, y: 80 }, media_type: 'video', assets: [clip] }
			],
			edges: []
		}
	});

	const editor = await openElixir(page, created.id);
	const clips = editor.getByRole('list', { name: 'Orbit clips files' });
	const video = clips.locator('video');
	await expect(video).toHaveCount(1, { timeout: 30000 });
	await expect(video).toHaveAttribute('preload', 'metadata');
	await expect(video).toHaveAttribute('src', /\/api\/media\/uploads\/[^?]+#t=0\.1$/);
	await expect.poll(() => video.evaluate((element: HTMLVideoElement) => element.readyState), { timeout: 15000 }).toBeGreaterThan(0);
	expect(await video.evaluate((element: HTMLVideoElement) => element.paused)).toBe(true);
	await clips.getByRole('listitem').first().hover();
	await expect.poll(() => video.evaluate((element: HTMLVideoElement) => element.paused), { timeout: 10000 }).toBe(false);
	await screenshot(page, JOURNEY, 'media-tiles-1440');
	await page.mouse.move(5, 5);
	await expect.poll(() => video.evaluate((element: HTMLVideoElement) => element.paused)).toBe(true);
	await expect(editor.getByRole('list', { name: 'Label stills files' }).locator('img')).toHaveCount(1);

	await page.setViewportSize({ width: 390, height: 844 });
	await expect(editor.locator('.lane-wrap').locator('video')).toHaveCount(1);
	await noHorizontalScroll(page);
	await screenshot(page, JOURNEY, 'media-tiles-390');
});
