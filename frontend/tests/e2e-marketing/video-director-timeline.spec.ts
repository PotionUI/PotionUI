import { test, expect } from '@playwright/test';
import { loginAsOwner, choosePreset, beat } from './helpers';

// Scene 4 (★): Wan in Video Director mode - Stage & Rail timeline, a second
// shot with its own direction, and the seeded Wan potion.mp4 generation
// attached as a shot's reference frame (tests/e2e/marketing/seed.py).
// Selectors verified against the real modeless Stage & Rail markup (accessible
// names captured from a live run, not guessed from source alone).
test('video-director-timeline', async ({ page }) => {
	await loginAsOwner(page);
	await page.goto('/generate');
	await page.waitForLoadState('networkidle');
	await choosePreset(page, 'Wan 2.1 / 2.2');
	await beat(page, 1200);

	const director = page.getByRole('region', { name: 'Video Director' });
	await expect(director).toBeVisible({ timeout: 20000 });
	await beat(page, 700);

	const shotDirection = director.getByRole('list', { name: 'Shot direction' }).getByRole('textbox').first();
	await expect(shotDirection).toBeVisible({ timeout: 15000 });
	await shotDirection.fill(
		'Extreme close-up on the glass, camera holding perfectly still as the liquid begins to swirl.'
	);
	await beat(page, 800);

	const referenceSlot = director.locator('[data-media-dropzone]').first();
	await expect(referenceSlot, 'the shot reference-frame slot should offer a drop zone').toBeVisible({
		timeout: 10000
	});
	await referenceSlot.getByRole('button', { name: 'History' }).click();

	const historyModal = page.getByText('Select Image from Generation History');
	await expect(historyModal, 'the generation-history modal should open').toBeVisible({ timeout: 5000 });
	await beat(page, 800);

	const thumbnail = page.locator('img[src*="generations"]').first();
	await expect(thumbnail, 'the seeded Wan potion.mp4 generation should appear as a history thumbnail').toBeVisible({
		timeout: 5000
	});
	await thumbnail.locator('xpath=ancestor::*[@role="button"][1]').click();
	await beat(page, 900);

	await expect(historyModal, 'picking a thumbnail should close the history modal').toBeHidden({ timeout: 5000 });
	const attachedPreview = director.getByRole('button', { name: 'View full size' }).first();
	await expect(
		attachedPreview,
		'the reference frame must actually attach - the empty add row should be replaced by a loaded row'
	).toBeVisible({ timeout: 5000 });
	await beat(page, 900);

	const addShot = director.getByRole('button', { name: 'Add shot' });
	if (await addShot.isEnabled().catch(() => false)) {
		await addShot.click();
		await beat(page, 900);
		const secondShotDirection = director.getByRole('list', { name: 'Shot direction' }).getByRole('textbox').first();
		if (await secondShotDirection.isVisible().catch(() => false)) {
			await secondShotDirection.fill('Camera pulls back in a slow crane shot revealing the full workshop.');
			await beat(page, 900);
		}
	}

	// Show the Rail timeline with its shot(s) laid out.
	const rail = page.getByText(/Edit Rail/).first();
	if (await rail.isVisible().catch(() => false)) {
		await rail.scrollIntoViewIfNeeded();
	}
	await beat(page, 1500);
});
