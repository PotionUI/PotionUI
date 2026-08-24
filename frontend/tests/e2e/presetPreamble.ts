import { expect, type Page } from '@playwright/test';
import { ownerToken } from './helpers';

export interface DiscoveredPreset {
	id: string;
	name: string;
}

/**
 * Finds an installable native image preset (prefers SDXL by id, same
 * fixture other specs already key off — see fe75-flow-view.spec.ts), installs
 * + assigns it to the owner if needed, and selects it through the real preset
 * picker on /generate. A segment editor (InlineChipEditor) only mounts once a
 * preset with prompt fields is active — see trigger-word-highlight.spec.ts's
 * header for why a fresh throwaway instance has none by default.
 *
 * Discovers the preset dynamically rather than hardcoding an id/slug: a fixed
 * reference silently rots when presets are renamed or removed (this replaced
 * an earlier version pinned to a 'carousel_demo' preset that no longer exists
 * anywhere in presets/ — confirmed via a real run, not a search-error).
 */
export async function installAndSelectImagePreset(page: Page): Promise<DiscoveredPreset | null> {
	const token = await ownerToken(page);
	const headers = { Authorization: `Bearer ${token}` };

	const list = await page.request.get('/api/presets?include_uninstalled=true', { headers });
	const listJson = await list.json().catch(() => ({}));
	const presets = (listJson.data || []) as Array<{
		id: string;
		name: string;
		engine?: string;
		category?: string;
		installed?: boolean;
	}>;
	const preset =
		presets.find((p) => /sdxl/i.test(p.id)) ||
		presets.find((p) => p.engine === 'native' && p.category === 'image');
	if (!preset) return null;

	if (!preset.installed) {
		const install = await page.request.post(`/api/presets/${preset.id}/install`, { headers });
		const installJson = await install.json().catch(() => ({}));
		expect(
			install.ok() && installJson.success,
			`preset install must succeed to render a prompt section: ${install.status()} ${JSON.stringify(installJson)}`
		).toBeTruthy();
	}

	// Installing does not assign; the generate page only lists presets assigned
	// to the current user.
	const me = await page.request.get('/api/auth/me', { headers });
	const userId = (await me.json())?.data?.id as string;
	expect(userId, 'owner user id').toBeTruthy();
	const assign = await page.request.post(`/api/presets/${preset.id}/assign`, {
		headers,
		data: { user_ids: [userId] }
	});
	const assignJson = await assign.json().catch(() => ({}));
	expect(
		assign.ok() && assignJson.success,
		`preset assign must succeed: ${assign.status()} ${JSON.stringify(assignJson)}`
	).toBeTruthy();

	await page.goto('/generate');
	await page.waitForLoadState('networkidle');

	const pickerTrigger = page.locator('button[aria-haspopup="dialog"]', { hasText: 'Choose a preset' });
	await expect(pickerTrigger).toBeVisible({ timeout: 15000 });
	await pickerTrigger.click();
	const presetList = page.locator('[role="listbox"][aria-label="Presets"]');
	await expect(presetList).toBeVisible({ timeout: 15000 });
	await presetList.getByText(preset.name, { exact: true }).click();
	await page.getByRole('button', { name: /Use this preset|Keep selected/ }).click();

	return { id: preset.id, name: preset.name };
}
