import { test, expect, type Locator, type Page } from '@playwright/test';
import { mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { loginAsOwner, ownerToken, screenshot } from './helpers';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '../../..');
const JOURNEY = 'admin-table-picker';

interface TwinPresets {
	nameA: string;
	nameB: string;
	idA: string;
	idB: string;
	folderA: string;
	folderB: string;
	dirs: string[];
}

function writePreset(dir: string, id: string, name: string, version: string) {
	mkdirSync(resolve(dir, 'modes/check'), { recursive: true });
	writeFileSync(
		resolve(dir, 'preset.yml'),
		`schema: 1
id: "${id}"
name: "${name}"
category: "utility"
version: "${version}"
engine: "native"
tags: ["e2e"]

modes:
  - check
`
	);
	writeFileSync(
		resolve(dir, 'modes/check/pipeline.yml'),
		`pipeline:
  - name: "gallery"
    id: "gallery"
    enabled: true
    configuration:
      mode: "save"
`
	);
	writeFileSync(resolve(dir, 'modes/check/form.yml'), `name: "check"\nfields: []\n`);
}

function createTwins(label: string): TwinPresets {
	const stamp = Date.now();
	const folderA = `e2e-picker-${label}-a-${stamp}`;
	const folderB = `e2e-picker-${label}-b-${stamp}`;
	const twins: TwinPresets = {
		nameA: `E2E Twin ${label}`,
		nameB: `E2E-Twin ${label}`,
		idA: folderA,
		idB: folderB,
		folderA,
		folderB,
		dirs: [resolve(REPO_ROOT, 'content/presets/local', folderA), resolve(REPO_ROOT, 'content/presets/local', folderB)]
	};
	writePreset(twins.dirs[0], twins.idA, twins.nameA, '1.0.0');
	writePreset(twins.dirs[1], twins.idB, twins.nameB, '2.0.0');
	return twins;
}

async function post(page: Page, url: string, token: string, data?: unknown) {
	const res = await page.request.post(url, { headers: { Authorization: `Bearer ${token}` }, data: data ?? {} });
	expect(res.ok(), `POST ${url} -> ${res.status()} ${res.ok() ? '' : await res.text()}`).toBeTruthy();
	return res.json();
}

async function createUser(page: Page, token: string, label: string): Promise<{ id: string; username: string }> {
	const username = `e2e-picker-${label}-${Date.now()}`;
	const body = await post(page, '/api/users', token, {
		username,
		email: `${username}@example.com`,
		password: 'e2e-password-1',
		account_type: 'USER'
	});
	return { id: body.data.id as string, username };
}

function pickerDialog(page: Page, name: string): Locator {
	return page.getByRole('dialog', { name });
}

function rowOf(scope: Locator, text: string | RegExp): Locator {
	return scope.getByRole('row').filter({ hasText: text });
}

