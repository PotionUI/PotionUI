import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken } from './helpers';

const TAB_ID = 'new-tab-stale-form-tab';
const TABS_STORAGE_KEY = 'potionui_tabs_state';
const SEED_FLAG = 'new-tab-stale-form-seeded';

async function firstUsablePreset(page: Page, token: string): Promise<{ id: string; mode: string }> {
	const headers = { Authorization: `Bearer ${token}` };
	const list = await page.request.get('/api/presets?include_uninstalled=true', { headers });
	const raw = (await list.json()).data;
	const presets = (Array.isArray(raw) ? raw : raw?.presets || []) as Array<{ id: string }>;
	for (const preset of presets) {
		const res = await page.request.get(`/api/presets/${preset.id}/modes`, { headers });
		const body = await res.json();
		const mode = body?.data?.default_mode || body?.data?.modes?.[0]?.name;
		if (mode) return { id: preset.id, mode };
	}
	test.skip(true, 'no installed preset with a mode on this instance');
	return { id: '', mode: '' };
}

test.describe('a new generation tab never shows another tab form', () => {
	test.beforeEach(async ({ page }) => {
		await loginAsOwner(page);
	});

	for (const size of [
		{ tag: '1440', width: 1440, height: 900 }
	]) {
		test(`the previous form is not on screen in any frame after the plus button at ${size.tag}`, async ({ page }) => {
			test.setTimeout(90000);
			await page.setViewportSize({ width: size.width, height: size.height });
			const preset = await firstUsablePreset(page, await ownerToken(page));

			await page.addInitScript(
				({ key, flag, tab }) => {
					try {
						if (sessionStorage.getItem(flag)) return;
						sessionStorage.setItem(flag, '1');
						localStorage.setItem(key, JSON.stringify({ tabs: [tab], activeTabId: tab.id }));
					} catch {}
				},
				{
					key: TABS_STORAGE_KEY,
					flag: SEED_FLAG,
					tab: {
						id: TAB_ID,
						name: 'First',
						selectedPreset: preset.id,
						selectedMode: preset.mode,
						selectedVariant: null,
						selectedSessionId: null,
						activeGenerationId: null,
						prompt: '',
						negativePrompt: '',
						promptSegments: [],
						negativePromptSegments: [],
						formData: {}
					}
				}
			);
			await page.goto('/generate');

			if (size.width < 768) {
				await expect(page.locator('.studio-dock')).toBeVisible({ timeout: 20000 });
				await page.getByRole('button', { name: 'Settings', exact: true }).click();
				await expect(page.getByRole('dialog', { name: 'Settings' }).locator('[data-field-name]').first()).toBeVisible({
					timeout: 30000
				});
				await page.getByRole('button', { name: 'Done', exact: true }).click();
			} else {
				await expect(page.locator('[data-field-name]').first()).toBeVisible({ timeout: 30000 });
			}

			await page.evaluate(() => {
				const w = window as unknown as { __visibleFieldFrames: number[]; __stopFrames: boolean; __clickFrame: number };
				w.__visibleFieldFrames = [];
				w.__stopFrames = false;
				w.__clickFrame = -1;
				document.addEventListener(
					'click',
					() => {
						if (w.__clickFrame < 0) w.__clickFrame = w.__visibleFieldFrames.length;
					},
					true
				);
				const tick = () => {
					const visible = Array.from(document.querySelectorAll<HTMLElement>('[data-field-name]')).filter(
						(el) => el.getClientRects().length > 0
					).length;
					w.__visibleFieldFrames.push(visible);
					if (!w.__stopFrames) requestAnimationFrame(tick);
				};
				requestAnimationFrame(tick);
			});

			await page.getByRole('button', { name: 'Add new tab' }).click();
			await expect(page.getByText('Generation 2').first()).toBeVisible();
			await page.waitForTimeout(4500);

			const { frames, clickFrame } = await page.evaluate(() => {
				const w = window as unknown as { __visibleFieldFrames: number[]; __stopFrames: boolean; __clickFrame: number };
				w.__stopFrames = true;
				return { frames: w.__visibleFieldFrames, clickFrame: w.__clickFrame };
			});
			expect(clickFrame).toBeGreaterThan(0);
			expect(frames.length - clickFrame).toBeGreaterThan(10);
			expect(frames[clickFrame - 1], 'the first tab form was on screen before the click').toBeGreaterThan(0);
			expect(frames.slice(clickFrame + 1).every((count) => count === 0)).toBe(true);
			if (size.width >= 768) await expect(page.getByText(/Ready to generate|Nothing to generate with yet/).first()).toBeVisible();
		});
	}
});
