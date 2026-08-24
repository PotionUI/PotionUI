import { test, expect, type Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { loginAsOwner, ownerToken, screenshot } from './helpers';
import { installAndSelectImagePreset } from './presetPreamble';

// Bug report: clicking a generation inside the generation page's "Last
// generations" drawer opens GenerationDetailsModal *inside* the drawer's DOM.
// The drawer's slide-out animation puts a CSS `transform` on an ancestor,
// which becomes the containing block for the modal's `position: fixed`
// backdrop (see BaseModal.svelte) - the dialog then sizes itself relative to
// the drawer's ~480px panel instead of the viewport, and renders unreadably
// narrow. The fix portals the modal to <body> (frontend/src/lib/actions/portal.ts)
// so it always sizes against the real viewport, the same as when it's opened
// from /history.
//
// `/api/generations/upload` always writes `preset_id=NULL` (see
// GenerationHistoryArchive.upload_generations) - there is no public API to
// scope an uploaded generation to a preset, so the row is patched afterward
// with a direct sqlite UPDATE against the throwaway instance's own scratch DB,
// the same technique tests/e2e/marketing/seed.py uses for the same reason.

const JOURNEY = 'fe149-drawer-modal-escape';

const TINY_PNG_BASE64 =
	'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=';

async function uploadGeneration(page: Page, token: string): Promise<string> {
	const pngBuffer = Buffer.from(TINY_PNG_BASE64, 'base64');
	const uploadRes = await page.request.post('/api/generations/upload', {
		headers: { Authorization: `Bearer ${token}` },
		multipart: {
			files: {
				name: 'fe149-repro.png',
				mimeType: 'image/png',
				buffer: pngBuffer
			}
		}
	});
	expect(uploadRes.ok(), `upload -> ${uploadRes.status()}`).toBeTruthy();
	const body = await uploadRes.json();
	return body.data.generation_id as string;
}

/** Scope the upload to `presetId` so it shows up in the "Last generations"
 * drawer, which filters `GET /api/generations/history?preset_id=...`. */
function scopeGenerationToPreset(generationId: string, presetId: string): void {
	const dbPath = process.env.E2E_DB_PATH;
	expect(dbPath, 'E2E_DB_PATH must be set by tests/e2e/ui/run.py').toBeTruthy();
	const script = `
import sqlite3
conn = sqlite3.connect(${JSON.stringify(dbPath)}, timeout=30)
try:
    conn.execute("UPDATE generations SET preset_id=? WHERE id=?", (${JSON.stringify(presetId)}, ${JSON.stringify(generationId)}))
    conn.commit()
finally:
    conn.close()
`;
	execFileSync('python3', ['-c', script]);
}

test('generation details modal opened from the Last Generations drawer escapes the drawer', async ({
	page
}) => {
	await loginAsOwner(page);
	const token = await ownerToken(page);

	const preset = await installAndSelectImagePreset(page);
	test.skip(!preset, 'No installable native image preset available on this throwaway instance.');
	if (!preset) return;

	const generationId = await uploadGeneration(page, token);
	scopeGenerationToPreset(generationId, preset.id);

	// --- Open the "Last generations" drawer and click into the seeded item.
	const drawerToggle = page.getByRole('button', { name: 'Last generations' });
	await expect(drawerToggle).toBeVisible({ timeout: 20000 });
	await drawerToggle.click();

	const drawerHeading = page.getByRole('heading', { name: 'Last Generations' });
	await expect(drawerHeading).toBeVisible({ timeout: 20000 });

	// The card's accessible name comes from MediaPreview.svelte's fixed
	// `alt="Generated content"` on the thumbnail `<img>` - unique on the page
	// since exactly one generation is seeded for this preset.
	const card = page.getByRole('button', { name: 'Generated content' });
	await expect(card).toBeVisible({ timeout: 20000 });
	await screenshot(page, JOURNEY, '01-drawer-open-with-generation');
	await card.click();

	// --- The details modal must be the same full-viewport dialog the history
	// page shows, not clipped to the drawer's ~480px panel.
	const dialog = page.getByRole('dialog', { name: 'Generation Details' });
	await expect(dialog).toBeVisible({ timeout: 20000 });
	await page.waitForTimeout(300); // let the open transition settle before measuring
	await screenshot(page, JOURNEY, '02-details-modal-open');

	const box = await dialog.boundingBox();
	const viewport = page.viewportSize();
	expect(box, 'dialog must report a bounding box').toBeTruthy();
	expect(viewport, 'page must report a viewport size').toBeTruthy();

	expect(
		box!.width,
		`dialog width (${box!.width}px) should be viewport-scale (> 60% of ${viewport!.width}px), ` +
			`not clipped to the drawer's own ~480px panel width`
	).toBeGreaterThan(viewport!.width * 0.6);

	// --- Structural check: the reworked header carries a status pill next to
	// the close button, and the media pane's hover action overlay (restored
	// to its pre-rework form) still exposes the reuse affordance.
	await expect(dialog.getByText('completed', { exact: false })).toBeVisible();
	await expect(dialog.getByRole('button', { name: "Reuse this generation's settings" })).toBeVisible();
});
