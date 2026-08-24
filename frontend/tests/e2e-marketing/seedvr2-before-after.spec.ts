import { test, expect } from '@playwright/test';
import { loginAsOwner, beat } from './helpers';

// Scene 10: the shipped SeedVR2 bar-restore.mp4 example IS a real
// before/after restoration pair (see shot_list.md) - honest capture is to
// play that exact seeded history clip rather than simulate a live GPU run.
// tests/e2e/marketing/seed.py tags SeedVR2's video_upscale row "restoration".
test('seedvr2-before-after', async ({ page }) => {
	await loginAsOwner(page);
	await page.goto('/history');
	await page.waitForLoadState('networkidle');
	await beat(page, 1000);

	await page.getByRole('button', { name: 'Videos only' }).click();
	await beat(page, 1000);

	const detailsButtons = page.getByRole('button', { name: 'View generation details' });
	await expect(detailsButtons.first()).toBeVisible({ timeout: 15000 });
	const count = await detailsButtons.count();
	// The seeded SeedVR2 video_upscale row is the only "restoration"-tagged
	// video among the video-only filtered set; open detail panels until its
	// preset/tag is visible rather than assuming card order.
	let opened = false;
	for (let i = 0; i < count && !opened; i++) {
		await detailsButtons.nth(i).scrollIntoViewIfNeeded();
		await detailsButtons.nth(i).click({ force: true });
		await expect(page.getByText('Generation Details')).toBeVisible({ timeout: 15000 });
		const isSeedVR2 = await page.getByText('01KXB7C553THYMSMKY1QSYESFM').isVisible().catch(() => false);
		if (isSeedVR2) {
			opened = true;
			break;
		}
		await page.keyboard.press('Escape');
		await beat(page, 300);
	}

	if (opened) {
		const video = page.locator('video').first();
		await expect(video).toBeVisible({ timeout: 15000 });
		await video.evaluate((el: HTMLVideoElement) => el.play());
		await beat(page, 3000);
	}
});
