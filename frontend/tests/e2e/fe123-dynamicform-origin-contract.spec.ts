import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';

const JOURNEY = 'fe123-dynamicform-origin-contract';
const BEAT = 400;

// MediaLoaderField sends provenance (`${name}__origin`) through a
// dedicated `onOriginChange` callback instead of overloading `onChange` -
// three consumers (chat, video-director composers, RelayTimeline) each
// clobbered their real value with the origin payload because they couldn't
// tell the two calls apart. DynamicForm is the one consumer that still needs
// the origin data (generation provenance), routed here through
// FormField -> FieldChildren -> the structural field wrappers -> DynamicForm.
//
// This journey drives a real preset's generation form (not chat, not the
// video director - both already covered elsewhere), picks an image from
// Generation History into a plain `image`-type field, and asserts the pick
// survives - i.e. the second (`onOriginChange`) call didn't clobber the
// first (`onChange`) call's value, which is exactly the bug shape that hit
// all three other consumers.

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

test('DynamicForm image field survives a History pick alongside onOriginChange', async ({
	page
}) => {
	test.setTimeout(90000);
	await loginAsOwner(page);
	const token = await ownerToken(page);

	// --- Seed a real generation to pick from History.
	const pngBuffer = Buffer.from(TINY_PNG_BASE64, 'base64');
	const uploadRes = await page.request.post('/api/generations/upload', {
		headers: { Authorization: `Bearer ${token}` },
		multipart: {
			files: { name: 'fe123-origin-contract.png', mimeType: 'image/png', buffer: pngBuffer }
		}
	});
	expect(uploadRes.ok(), `upload -> ${uploadRes.status()}`).toBeTruthy();
	const generationId = (await uploadRes.json()).data.generation_id as string;

	const me = await apiGet(page, '/api/auth/me', token);
	const userId = me.data.id as string;

	// --- Qwen-Image/img2img has a plain top-level `source_image` field (no
	// gating checkbox, unlike SDXL's ControlNet image which needs enabling
	// first) - the simplest real image field reachable via DynamicForm.
	const list = await apiGet(page, '/api/presets?include_uninstalled=true', token);
	const presets = (list.data || []) as Array<{
		id: string;
		name: string;
		engine?: string;
		installed?: boolean;
	}>;
	const preset = presets.find((p) => p.engine === 'native' && p.name === 'Qwen-Image');
	if (!preset) {
		test.skip(true, 'No native Qwen-Image preset available on this throwaway instance.');
		return;
	}

	if (!preset.installed) {
		await apiPost(page, `/api/presets/${preset.id}/install`, token);
	}
	await apiPost(page, `/api/presets/${preset.id}/assign`, token, { user_ids: [userId] });

	const modesRes = await apiGet(page, `/api/presets/${preset.id}/modes`, token);
	const modes = (modesRes.data?.modes || []) as Array<{ name: string; label?: string }>;
	const img2img = modes.find((m) => m.name === 'img2img');
	if (!img2img) {
		test.skip(true, `No img2img mode on ${preset.id}.`);
		return;
	}

	await page.goto('/generate');
	await page.getByRole('button', { name: 'Choose a preset' }).click();
	await page.getByText(preset.name, { exact: true }).first().click();
	await page.getByRole('button', { name: /Use this preset|Keep selected/ }).click();
	await page.waitForTimeout(BEAT);

	const modeLabel = img2img.label || img2img.name;
	await page.getByRole('button', { name: modeLabel, exact: true }).click();
	await page.waitForTimeout(BEAT);

	// 04f53dc7 ("Reference media gets its own tab, out of the generation tab")
	// moved source_image out of the Generation tab into its own References tab.
	await page.getByRole('tab', { name: 'References' }).click();
	await page.waitForTimeout(BEAT);
	await screenshot(page, JOURNEY, '01-img2img-selected');

	// --- Open History from the source_image field and pick the seeded
	// generation (non-compact rendering, unlike the chat/director journeys).
	// 71a5d03a ("The media field fits where it is put...") replaced the single
	// "Drop an image, or ..." line with a standalone `emptyHint` ("Drop an
	// image here") plus a separate "or paste from clipboard" line below it.
	// Scoped to the source_image field itself — the same text/hint renders
	// (hidden) in every other media field on the page.
	const dropHint = page
		.locator('[data-field-name="source_image"]')
		.getByText(/Drop an image here/i)
		.first();
	await expect(dropHint).toBeVisible({ timeout: 20000 });
	const dropzone = dropHint.locator('xpath=ancestor::*[contains(@class, "border-dashed")][1]');
	await dropzone.getByText('History', { exact: true }).click();

	const historyModal = page.getByText('Select Image from Generation History');
	await expect(historyModal).toBeVisible({ timeout: 20000 });
	await page.waitForTimeout(BEAT);

	const thumbnail = page.locator(`img[src*="${generationId}"]`).first();
	await expect(thumbnail, 'seeded generation should appear in the history grid').toBeVisible({
		timeout: 20000
	});
	await thumbnail.locator('xpath=ancestor::*[@role="button"][1]').click();

	await expect(historyModal).not.toBeVisible({ timeout: 20000 });
	await page.waitForTimeout(BEAT);

	// --- Decisive assertion: the field shows the picked image. MediaLoaderField
	// fires onChange(name, value) then onOriginChange(name, origin) for this
	// same pick - if either the split were wired wrong, or a future consumer
	// went back to reading origin off onChange, this is exactly what would
	// have caught it (the field would revert to the empty dropzone, the same
	// way the chat media-loader history-pick bug once did).
	await expect(dropHint).not.toBeVisible();
	const previewImage = page.locator(`img[src*="${generationId}"]`).first();
	await expect(previewImage, 'source_image field should render the picked image').toBeVisible({
		timeout: 20000
	});
	await screenshot(page, JOURNEY, '02-picked');

	console.log(`[${JOURNEY}] history pick survived onOriginChange for generation ${generationId}`);
});
