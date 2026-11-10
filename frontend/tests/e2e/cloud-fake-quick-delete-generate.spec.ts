import { test, expect, type Page, type Request } from '@playwright/test';
import { screenshot } from './helpers';
import { prepare, setKnobs, api } from './elixirs-fixtures';
import { openFakeStudio } from './fakeStudio';

const JOURNEY = 'cloud-fake-quick-delete-generate';

const field = (page: Page, name: string) => page.locator(`[data-field-name="${name}"]`);
const alert = (page: Page) => page.getByRole('alertdialog');
const deleteButton = (page: Page) => page.getByTestId('workbench-delete-generation');
const dockedPane = (page: Page) => page.getByTestId('workbench-pane');

interface Traffic {
	calls: string[];
}

function watch(page: Page): Traffic {
	const traffic: Traffic = { calls: [] };
	page.on('request', (request: Request) => {
		const url = new URL(request.url());
		if (request.method() === 'DELETE' && url.pathname.startsWith('/api/generations/history/')) traffic.calls.push(`DELETE ${url.pathname}`);
		if (request.method() === 'POST' && /^\/api\/generations\/[^/]+\/cancel$/.test(url.pathname)) traffic.calls.push(`CANCEL ${url.pathname}`);
	});
	return traffic;
}

async function leaveFocus(page: Page) {
	await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
	await expect.poll(() => page.evaluate(() => document.activeElement === document.body)).toBe(true);
}

async function startGeneration(page: Page, prompt: string) {
	const editor = page.locator('[contenteditable="true"]').first();
	await editor.click();
	await page.keyboard.press('ControlOrMeta+a');
	await page.keyboard.type(prompt);
	const started = page.waitForResponse((response) => {
		const url = new URL(response.url());
		return response.request().method() === 'POST' && url.pathname === '/api/generations/start';
	});
	await page.getByRole('button', { name: 'Generate', exact: true }).click();
	const body = await (await started).json();
	const id = body?.data?.generation_id ?? body?.data?.id ?? body?.generation_id;
	expect(id, JSON.stringify(body)).toBeTruthy();
	return id as string;
}

async function finishedGeneration(page: Page, prompt: string) {
	const id = await startGeneration(page, prompt);
	await expect(deleteButton(page)).toBeVisible({ timeout: 60000 });
	return id;
}

async function exists(page: Page, token: string, id: string) {
	const response = await page.request.get(`/api/generations/history/${id}`, { headers: { Authorization: `Bearer ${token}` } });
	return response.status() === 200;
}

test.describe.configure({ mode: 'serial' });

