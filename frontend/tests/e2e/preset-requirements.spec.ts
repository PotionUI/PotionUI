import { test, expect, type Page } from '@playwright/test';
import { mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { loginAsOwner, ownerToken, screenshot } from './helpers';

const __dirname = dirname(fileURLToPath(import.meta.url));

// Admin -> Presets -> Requirements tab: a preset that declares a satisfied
// `binary` requirement and a missing one shows the "can't run here" verdict,
// the Python/System... section for the missing entry stays expanded with its
// hint and re-check keeps working. The throwaway backend runs with cwd = the
// real repo checkout (see tests/e2e/harness/e2e_harness.py), so this writes
// straight into the real, .gitignored content/presets/local/ - the unique id
// plus the cleanup below keep this from leaving anything behind.
const JOURNEY = 'preset-requirements';
const REPO_ROOT = resolve(__dirname, '../../..');

async function apiPost(page: Page, url: string, token: string, data?: unknown) {
	const res = await page.request.post(url, {
		headers: { Authorization: `Bearer ${token}` },
		data: data ?? {}
	});
	expect(res.ok(), `POST ${url} -> ${res.status()}`).toBeTruthy();
	return res.json();
}

test('Admin > Presets > Requirements - satisfied and missing binaries render the grouped verdict', async ({
	page
}) => {
	const familyId = `e2e-requirements-${Date.now()}`;
	const presetDir = resolve(REPO_ROOT, 'content/presets/local', familyId);

	const pageErrors: string[] = [];
	page.on('pageerror', (err) => pageErrors.push(`pageerror: ${err.message}`));
	page.on('console', (msg) => {
		if (msg.type() === 'error') pageErrors.push(`console.error: ${msg.text()}`);
	});

	try {
		mkdirSync(resolve(presetDir, 'modes/check'), { recursive: true });
		writeFileSync(
			resolve(presetDir, 'preset.yml'),
			`schema: 1
id: "${familyId}"
name: "E2E Requirements Fixture"
category: "utility"
version: "1.0.0"
engine: "native"
tags: ["e2e"]

requirements:
  - type: binary
    name: python3
  - type: binary
    name: definitely-not-installed-xyz
    hint: "Install this binary and make sure it is on PATH"

modes:
  - check
`
		);
		writeFileSync(
			resolve(presetDir, 'modes/check/pipeline.yml'),
			`pipeline:
  - name: "gallery"
    id: "gallery"
    enabled: true
    configuration:
      mode: "save"
`
		);
		writeFileSync(resolve(presetDir, 'modes/check/form.yml'), `name: "check"\nfields: []\n`);

		await loginAsOwner(page);
		const token = await ownerToken(page);

		// Reload clears + re-scans the WHOLE preset catalog from disk, so this
		// picks up the fixture even though the throwaway backend started before
		// it existed on disk. `preset_id` here is only the reload target after
		// the rescan - any id works as the trigger.
		await apiPost(page, `/api/presets/${familyId}/reload`, token);

		await page.goto('/admin?tab=presets');
		await expect(page.getByRole('heading', { level: 2 })).toBeVisible({ timeout: 10000 });

		await page.getByPlaceholder('Search presets by name, engine, type, or tag…').fill('E2E Requirements Fixture');
		const row = page.getByRole('option', { name: /E2E Requirements Fixture/ });
		await expect(row).toBeVisible({ timeout: 10000 });
		await expect(row).toHaveAttribute('aria-selected', 'true', { timeout: 10000 });
		await row.click();
		if (pageErrors.length) console.log('PAGE ERRORS:', JSON.stringify(pageErrors, null, 2));
		await expect(page.getByRole('heading', { name: 'E2E Requirements Fixture' })).toBeVisible({ timeout: 15000 });
		await screenshot(page, JOURNEY, '00-selected');

		await page.locator('nav[aria-label="Preset details"]').getByRole('button', { name: 'Requirements' }).click();
		await expect(page.getByText('/ 2 satisfied', { exact: false })).toBeVisible({ timeout: 10000 });
		await expect(page.getByText("This preset can't run here: 1 missing.")).toBeVisible();
		await expect(page.getByText('definitely-not-installed-xyz', { exact: false }).first()).toBeVisible();
		await expect(page.getByText('Install this binary and make sure it is on PATH', { exact: false })).toBeVisible();

		await page.setViewportSize({ width: 1440, height: 900 });
		await screenshot(page, JOURNEY, '01-some-missing');

		const recheck = page.getByRole('button', { name: 'Re-check requirements' });
		await recheck.click();
		await expect(page.getByText("This preset can't run here: 1 missing.")).toBeVisible({ timeout: 10000 });
	} finally {
		rmSync(presetDir, { recursive: true, force: true });
	}
});
