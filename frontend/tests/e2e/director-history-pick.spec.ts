import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot, shotPath } from './helpers';

// Bug report: in the Video Director, picking an image from Generation
// History for a shot's leading frame did nothing visible — the compact media
// field never showed the picked image.
//
// Root cause: commit 14378ad5 made MediaLoaderField fire a SECOND
// onChange call per pick — one for the real value, one for the
// `${name}__origin` sibling key consumed by provenance tracking. Every Video
// Director composer wired its onChange as `(_n, v) => patch({ someKey: v })`,
// ignoring which field name fired. The origin-key call's payload silently
// clobbered the value the first call had just set — an upload cleared the
// field outright (origin value is `undefined` on upload), and a history pick
// left the field holding `{ generation_id, file_index }` instead of the real
// media object, so the preview never rendered.
//
// This journey seeds a real generation via the real upload API, opens the
// leading-frame media well on the Stage & Rail editor's default shot, opens
// History from it, picks that generation's image, and proves the preview
// renders it — the fix is that each media slot's onChange now checks the
// field name before patching (DirectorMediaSlot, unchanged by the modeless
// rework — see stage-rail/StageShot.svelte for the current call site).

const JOURNEY = 'director-history-pick';
const BEAT = 400;

const TINY_PNG_BASE64 =
	'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=';

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

test('director history pick — i2v compact media field renders the picked image', async ({ page }) => {
	await loginAsOwner(page);
	const token = await ownerToken(page);

	const me = await apiGet(page, '/api/auth/me', token);
	const userId = me.data.id as string;

	// --- Seed a real generation to pick from History.
	const pngBuffer = Buffer.from(TINY_PNG_BASE64, 'base64');
	const uploadRes = await page.request.post('/api/generations/upload', {
		headers: { Authorization: `Bearer ${token}` },
		multipart: {
			files: {
				name: 'director-history-pick.png',
				mimeType: 'image/png',
				buffer: pngBuffer
			}
		}
	});
	expect(uploadRes.ok(), `upload -> ${uploadRes.status()}`).toBeTruthy();
	const uploadBody = await uploadRes.json();
	const generationId = uploadBody.data.generation_id as string;

	// --- Find a Wan/LTX video preset (native engine, i2v-capable Director).
	const list = await apiGet(page, '/api/presets?include_uninstalled=true', token);
	const presets = (list.data || []) as Array<{
		id: string;
		name: string;
		engine?: string;
		installed?: boolean;
	}>;
	const videoPreset =
		presets.find((p) => p.engine === 'native' && /wan/i.test(p.name)) ||
		presets.find((p) => p.engine === 'native' && /ltx/i.test(p.name));

	if (!videoPreset) {
		test.skip(true, 'No native Wan/LTX preset available on this throwaway instance.');
		return;
	}

	if (!videoPreset.installed) {
		await apiPost(page, `/api/presets/${videoPreset.id}/install`, token);
	}
	await apiPost(page, `/api/presets/${videoPreset.id}/assign`, token, { user_ids: [userId] });

	await page.goto('/generate');
	await page.getByRole('button', { name: 'Choose a preset' }).click();
	await page.getByText(videoPreset.name, { exact: true }).first().click();
	await page.getByRole('button', { name: /Use this preset|Keep selected/ }).click();
	await page.waitForTimeout(BEAT);

	// The modeless Stage & Rail editor has no mode tabs: a fresh document's
	// default (first, auto-selected) shot shows its leading media well
	// directly whenever the preset's capabilities make one legal there (e.g.
	// Wan's first_only keyframes) -- the same compact call site the old i2v
	// mode used.
	await page.waitForTimeout(BEAT);

	const compactDropHint = page.getByText(/Paste or drop image/i).first();
	if ((await compactDropHint.count()) === 0 || !(await compactDropHint.isVisible().catch(() => false))) {
		test.skip(true, `No leading-frame media well reachable on ${videoPreset.id}'s default shot.`);
		return;
	}
	await expect(compactDropHint).toBeVisible({ timeout: 20000 });
	const compactDropzone = compactDropHint.locator('xpath=ancestor::*[contains(@class, "border-dashed")][1]');
	await screenshot(page, JOURNEY, '01-i2v-compact-empty');

	// --- Open History from the compact field and pick the seeded generation.
	await compactDropzone.getByTitle('History').click();

	const historyModal = page.getByText('Select Image from Generation History');
	await expect(historyModal).toBeVisible({ timeout: 20000 });
	await page.waitForTimeout(BEAT);

	const thumbnail = page.locator(`img[src*="${generationId}"]`).first();
	await expect(thumbnail, 'seeded generation should appear in the history grid').toBeVisible({ timeout: 20000 });
	await thumbnail.locator('xpath=ancestor::*[@role="button"][1]').click();

	await expect(historyModal).not.toBeVisible({ timeout: 20000 });
	await page.waitForTimeout(BEAT);

	// --- Decisive assertion: the compact field's preview shows the picked
	// image, not an empty dropzone and not a clobbered/blank field.
	await expect(compactDropHint).not.toBeVisible();
	const previewImage = page.locator(`img[src*="${generationId}"]`).first();
	await expect(previewImage, 'compact media field should render the picked image').toBeVisible({ timeout: 20000 });

	const previewCard = previewImage.locator('xpath=ancestor::*[contains(@class, "border-line-strong")][1]');
	await previewCard.scrollIntoViewIfNeeded();
	await previewCard.screenshot({ path: shotPath(JOURNEY, '02-i2v-compact-picked') });

	console.log(`[${JOURNEY}] history pick rendered for generation ${generationId} on ${videoPreset.id}`);
});
