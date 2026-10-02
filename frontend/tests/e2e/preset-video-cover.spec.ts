import { test, expect, type Page } from '@playwright/test';
import { copyFileSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { loginAsOwner, ownerToken } from './helpers';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '../../..');
const COVER_FIXTURE = resolve(__dirname, 'fixtures/preset-cover.webm');

const PRESET_NAME = 'E2E Video Cover Fixture';

function writeFixturePreset(presetDir: string, familyId: string) {
	mkdirSync(resolve(presetDir, 'modes/clip'), { recursive: true });
	mkdirSync(resolve(presetDir, 'public'), { recursive: true });
	copyFileSync(COVER_FIXTURE, resolve(presetDir, 'public/cover.webm'));
	writeFileSync(
		resolve(presetDir, 'preset.yml'),
		`schema: 1
id: "${familyId}"
name: "${PRESET_NAME}"
category: "video"
version: "1.0.0"
engine: "native"
tags: ["e2e"]

media:
  cover: "public/cover.webm"

modes:
  - clip
`
	);
	writeFileSync(
		resolve(presetDir, 'modes/clip/pipeline.yml'),
		`pipeline:
  - name: "gallery"
    id: "gallery"
    enabled: true
    configuration:
      mode: "save"
`
	);
	writeFileSync(resolve(presetDir, 'modes/clip/form.yml'), `name: "clip"\nfields: []\n`);
}

async function openFixtureCard(page: Page, familyId: string) {
	await loginAsOwner(page);
	const token = await ownerToken(page);
	const res = await page.request.post(`/api/presets/${familyId}/reload`, {
		headers: { Authorization: `Bearer ${token}` },
		data: {}
	});
	expect(res.ok()).toBeTruthy();

	await page.goto('/admin?tab=presets');
	await page.getByPlaceholder('Search presets by name, id, or tag…').fill(PRESET_NAME);
	const card = page.locator('[data-preset-card]').filter({ hasText: PRESET_NAME });
	await expect(card).toBeVisible({ timeout: 10000 });
	return card;
}

const isPaused = (video: ReturnType<Page['locator']>) =>
	video.evaluate((el: HTMLVideoElement) => el.paused);

async function withFixture(run: (familyId: string) => Promise<void>) {
	const familyId = `e2e-video-cover-${Date.now()}`;
	const presetDir = resolve(REPO_ROOT, 'content/presets/local', familyId);
	try {
		writeFixturePreset(presetDir, familyId);
		await run(familyId);
	} finally {
		rmSync(presetDir, { recursive: true, force: true, maxRetries: 20, retryDelay: 250 });
	}
}

test.describe('desktop', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('video cover shows a still, plays on hover and pauses on leave', async ({ page }) => {
		await withFixture(async (familyId) => {
			const card = await openFixtureCard(page, familyId);
			const video = card.locator('video');
			await expect(video).toHaveCount(1);
			await expect(video).toHaveJSProperty('muted', true);
			await expect(video).toHaveAttribute('poster', /cover\.webm\?size=small/);
			await expect.poll(() => isPaused(video)).toBe(true);

			const scaleOf = () => video.evaluate((el) => getComputedStyle(el).transform);
			expect(await scaleOf()).toBe('none');
			await card.hover();
			await expect.poll(() => isPaused(video), { timeout: 10000 }).toBe(false);
			await expect.poll(scaleOf).toMatch(/^matrix\(1\.0\d*,/);

			await page.mouse.move(2, 2);
			await expect.poll(() => isPaused(video), { timeout: 10000 }).toBe(true);
		});
	});

	test('reduced motion keeps the still and never plays', async ({ page }) => {
		await page.emulateMedia({ reducedMotion: 'reduce' });
		await withFixture(async (familyId) => {
			const card = await openFixtureCard(page, familyId);
			const video = card.locator('video');
			await expect
				.poll(async () => (await card.locator('img').count()) + (await video.count()), { timeout: 10000 })
				.toBe(1);
			await card.hover();
			if ((await video.count()) === 1) {
				await expect.poll(() => isPaused(video)).toBe(true);
				expect(await video.evaluate((el: HTMLVideoElement) => el.currentTime)).toBe(0);
			}
			const media = card.locator('img, video').first();
			await expect.poll(() => media.evaluate((el) => getComputedStyle(el).transform)).toBe('none');
		});
	});
});

test.describe('phone', () => {
	test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

	test('video cover plays while at least half in view', async ({ page }) => {
		await withFixture(async (familyId) => {
			const card = await openFixtureCard(page, familyId);
			const video = card.locator('video');
			await card.scrollIntoViewIfNeeded();
			await expect.poll(() => isPaused(video), { timeout: 10000 }).toBe(false);
		});
	});
});
