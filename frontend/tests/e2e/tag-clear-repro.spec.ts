import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';

// Bug report: "something is clearing the assigned tags when opening the
// generation details modal". This journey uploads a real generation via the
// real API, assigns two GENERATION tags to it via the real tags endpoint,
// then opens GenerationDetailsModal in a real browser (the same path a user
// takes from /history) and proves two things against the real backend:
//
//  1. The modal's own tag display goes to 0 the moment its detail fetch
//     resolves (a visible symptom matching the report), even though nothing
//     has written to the DB yet.
//  2. Because TagSelector's `selectedTagIds` is now wrongly empty, the very
//     next tag interaction a user would naturally make (re-clicking a tag
//     they can see is "missing") issues a replace-all PUT that permanently
//     drops the other previously-assigned tag from the database.
//
// (2) is the actual persisted "clearing" — verified via a plain GET after
// the modal closes, not by trusting the UI.

const JOURNEY = 'tag-clear-repro';

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

async function apiPut(page: Page, url: string, token: string, data?: unknown) {
	const res = await page.request.put(url, {
		headers: { Authorization: `Bearer ${token}` },
		data: data ?? {}
	});
	expect(res.ok(), `PUT ${url} -> ${res.status()}`).toBeTruthy();
	return res.json();
}

async function createTag(page: Page, token: string, name: string) {
	const body = await apiPost(page, '/api/tags', token, { name, type: 'GENERATION' });
	return body.data.tag.id as string;
}

test('FE tag-clear — opening the generation details modal drops previously-assigned tags', async ({
	page
}) => {
	await loginAsOwner(page);
	const token = await ownerToken(page);

	// --- Set up: one uploaded generation with two GENERATION tags assigned.
	const tagAId = await createTag(page, token, `repro-tag-a-${Date.now()}`);
	const tagBId = await createTag(page, token, `repro-tag-b-${Date.now()}`);

	const pngBuffer = Buffer.from(TINY_PNG_BASE64, 'base64');
	const uploadRes = await page.request.post('/api/generations/upload', {
		headers: { Authorization: `Bearer ${token}` },
		multipart: {
			files: {
				name: 'repro.png',
				mimeType: 'image/png',
				buffer: pngBuffer
			}
		}
	});
	expect(uploadRes.ok(), `upload -> ${uploadRes.status()}`).toBeTruthy();
	const uploadBody = await uploadRes.json();
	const generationId = uploadBody.data.generation_id as string;

	await apiPut(page, `/api/generations/${generationId}/tags`, token, {
		tag_ids: [tagAId, tagBId]
	});

	const baseline = await apiGet(page, `/api/generations/${generationId}/tags`, token);
	const baselineIds = (baseline.data.tags || []).map((t: any) => t.id).sort();
	expect(baselineIds, 'both tags should be assigned before the modal is ever opened').toEqual(
		[tagAId, tagBId].sort()
	);

	// --- Open the details modal the way a real user does: from /history.
	// be77725d ("History tiles reveal their chrome as they grow") made the
	// per-tile action set responsive to rendered tile width — at Playwright's
	// default (narrower) viewport a lone tile lands in a chrome bucket below
	// the one that keeps "View generation details", so it never renders. A
	// wide viewport puts the tile in a bucket wide enough to keep the action.
	await page.setViewportSize({ width: 1440, height: 960 });
	await page.goto('/history');
	const viewButton = page.getByRole('button', { name: 'View generation details' }).first();
	await expect(viewButton).toBeVisible({ timeout: 20000 });
	await viewButton.click();

	const tagButton = page.getByRole('button', { name: 'Manage tags' });
	await expect(tagButton).toBeVisible({ timeout: 20000 });

	// Let the modal's detail fetch (segments/tags) resolve.
	await page.waitForTimeout(1500);
	await screenshot(page, JOURNEY, '01-modal-open-after-detail-fetch');

	// Symptom (1): the tag badge on the button should read 2 (both tags
	// survived the detail fetch). Before the fix it reads nothing (0).
	const badgeLocator = tagButton.locator('span');
	const badgeCountAfterOpen = (await badgeLocator.count()) > 0 ? await badgeLocator.first().innerText() : '0';

	// --- Symptom (2): the natural next click. The tag selector's dropdown
	// still lists every GENERATION tag; with local state wrongly emptied by
	// the detail-fetch bug, tag A shows up as "available" (not selected) even
	// though it is actually already assigned. Clicking it replaces the full
	// tag_ids list with just [A], only because a real fix must instead show
	// both tags already selected and leave DB state untouched on a bare open.
	await tagButton.click();
	const optionA = page.getByRole('option').filter({ hasText: /repro-tag-a-/ });
	if ((await optionA.count()) > 0) {
		await optionA.first().click();
		await page.waitForTimeout(800);
	}

	// Close the modal.
	await page.keyboard.press('Escape');
	await page.waitForTimeout(300);

	const after = await apiGet(page, `/api/generations/${generationId}/tags`, token);
	const afterIds = (after.data.tags || []).map((t: any) => t.id).sort();

	expect.soft(
		badgeCountAfterOpen,
		'TagSelector badge should show 2 tags immediately after the modal\'s detail fetch resolves'
	).toBe('2');
	expect(
		afterIds,
		`both previously-assigned tags should still be present after the modal was opened and closed; got ${JSON.stringify(afterIds)}`
	).toEqual([tagAId, tagBId].sort());
});