test.describe('deleting the generation shown on the generate page', () => {
	let token = '';

	test.beforeEach(async ({ page }) => {
		test.setTimeout(150000);
		await page.setViewportSize({ width: 1440, height: 900 });
		token = (await prepare(page)).token;
	});

	test('Delete does nothing in a tab without a generation', async ({ page }) => {
		const traffic = watch(page);
		await page.goto('/generate');
		await expect(page.getByRole('button', { name: 'Choose a preset' })).toBeVisible({ timeout: 20000 });
		await leaveFocus(page);
		await page.keyboard.press('Delete');
		await page.waitForTimeout(500);
		await expect(alert(page)).toHaveCount(0);
		expect(traffic.calls).toEqual([]);
		await screenshot(page, JOURNEY, 'empty-tab-delete-ignored-1440');
	});

	test('the Workbench button deletes the whole generation after one confirm and empties the Workbench', async ({ page }) => {
		const traffic = watch(page);
		await openFakeStudio(page);
		const id = await finishedGeneration(page, 'a glass potion on a wooden table');
		const actions = await dockedPane(page).locator('button[aria-label]').evaluateAll((buttons) => buttons.map((b) => b.getAttribute('aria-label')));
		expect(actions.indexOf('Delete generation')).toBe(actions.indexOf('Download file') + 1);
		await screenshot(page, JOURNEY, 'workbench-with-delete-1440');

		await deleteButton(page).click();
		await expect(alert(page)).toContainText('Delete Generation', { timeout: 15000 });
		await expect(alert(page)).toContainText('1 generated file(s)');
		await screenshot(page, JOURNEY, 'delete-dialog-1440');
		await alert(page).getByRole('button', { name: 'Delete', exact: true }).click();

		await expect(dockedPane(page).getByTestId('workbench-empty')).toHaveAttribute('data-state', 'empty', { timeout: 15000 });
		await expect(alert(page)).toHaveCount(0);
		expect(traffic.calls).toEqual([`DELETE /api/generations/history/${id}`]);
		expect(await exists(page, token, id)).toBe(false);
		await screenshot(page, JOURNEY, 'workbench-emptied-1440');
	});

	test('the Delete key deletes the shown generation after one confirm, also at phone width', async ({ page }) => {
		const traffic = watch(page);
		await openFakeStudio(page);
		const first = await finishedGeneration(page, 'a copper kettle');
		await leaveFocus(page);
		await page.keyboard.press('Delete');
		await expect(alert(page)).toContainText('1 generated file(s)', { timeout: 15000 });
		await alert(page).getByRole('button', { name: 'Delete', exact: true }).click();
		await expect(dockedPane(page).getByTestId('workbench-empty')).toHaveAttribute('data-state', 'empty', { timeout: 15000 });
		expect(traffic.calls).toEqual([`DELETE /api/generations/history/${first}`]);
		expect(await exists(page, token, first)).toBe(false);

		const second = await finishedGeneration(page, 'a brass compass');
		await page.setViewportSize({ width: 390, height: 844 });
		await expect(deleteButton(page)).toHaveCount(0);
		await screenshot(page, JOURNEY, 'phone-result-390');
		await leaveFocus(page);
		await page.keyboard.press('Delete');
		await expect(alert(page)).toContainText('1 generated file(s)', { timeout: 15000 });
		await screenshot(page, JOURNEY, 'delete-dialog-390');
		await alert(page).getByRole('button', { name: 'Delete', exact: true }).click();
		await expect(alert(page)).toHaveCount(0, { timeout: 15000 });
		expect(traffic.calls).toEqual([`DELETE /api/generations/history/${first}`, `DELETE /api/generations/history/${second}`]);
		expect(await exists(page, token, second)).toBe(false);
		await screenshot(page, JOURNEY, 'phone-emptied-390');
	});

	test('Delete edits the prompt while typing and opens nothing while a dialog or list is open', async ({ page }) => {
		const traffic = watch(page);
		await openFakeStudio(page);
		const id = await finishedGeneration(page, 'a silver spoon');

		const editor = page.locator('[contenteditable="true"]').first();
		await editor.click();
		await page.keyboard.press('ControlOrMeta+a');
		await page.keyboard.type('abc');
		await page.keyboard.press('ArrowLeft');
		await page.keyboard.press('Delete');
		await expect(editor).toHaveText(/^\s*ab\s*$/);
		await page.waitForTimeout(400);
		await expect(alert(page)).toHaveCount(0);

		await page.keyboard.press('ControlOrMeta+a');
		await page.keyboard.type('glass {red|blue}');
		await page.keyboard.press('End');
		const chip = editor.locator('[data-group-raw]');
		await expect(chip).toHaveCount(1, { timeout: 3000 });
		await page.keyboard.press('Backspace');
		await expect(chip).toHaveCount(0);
		await expect(editor).toContainText('glass');
		await page.waitForTimeout(400);
		await expect(alert(page)).toHaveCount(0);

		await leaveFocus(page);
		const shortcuts = page.getByRole('dialog', { name: 'Keyboard Shortcuts' });
		await expect(async () => {
			if (!(await shortcuts.isVisible())) await page.keyboard.press('?');
			await expect(shortcuts).toBeVisible({ timeout: 500 });
		}).toPass({ timeout: 10000 });
		await page.keyboard.press('Delete');
		await page.waitForTimeout(400);
		await expect(alert(page)).toHaveCount(0);
		await screenshot(page, JOURNEY, 'shortcuts-open-delete-ignored-1440');
		await page.keyboard.press('Escape');
		await expect(shortcuts).toHaveCount(0);

		await field(page, 'aspect_ratio').locator('button[aria-haspopup="listbox"]').click();
		await expect(page.getByRole('listbox').first()).toBeVisible();
		await page.keyboard.press('Delete');
		await page.waitForTimeout(400);
		await expect(alert(page)).toHaveCount(0);
		await screenshot(page, JOURNEY, 'list-open-delete-ignored-1440');
		await page.keyboard.press('Escape');

		expect(traffic.calls).toEqual([]);
		expect(await exists(page, token, id)).toBe(true);
		await expect(deleteButton(page)).toBeVisible();
	});

	test('the floating Workbench is a view of the same result, so Delete and its own button both delete it', async ({ page }) => {
		const traffic = watch(page);
		await openFakeStudio(page);
		const first = await finishedGeneration(page, 'a pewter goblet');
		await leaveFocus(page);
		await page.keyboard.press('w');
		const floating = page.getByRole('dialog', { name: 'Workbench' });
		await expect(floating).toBeVisible({ timeout: 10000 });
		await expect(floating.getByTestId('workbench-delete-generation')).toBeVisible();
		await screenshot(page, JOURNEY, 'floating-workbench-1440');

		await page.keyboard.press('Delete');
		await expect(alert(page)).toContainText('1 generated file(s)', { timeout: 15000 });
		await screenshot(page, JOURNEY, 'floating-delete-key-dialog-1440');
		await alert(page).getByRole('button', { name: 'Cancel' }).click();
		await expect(alert(page)).toHaveCount(0);
		await expect(floating).toBeVisible();
		expect(traffic.calls).toEqual([]);

		await floating.getByTestId('workbench-delete-generation').click();
		await expect(alert(page)).toContainText('1 generated file(s)', { timeout: 15000 });
		await alert(page).getByRole('button', { name: 'Delete', exact: true }).click();
		await expect(floating.getByTestId('workbench-empty')).toBeVisible({ timeout: 15000 });
		expect(traffic.calls).toEqual([`DELETE /api/generations/history/${first}`]);
		expect(await exists(page, token, first)).toBe(false);
		await screenshot(page, JOURNEY, 'floating-emptied-1440');
	});

	test('a running generation is cancelled, then deleted; when the cancel fails nothing is deleted', async ({ page }) => {
		const traffic = watch(page);
		const backendId = await setKnobs(page, token, { duration_seconds: 40 });
		try {
			await openFakeStudio(page);
			const id = await startGeneration(page, 'a slow sunrise over the sea');
			await expect(page.getByRole('button', { name: 'Cancel generation' })).toBeVisible({ timeout: 15000 });
			await expect(deleteButton(page)).toHaveCount(0);

			await page.route(/\/api\/generations\/[^/]+\/cancel$/, (route) =>
				route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ success: false, message: 'The backend would not stop it.' }) })
			);
			await leaveFocus(page);
			await page.keyboard.press('Delete');
			await expect(alert(page)).toContainText('Delete Generation', { timeout: 15000 });
			await screenshot(page, JOURNEY, 'running-delete-dialog-1440');
			await alert(page).getByRole('button', { name: 'Delete', exact: true }).click();
			await expect(page.getByText('The backend would not stop it.').first()).toBeVisible({ timeout: 15000 });
			await screenshot(page, JOURNEY, 'cancel-refused-1440');
			expect(traffic.calls).toEqual([`CANCEL /api/generations/${id}/cancel`]);
			expect(await exists(page, token, id)).toBe(true);
			if (await alert(page).count()) await alert(page).getByRole('button', { name: 'Cancel' }).click();
			await page.unroute(/\/api\/generations\/[^/]+\/cancel$/);

			await leaveFocus(page);
			await page.keyboard.press('Delete');
			await expect(alert(page)).toContainText('Delete Generation', { timeout: 15000 });
			await alert(page).getByRole('button', { name: 'Delete', exact: true }).click();
			await expect(alert(page)).toHaveCount(0, { timeout: 20000 });
			await expect(dockedPane(page).getByTestId('workbench-empty')).toHaveAttribute('data-state', 'empty', { timeout: 15000 });
			expect(traffic.calls).toEqual([
				`CANCEL /api/generations/${id}/cancel`,
				`CANCEL /api/generations/${id}/cancel`,
				`DELETE /api/generations/history/${id}`
			]);
			expect(await exists(page, token, id)).toBe(false);
			await screenshot(page, JOURNEY, 'running-deleted-1440');
		} finally {
			await api(page, token, 'put', `/api/backends/${backendId}`, {
				...(await api(page, token, 'get', `/api/backends/${backendId}`)).data,
				duration_seconds: 1.0
			});
		}
	});
});
