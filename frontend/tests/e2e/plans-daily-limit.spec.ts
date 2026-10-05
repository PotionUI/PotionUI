import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';

const JOURNEY = 'plans-daily-limit';
const BACKEND_NAME = 'E2E Fake Cloud';
const PRESET_NAME = 'Fake Studio';
const PLAN_NAME = 'E2E daily limit 1';
const BEAT = 400;

const field = (page: Page, name: string) => page.locator(`[data-field-name="${name}"]`);

function unwrap<T>(body: any): T {
	return (body && typeof body === 'object' && 'data' in body ? body.data : body) as T;
}

async function call(page: Page, token: string, method: 'get' | 'post' | 'put' | 'delete', url: string, data?: unknown) {
	const res = await page.request[method](url, {
		headers: { Authorization: `Bearer ${token}` },
		...(data === undefined ? {} : { data })
	});
	expect(res.ok(), `${method.toUpperCase()} ${url} -> ${res.status()} ${await res.text()}`).toBeTruthy();
	const text = await res.text();
	return text ? unwrap<any>(JSON.parse(text)) : null;
}

async function ensureCatalogModels(page: Page, token: string) {
	const backends = await call(page, token, 'get', '/api/backends');
	const backend = (backends as Array<{ id: string; name: string }>).find((b) => b.name === BACKEND_NAME);
	expect(backend, `backend "${BACKEND_NAME}" must be seeded`).toBeTruthy();
	await page.goto(`/admin?tab=backends&backend=${backend!.id}&view=catalog`);
	await expect(page.getByRole('button', { name: 'Refresh catalog' }).first()).toBeVisible({ timeout: 20000 });
	const row = page.locator('.dt-scroll > .dt-row:not(.dt-row--head)', { hasText: 'Fake Image' }).first();
	await expect(row).toBeVisible({ timeout: 20000 });
	if (!(await row.getByRole('switch').isChecked())) await row.getByRole('switch').click();
	await expect(row.getByRole('switch')).toBeChecked();
}

async function openFakeStudio(page: Page) {
	await page.addInitScript(() => localStorage.setItem('potionui-form-audience', 'advanced'));
	await page.goto('/generate');
	const choose = page.getByRole('button', { name: 'Choose a preset' });
	const needsPreset = await choose.waitFor({ state: 'visible', timeout: 10000 }).then(() => true, () => false);
	if (needsPreset) {
		await choose.click();
		await page.getByText(PRESET_NAME, { exact: true }).first().click();
		await page.getByRole('button', { name: /Use this preset|Keep selected/ }).click();
	}
	await expect(field(page, 'model')).toBeVisible({ timeout: 20000 });
}

async function pickModel(page: Page, label: string) {
	const swap = field(page, 'model').locator('button:visible', { hasText: 'Swap' }).first();
	if (await swap.isVisible().catch(() => false)) await swap.click();
	await field(page, 'model').locator('input').first().click();
	await page.getByText(label, { exact: true }).last().click();
	await page.waitForTimeout(BEAT);
}

async function typePrompt(page: Page, text: string) {
	const editor = page.locator('[contenteditable="true"]').first();
	await editor.click();
	await page.keyboard.type(text);
	await page.waitForTimeout(BEAT);
}

test.describe.configure({ mode: 'serial' });

test.describe('a daily generation limit', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	let token = '';
	let planId = '';
	let allowed = 1;
	let previousSettings: { default_plan_id: string | null; exempt_admins: boolean } | null = null;

	test.afterAll(async ({ browser }) => {
		if (!previousSettings) return;
		const page = await browser.newPage();
		try {
			await loginAsOwner(page);
			const adminToken = await ownerToken(page);
			await call(page, adminToken, 'put', '/api/admin/plans/settings', {
				default_plan_id: previousSettings.default_plan_id,
				exempt_admins: previousSettings.exempt_admins
			});
			if (planId) await call(page, adminToken, 'delete', `/api/admin/plans/${planId}`);
		} finally {
			await page.close();
		}
	});

	test('an admin sets a daily limit of 1 on the default plan', async ({ page }) => {
		test.setTimeout(120000);
		await loginAsOwner(page);
		token = await ownerToken(page);
		await ensureCatalogModels(page, token);

		previousSettings = await call(page, token, 'get', '/api/admin/plans/settings');
		const plan = await call(page, token, 'post', '/api/admin/plans', {
			name: PLAN_NAME,
			description: 'Playwright',
			limits: [{ kind: 'generations_per_day', value: 1 }]
		});
		planId = plan.id;
		await call(page, token, 'put', '/api/admin/plans/settings', { default_plan_id: planId, exempt_admins: false });

		const before = await call(page, token, 'get', '/api/me/limits');
		const usedToday = before.limits.find((row: any) => row.kind === 'generations_per_day')?.used ?? 0;
		allowed = usedToday + 1;
		await call(page, token, 'put', `/api/admin/plans/${planId}`, {
			name: PLAN_NAME,
			description: 'Playwright',
			limits: [{ kind: 'generations_per_day', value: allowed }]
		});

		const limits = await call(page, token, 'get', '/api/me/limits');
		const rows = limits.limits;
		expect(rows.some((row: any) => row.kind === 'generations_per_day' && row.limit === allowed && row.used === usedToday)).toBeTruthy();
	});

	test('the first generation runs and the second is stopped with the limit message', async ({ page }) => {
		test.setTimeout(180000);
		await loginAsOwner(page);
		await openFakeStudio(page);
		await pickModel(page, 'Fake Image');
		await typePrompt(page, 'a lighthouse at dusk');

		const generate = page.getByRole('button', { name: 'Generate', exact: true });
		await generate.click();
		await expect(page.getByRole('button', { name: 'Cancel generation' })).toBeVisible({ timeout: 15000 });
		await expect(page.getByText(/Daily limit reached/).first()).toBeVisible({ timeout: 60000 });
		await screenshot(page, JOURNEY, 'limit-reached-1440');

		await expect(page.getByText(/resets in \d+ (h|min)/).first()).toBeVisible();
		await expect(page.getByText('Ask your admin for more.').first()).toBeVisible();
		await expect(page.getByRole('button', { name: /Daily limit reached/ })).toBeDisabled();
	});

	test('the usage view and the account menu show the used limit', async ({ page }) => {
		test.setTimeout(60000);
		await loginAsOwner(page);
		await page.goto('/settings');
		const section = page.locator('#plan');
		await expect(section).toContainText('Generations today', { timeout: 15000 });
		await expect(section).toContainText(/Generations today\s*(\d+) \/ \1/);
		await expect(section.locator('[data-limit-row="storage_bytes"]')).toContainText('no limit');
		await screenshot(page, JOURNEY, 'settings-usage-1440');

		await page.getByRole('button', { name: 'Account menu' }).click();
		await expect(page.locator('[data-menu-usage]')).toContainText(/(\d+) \/ \1/);
		await expect(page.locator('[data-menu-storage]')).toContainText('no limit');
	});
});
