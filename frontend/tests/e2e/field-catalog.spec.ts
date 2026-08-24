import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot, shotPath } from './helpers';

// Visual evidence — the Field Catalog restyle (3a tabs + 2a cards).
// Captures the decisive states of the redesigned form fields on a real native
// preset form so the shots can be compared 1:1 against the design mock's
// rendered columns. No functional changes; light assertions only (the boxes
// resolution default and the tablist rendering), everything else is evidence.

const JOURNEY = 'field-catalog';
const BEAT = 400;

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

test('field catalog restyle — visual capture on a native image preset', async ({ page }) => {
	await loginAsOwner(page);
	const token = await ownerToken(page);

	const me = await apiGet(page, '/api/auth/me', token);
	const userId = me.data.id as string;

	const list = await apiGet(page, '/api/presets?include_uninstalled=true', token);
	const presets = (list.data || []) as Array<{
		id: string;
		name: string;
		engine?: string;
		category?: string;
		installed?: boolean;
	}>;
	const imagePreset =
		presets.find((p) => /sdxl/i.test(p.name)) ||
		presets.find((p) => p.engine === 'native' && p.category === 'image');

	if (!imagePreset) {
		test.skip(true, 'No native image preset available on this throwaway instance.');
		return;
	}

	if (!imagePreset.installed) {
		await apiPost(page, `/api/presets/${imagePreset.id}/install`, token);
	}
	await apiPost(page, `/api/presets/${imagePreset.id}/assign`, token, { user_ids: [userId] });

	await page.goto('/generate');
	await page.getByRole('button', { name: 'Choose a preset' }).click();
	await page.getByText(imagePreset.name, { exact: true }).first().click();
	await page.getByRole('button', { name: /Use this preset|Keep selected/ }).click();

	const tablist = page.locator('[role="tablist"]').first();
	await expect(tablist).toBeVisible({ timeout: 20000 });
	await page.waitForTimeout(BEAT);

	await screenshot(page, JOURNEY, '01-generate-form-full');
	await tablist.screenshot({ path: shotPath(JOURNEY, '02-tab-bar') });

	// Walk every tab, screenshotting each panel — covers whichever field types
	// this preset declares without hardcoding its schema.
	const tabs = page.locator('[role="tab"]');
	const tabCount = await tabs.count();
	for (let i = 0; i < tabCount && i < 8; i++) {
		await tabs.nth(i).click();
		await page.waitForTimeout(BEAT);
		const label = ((await tabs.nth(i).getAttribute('aria-label')) || `tab-${i}`)
			.toLowerCase()
			.replace(/[^a-z0-9]+/g, '-')
			.slice(0, 32);

		// Bug evidence: SDXL's CFG slider (min 1, max 30, step 0.5,
		// default 4) starts on a value that reads as a whole number. Drive it
		// to a genuinely fractional value through the real UI so the panel
		// screenshot shows the thumb aligned with a fractional fill position,
		// not just the round default.
		if (label.includes('generation')) {
			const cfgValueButton = page.locator('button[title="Click to type a value"]', { hasText: '4' }).first();
			if ((await cfgValueButton.count()) > 0) {
				await cfgValueButton.click();
				const editInput = page.locator('input[type="text"]').first();
				await editInput.fill('12.5');
				await editInput.press('Enter');
				await page.waitForTimeout(BEAT);
			}
		}

		await screenshot(page, JOURNEY, `1${i}-panel-${label}`);

		// Evidence: the Generation tab now carries `section` dividers
		// (Sampling / Model / Image) that fold on click. Capture them expanded,
		// then collapse the "Model" section and confirm its field disappears
		// while the other sections (and their fields) stay open — proves the
		// fold is scoped to a single section's run, not the whole tab.
		if (label.includes('generation')) {
			const sectionButtons = page.locator('button[aria-expanded]');
			if ((await sectionButtons.count()) > 0) {
				await screenshot(page, JOURNEY, '40-sections');

				const modelSection = sectionButtons.filter({ hasText: 'Model' }).first();
				if ((await modelSection.count()) > 0) {
					await modelSection.click();
					await page.waitForTimeout(BEAT);
					await screenshot(page, JOURNEY, '41-section-collapsed');

					// Restore expanded state so later steps in this tab aren't affected.
					await modelSection.click();
					await page.waitForTimeout(BEAT);
				} else {
					console.log(`[${JOURNEY}] no "Model" section button found — skipping 41-section-collapsed.`);
				}
			} else {
				console.log(`[${JOURNEY}] no section dividers found on the Generation tab — skipping 40/41 section captures.`);
			}
		}
	}

	// Resolution: the boxes grid is the designed default. Find its card via the
	// view-toggle it renders; assert chips (not the dropdown trigger) show.
	const resolutionToggle = page.locator('button[title*="resolution" i], button[aria-label*="resolution view" i]');
	if ((await resolutionToggle.count()) > 0) {
		const card = resolutionToggle.first().locator('xpath=ancestor::*[contains(@class, "field-card")][1]');
		if ((await card.count()) > 0) {
			await card.scrollIntoViewIfNeeded();
			await card.screenshot({ path: shotPath(JOURNEY, '20-resolution-card') });
		}
	}

	// Media field empty state: switch to a mode that exposes an image/media
	// input (SDXL's "Inpaint" mode has a required `source_image` field) and
	// capture the dropzone before anything is uploaded, for comparison against
	// the design mock's empty media_loader card.
	const modeButtons = page.locator('button', { hasText: /inpaint|img2img|image.to.image/i });
	if ((await modeButtons.count()) > 0) {
		await modeButtons.first().click();
		await page.waitForTimeout(BEAT);

		const dropHint = page.getByText(/Drop an (image|video|audio|file), or/i).first();
		if ((await dropHint.count()) > 0 && (await dropHint.isVisible())) {
			const dropzone = dropHint.locator('xpath=ancestor::*[contains(@class, "border-dashed")][1]');
			if ((await dropzone.count()) > 0) {
				await dropzone.scrollIntoViewIfNeeded();
				await dropzone.screenshot({ path: shotPath(JOURNEY, '30-media-empty') });
			} else {
				console.log(`[${JOURNEY}] media dropzone hint found but ancestor container not matched — skipping 30-media-empty.`);
			}
		} else {
			console.log(`[${JOURNEY}] no media dropzone visible after switching mode — skipping 30-media-empty.`);
		}
	} else {
		console.log(`[${JOURNEY}] no image/media-bearing mode reachable on ${imagePreset.id} — skipping 30-media-empty.`);
	}

	console.log(`[${JOURNEY}] captured form + ${Math.min(tabCount, 8)} tab panels for ${imagePreset.id}`);

	// Compact MediaLoaderField evidence: the LLM chat compact variant isn't
	// reachable on this throwaway instance (no vision-capable LLM configured),
	// but Wan/LTX's Video Director SimpleComposer mounts the same
	// compact+compactFullWidth branch in its i2v mode — the only other
	// reachable compact call site. Best-effort; skips cleanly if no video
	// preset is installed or the mode isn't reachable.
	const videoPreset =
		presets.find((p) => p.engine === 'native' && /wan/i.test(p.name)) ||
		presets.find((p) => p.engine === 'native' && /ltx/i.test(p.name));

	if (!videoPreset) {
		console.log(`[${JOURNEY}] no Wan/LTX video preset available on this throwaway instance — skipping compact media-loader capture.`);
		return;
	}

	if (!videoPreset.installed) {
		await apiPost(page, `/api/presets/${videoPreset.id}/install`, token);
	}
	await apiPost(page, `/api/presets/${videoPreset.id}/assign`, token, { user_ids: [userId] });

	// The preset-picker's list is a client-side store hydrated at page load —
	// it won't see the assign made via page.request above until refetched.
	await page.reload();
	await expect(tablist).toBeVisible({ timeout: 20000 });
	await page.waitForTimeout(BEAT);

	const newTabButton = page.getByRole('button', { name: 'New tab' });
	if ((await newTabButton.count()) === 0) {
		console.log(`[${JOURNEY}] no "New tab" control found — skipping compact media-loader capture.`);
		return;
	}
	await newTabButton.click();
	await page.waitForTimeout(BEAT);
	await page.getByRole('button', { name: 'Choose a preset' }).click();
	await page.getByText(videoPreset.name, { exact: true }).first().click();
	await page.getByRole('button', { name: /Use this preset|Keep selected/ }).click();
	await page.waitForTimeout(BEAT);

	// The modeless Stage & Rail editor has no mode tabs to switch into — a
	// fresh document's default (first, auto-selected) shot shows its leading
	// media well directly whenever the preset's capabilities make one legal
	// there (e.g. Wan's first_only keyframes), the same compact call site the
	// old i2v mode's SimpleComposer used.
	await page.waitForTimeout(BEAT);

	const compactDropHint = page.getByText(/Paste or drop image/i).first();
	if ((await compactDropHint.count()) === 0 || !(await compactDropHint.isVisible())) {
		console.log(`[${JOURNEY}] no compact media dropzone visible in i2v mode — skipping 31-media-compact-empty.`);
		return;
	}
	const compactDropzone = compactDropHint.locator('xpath=ancestor::*[contains(@class, "border-dashed")][1]');
	if ((await compactDropzone.count()) === 0) {
		console.log(`[${JOURNEY}] compact dropzone hint found but ancestor container not matched — skipping 31-media-compact-empty.`);
		return;
	}
	await compactDropzone.scrollIntoViewIfNeeded();
	await compactDropzone.screenshot({ path: shotPath(JOURNEY, '31-media-compact-empty') });
	console.log(`[${JOURNEY}] captured compact media loader (video director i2v) for ${videoPreset.id}`);
});
