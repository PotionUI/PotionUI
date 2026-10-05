import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';
import { fixturePng } from './imageEditorHelpers';

const JOURNEY = 'plans-upload-file-size';
const PLAN_NAME = 'E2E upload 1 KB';
const LIMIT_BYTES = 1024;

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

const MESSAGE = /This file is [\d.]+ (KB|MB); your plan allows files up to 1 KB\./;

test.describe.configure({ mode: 'serial' });

test.describe('a per-file upload size limit', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	let token = '';
	let planId = '';
	let previousSettings: { default_plan_id: string | null; exempt_admins: boolean } | null = null;
	const png = fixturePng(96);

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

	test('an admin sets a 1 KB largest upload on the default plan', async ({ page }) => {
		test.setTimeout(60000);
		expect(png.length).toBeGreaterThan(LIMIT_BYTES);
		await loginAsOwner(page);
		token = await ownerToken(page);

		previousSettings = await call(page, token, 'get', '/api/admin/plans/settings');
		const plan = await call(page, token, 'post', '/api/admin/plans', {
			name: PLAN_NAME,
			description: 'Playwright',
			limits: [{ kind: 'upload_file_size', value: LIMIT_BYTES }]
		});
		planId = plan.id;
		await call(page, token, 'put', '/api/admin/plans/settings', { default_plan_id: planId, exempt_admins: false });

		const mine = await call(page, token, 'get', '/api/me/limits');
		const row = mine.limits.find((entry: any) => entry.kind === 'upload_file_size');
		expect(row.limit).toBe(LIMIT_BYTES);
		expect(row.used).toBeNull();
		expect(row.percent).toBeNull();
		expect(typeof mine.usage.storage_bytes).toBe('number');
	});

	test('the server refuses an oversize upload with the plain message', async ({ page }) => {
		test.setTimeout(60000);
		await loginAsOwner(page);
		const res = await page.request.post('/api/media/upload', {
			headers: { Authorization: `Bearer ${token}` },
			multipart: { file: { name: 'big.png', mimeType: 'image/png', buffer: png } }
		});
		expect(res.status()).toBe(403);
		const detail = (await res.json()).detail;
		expect(detail.code).toBe('upload_file_size_exceeded');
		expect(detail.limit).toBe(LIMIT_BYTES);
		expect(detail.incoming).toBe(png.length);
		expect(detail.message).toMatch(MESSAGE);
	});

	test('the library shows the refusal and nothing is added', async ({ page }) => {
		test.setTimeout(60000);
		await loginAsOwner(page);
		await page.goto('/library');
		await expect(page.locator('input[type="file"]').first()).toBeAttached({ timeout: 20000 });
		await page.locator('input[type="file"]').first().setInputFiles({ name: 'big.png', mimeType: 'image/png', buffer: png });
		await expect(page.getByText(MESSAGE).first()).toBeVisible({ timeout: 15000 });
		await screenshot(page, JOURNEY, 'library-refusal-1440');
	});

	test('Plan and usage lists the largest upload and storage with no bar for the per-file limit', async ({ page }) => {
		test.setTimeout(60000);
		await loginAsOwner(page);
		await page.goto('/settings');
		const section = page.locator('#plan');
		await expect(section).toContainText('Largest upload', { timeout: 15000 });
		await expect(section).toContainText('1 KB');
		await expect(section).toContainText('per file');
		await expect(section.locator('[data-limit-row="upload_file_size"] [role="progressbar"]')).toHaveCount(0);
		await expect(section.locator('[data-limit-row="storage_bytes"]')).toContainText('no limit');

		await page.getByRole('button', { name: 'Account menu' }).click();
		await expect(page.locator('[data-menu-storage]')).toContainText('no limit');
		await expect(page.locator('[data-menu-usage]')).toHaveCount(0);
	});
});
