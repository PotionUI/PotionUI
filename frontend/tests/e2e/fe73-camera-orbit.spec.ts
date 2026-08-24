import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';

// Camera-orbit viewfinder — verifies the three orientation fixes:
// 1. Dragging DOWN raises the camera over the subject (standard orbit
//    convention), so the top-of-head gesture quantizes to high angle /
//    overhead, never worm's-eye.
// 2. The face (eyes + nose) stays visible from below-front poses — culling
//    is relative to the head's depth, not absolute.
// 3. A live caption under the stage names the quantized shots while dragging.

const JOURNEY = 'fe73-camera-orbit';

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

async function dragOnStage(page: Page, dx: number, dy: number) {
	const stage = page.locator('.orbit .stage');
	const box = await stage.boundingBox();
	expect(box, 'orbit stage should be visible').toBeTruthy();
	const cx = box!.x + box!.width / 2;
	const cy = box!.y + box!.height / 2;
	await page.mouse.move(cx, cy);
	await page.mouse.down();
	await page.mouse.move(cx + dx, cy + dy, { steps: 8 });
	await page.mouse.up();
}

test('camera orbit — drag direction, face visibility, live caption', async ({ page }) => {
	await loginAsOwner(page);
	const token = await ownerToken(page);

	const me = await apiGet(page, '/api/auth/me', token);
	const userId = me.data.id as string;

	const list = await apiGet(page, '/api/presets?include_uninstalled=true', token);
	const presets = (list.data || []) as Array<{ id: string; name: string; installed?: boolean }>;
	// Preset ids are ULIDs — match the camera_shot hosts (SDXL / Krea-2) by name.
	const sdxl = presets.find((p) => /sdxl|krea/i.test(p.name) || /sdxl|krea/i.test(p.id));
	console.log(`[${JOURNEY}] presets=${presets.map((p) => p.name).join(', ')} -> ${sdxl?.name}`);
	if (!sdxl) {
		test.skip(true, 'No SDXL/Krea-2 preset (the camera_shot field hosts) on this throwaway instance.');
		return;
	}
	if (!sdxl.installed) await apiPost(page, `/api/presets/${sdxl.id}/install`, token);
	await apiPost(page, `/api/presets/${sdxl.id}/assign`, token, { user_ids: [userId] });

	await page.setViewportSize({ width: 1440, height: 960 });
	await page.goto('/generate');
	await page.waitForLoadState('networkidle');
	await page.waitForTimeout(1000);

	await page.getByRole('button', { name: 'Choose a preset' }).click();
	await page.getByRole('listbox', { name: 'Presets' }).getByText(sdxl.name, { exact: true }).click();
	await page.getByRole('button', { name: /Use this preset|Keep selected/ }).click();
	await page.waitForTimeout(1000);

	await page.getByRole('tab', { name: 'Camera' }).click();
	await page.getByRole('button', { name: '3D' }).click();

	const stage = page.locator('.orbit .stage');
	await stage.scrollIntoViewIfNeeded();
	await expect(stage).toBeVisible();

	// Default pose: eye level, facing front — caption names it, face visible.
	const caption = page.locator('.orbit .caption');
	await expect(caption).toContainText(/eye level/i);
	await expect(page.locator('.orbit .nose')).toHaveCount(1);
	await expect(page.locator('.orbit .eye')).toHaveCount(2);
	await screenshot(page, JOURNEY, '00-orbit-default-eye-level');

	// Drag DOWN 80px: the camera must RISE over the head (el +48 -> high angle).
	// Before the fix this exact gesture produced "worm's-eye".
	await dragOnStage(page, 0, 80);
	await expect(caption).toContainText(/high angle/i);
	await screenshot(page, JOURNEY, '01-orbit-drag-down-high-angle');

	// Keep dragging down to the overhead clamp.
	await dragOnStage(page, 0, 120);
	await expect(caption).toContainText(/overhead|top.?down|bird/i);
	await screenshot(page, JOURNEY, '02-orbit-overhead');

	// Drag UP far enough to swing below the subject: worm's-eye, and the face
	// (eyes + nose) must STILL be visible from below-front — the culling bug
	// used to erase it exactly here.
	await dragOnStage(page, 0, -250);
	await expect(caption).toContainText(/worm/i);
	await expect(page.locator('.orbit .nose')).toHaveCount(1);
	await expect(page.locator('.orbit .eye')).toHaveCount(2);
	await screenshot(page, JOURNEY, '03-orbit-worms-eye-face-visible');

	// The pose flows into the phrase card as before.
	await expect(page.locator('text=Phrase').first()).toBeVisible();
});