async function runPresetFlow(page: Page, label: string) {
	const twins = createTwins(label);
	try {
		await loginAsOwner(page);
		const token = await ownerToken(page);
		await post(page, `/api/presets/${twins.idA}/reload`, token);
		await post(page, `/api/presets/${twins.idA}/install`, token);
		await post(page, `/api/presets/${twins.idB}/install`, token);
		const user = await createUser(page, token, label);

		await page.goto(`/admin?tab=users&id=${user.id}`);
		await page.locator('nav[aria-label="User details"]').getByRole('button', { name: /Presets/ }).click();
		await page.getByRole('button', { name: 'Add presets' }).first().click();

		const dialog = pickerDialog(page, 'Add presets');
		await expect(dialog).toBeVisible({ timeout: 15000 });
		await expect(dialog.getByRole('button', { name: /^Not assigned/ })).toHaveAttribute('aria-current', 'page');

		await dialog.getByRole('searchbox', { name: 'Search presets' }).fill('E2E Twin');
		const rowA = rowOf(dialog, twins.nameA);
		const rowB = rowOf(dialog, twins.nameB);
		await expect(rowA).toHaveCount(1);
		await expect(rowB).toHaveCount(1);

		await expect(rowA).toContainText(/2 same name/i);
		await expect(rowB).toContainText(/2 same name/i);
		await expect(rowA).toContainText(twins.folderA);
		await expect(rowA).toContainText('v1.0.0');
		await expect(rowB).toContainText(twins.folderB);
		await expect(rowB).toContainText('v2.0.0');
		await expect(rowA).not.toContainText(twins.folderB);
		await screenshot(page, JOURNEY, `${label}-picker-twins`);

		await rowA.click();
		await rowB.click();
		await expect(rowA).toHaveAttribute('aria-selected', 'true');
		await expect(rowB).toHaveAttribute('aria-selected', 'true');
		await expect(dialog.locator('[data-picker-summary]')).toContainText('+2 add');
		await dialog.getByRole('button', { name: /^Add 2 presets/ }).click();
		await expect(dialog).toHaveCount(0, { timeout: 15000 });

		const assigned = page.getByRole('grid', { name: 'Assigned presets' });
		await expect(rowOf(assigned, twins.nameA)).toHaveCount(1, { timeout: 15000 });
		await expect(rowOf(assigned, twins.nameB)).toHaveCount(1);
		await expect(rowOf(assigned, twins.nameA)).toContainText(twins.folderA);
		await expect(rowOf(assigned, twins.nameB)).toContainText(twins.folderB);
		await screenshot(page, JOURNEY, `${label}-assigned-card`);

		await page.getByRole('button', { name: 'Add presets' }).first().click();
		await expect(dialog).toBeVisible({ timeout: 15000 });
		await dialog.getByRole('button', { name: /^Assigned/ }).click();
		await dialog.getByRole('searchbox', { name: 'Search presets' }).fill('E2E Twin');
		await expect(rowOf(dialog, twins.nameA)).toHaveCount(1);
		await rowOf(dialog, twins.nameA).click();
		await expect(rowOf(dialog, twins.nameA)).toHaveAttribute('aria-selected', 'false');
		await expect(dialog.locator('[data-picker-summary]')).toContainText('-1 remove');
		await dialog.getByRole('button', { name: /^Remove 1/ }).click();
		await expect(dialog).toHaveCount(0, { timeout: 15000 });

		await expect(rowOf(assigned, twins.nameA)).toHaveCount(0, { timeout: 15000 });
		await expect(rowOf(assigned, twins.nameB)).toHaveCount(1);
		await screenshot(page, JOURNEY, `${label}-after-unassign`);
	} finally {
		for (const dir of twins.dirs) rmSync(dir, { recursive: true, force: true });
	}
}

async function runPlanFlow(page: Page, label: string) {
	await loginAsOwner(page);
	const token = await ownerToken(page);
	const stamp = Date.now();
	const planName = `e2e-picker-plan-${label}-${stamp}`;
	const planRes = await post(page, '/api/admin/plans', token, {
		name: planName,
		description: '',
		limits: [{ kind: 'generations_per_day', value: 5 }]
	});
	const planId = (planRes.data?.id ?? planRes.id) as string;
	expect(planId, 'plan create returned an id').toBeTruthy();
	const first = await createUser(page, token, `${label}-one`);
	const second = await createUser(page, token, `${label}-two`);

	await page.goto(`/admin?tab=users&view=plans&id=${planId}`);
	const assigned = page.locator('[data-plan-assigned]');
	await expect(assigned).toBeVisible({ timeout: 15000 });
	await assigned.getByRole('button', { name: 'Add users' }).click();

	const dialog = pickerDialog(page, 'Assign users to this plan');
	await expect(dialog).toBeVisible({ timeout: 15000 });
	await dialog.getByRole('searchbox', { name: 'Search users' }).fill(`e2e-picker-${label}`);
	await expect(rowOf(dialog, first.username)).toHaveCount(1);
	await expect(rowOf(dialog, second.username)).toHaveCount(1);
	await rowOf(dialog, first.username).click();
	await rowOf(dialog, second.username).click();
	await expect(dialog.locator('[data-picker-summary]')).toContainText('+2 add');
	await screenshot(page, JOURNEY, `${label}-plan-users-picker`);
	await dialog.getByRole('button', { name: /^Add 2 users/ }).click();
	await expect(dialog).toHaveCount(0, { timeout: 15000 });

	await expect(assigned).toContainText(first.username, { timeout: 15000 });
	await expect(assigned).toContainText(second.username);
	await screenshot(page, JOURNEY, `${label}-plan-assigned`);

	await page.goto(`/admin?tab=users&id=${first.id}`);
	await expect(page.locator('[data-user-plan-select]').locator('[data-plan-picker-label]')).toHaveText(planName, {
		timeout: 15000
	});
}

test('admin tells two same-named presets apart by tag, assigns both to a user and unassigns one in the picker', async ({
	page
}) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	await runPresetFlow(page, 'desktop');
});

test('admin assigns users to a plan from the plan editor', async ({ page }) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	await runPlanFlow(page, 'desktop');
});

test.describe('at 390px', () => {
	test.use({ viewport: { width: 390, height: 844 } });

	test('same-named presets stay distinguishable and assignable on a phone', async ({ page }) => {
		await runPresetFlow(page, 'mobile');
	});

	test('users can be assigned to a plan on a phone', async ({ page }) => {
		await runPlanFlow(page, 'mobile');
	});
});
