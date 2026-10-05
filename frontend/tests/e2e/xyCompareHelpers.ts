import { expect, type Page } from '@playwright/test';

export const BACKEND_NAME = 'E2E Fake Cloud';
export const PRESET_NAME = 'Fake Studio';
export const GRIDS = '/api/generations/grids';
export const BEAT = 400;

export const field = (page: Page, name: string) => page.locator(`[data-field-name="${name}"]`);

export interface Axis {
	field: string;
	type: string;
	label: string;
	values: Array<{ value: unknown; label: string }>;
}

export const QUALITY: Axis = {
	field: 'quality',
	type: 'select',
	label: 'Quality',
	values: [3, 5, 7].map((value) => ({ value, label: String(value) }))
};

export const ASPECT: Axis = {
	field: 'aspect_ratio',
	type: 'select',
	label: 'Aspect ratio',
	values: ['1:1', '16:9'].map((value) => ({ value, label: value }))
};

export const compareToggle = (page: Page) => page.locator('[data-compare-toggle]:visible').first();
export const compareDrawer = (page: Page) => page.getByRole('dialog', { name: 'Compare', exact: true });
export const compareGenerate = (page: Page, count: number) =>
	page.getByRole('button', { name: new RegExp(`^Generate ${count}\\b`) }).first();
export const generateMarkCount = (page: Page) => page.locator('.generate-button:visible .generate-count').first();

export async function openCompareDrawer(page: Page) {
	const drawer = compareDrawer(page);
	if (!(await drawer.isVisible())) {
		const toggle = compareToggle(page);
		await expect(toggle).toBeEnabled({ timeout: 20000 });
		await toggle.click();
	}
	await expect(drawer).toBeVisible({ timeout: 10000 });
	return drawer;
}

export async function closeCompareDrawer(page: Page) {
	const drawer = compareDrawer(page);
	await drawer.getByRole('button', { name: 'Close compare' }).click();
	await expect(drawer).toHaveCount(0, { timeout: 10000 });
}

export async function pickAxisField(page: Page, slot: 'X' | 'Y', target: string | RegExp) {
	const drawer = compareDrawer(page);
	const trigger =
		slot === 'X'
			? drawer.getByRole('button', { name: 'X axis field', exact: true })
			: drawer.getByRole('button', { name: /^(Y axis field|Add a second field)/ });
	await trigger.click();
	const picker = page.getByRole('dialog', { name: 'Pick a field' });
	await expect(picker).toBeVisible({ timeout: 10000 });
	const option =
		typeof target === 'string'
			? picker.locator(`[role="option"][data-field="${target}"]`)
			: picker.getByRole('option', { name: target }).first();
	await expect(option).toBeEnabled();
	await option.click();
	await expect(picker).toHaveCount(0);
}

export async function turnOffCompare(page: Page) {
	const toggle = compareToggle(page);
	await expect(toggle).toBeVisible({ timeout: 20000 });
	if ((await toggle.getAttribute('aria-pressed')) !== 'true') return;
	const drawer = await openCompareDrawer(page);
	await drawer.getByRole('button', { name: 'Turn off' }).click();
	await expect(toggle).toHaveAttribute('aria-pressed', 'false');
	if (await drawer.isVisible()) await closeCompareDrawer(page);
}

export async function apiGet(page: Page, url: string, token: string) {
	const res = await page.request.get(url, { headers: { Authorization: `Bearer ${token}` } });
	expect(res.ok(), `GET ${url} -> ${res.status()}`).toBeTruthy();
	return res.json();
}

export async function backendId(page: Page, token: string): Promise<string> {
	const list = await apiGet(page, '/api/backends', token);
	const backend = (list.data as Array<{ id: string; name: string }>).find((b) => b.name === BACKEND_NAME);
	expect(backend, `backend "${BACKEND_NAME}" must be seeded`).toBeTruthy();
	return backend!.id;
}

export async function setKnob(page: Page, token: string, id: string, knobs: Record<string, unknown>) {
	const current = await apiGet(page, `/api/backends/${id}`, token);
	const res = await page.request.put(`/api/backends/${id}`, {
		headers: { Authorization: `Bearer ${token}` },
		data: { ...current.data, ...knobs }
	});
	expect(res.ok(), `PUT backend -> ${res.status()} ${await res.text()}`).toBeTruthy();
}

export async function enableFakeModels(page: Page, token: string) {
	const id = await backendId(page, token);
	await page.goto(`/admin?tab=backends&backend=${id}&view=catalog`);
	await expect(page.getByRole('button', { name: 'Refresh catalog' }).first()).toBeVisible({ timeout: 20000 });
	const row = (label: string) => page.locator('.dt-scroll > .dt-row:not(.dt-row--head)', { hasText: label }).first();
	await expect(row('Fake Image')).toBeVisible({ timeout: 20000 });
	for (const label of ['Fake Image', 'Fake Lite']) {
		const toggle = row(label).getByRole('switch');
		if (!(await toggle.isChecked())) {
			await toggle.click();
			await expect(toggle).toBeChecked();
		}
	}
}

export async function openFakeStudio(page: Page) {
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
	await turnOffCompare(page);
}

export async function pickModel(page: Page, label: string) {
	const swap = field(page, 'model').locator('button:visible', { hasText: 'Swap' }).first();
	if (await swap.isVisible().catch(() => false)) await swap.click();
	await field(page, 'model').locator('input').first().click();
	await page.getByText(label, { exact: true }).last().click();
	await page.waitForTimeout(BEAT);
}

export async function typePrompt(page: Page, text: string) {
	const editor = page.locator('[contenteditable="true"]').first();
	await editor.click();
	await page.keyboard.type(text);
	await page.waitForTimeout(BEAT);
}

export async function captureStartRequest(page: Page): Promise<Record<string, any>> {
	await openFakeStudio(page);
	await pickModel(page, 'Fake Image');
	await typePrompt(page, 'a lighthouse on a cliff at dusk');
	const request = page.waitForRequest(
		(candidate) => candidate.method() === 'POST' && (candidate.postData() ?? '').includes('"form_data"')
	);
	await page.getByRole('button', { name: 'Generate', exact: true }).click();
	const sent = JSON.parse((await request).postData() ?? '{}');
	await expect(page.getByRole('button', { name: 'Generate', exact: true })).toBeVisible({ timeout: 90000 });
	return sent;
}

export async function createGrid(
	page: Page,
	token: string,
	request: Record<string, any>,
	x: Axis,
	y: Axis | null,
	lockSeed = true
): Promise<string> {
	const res = await page.request.post(GRIDS, {
		headers: { Authorization: `Bearer ${token}` },
		data: { request, x_axis: x, y_axis: y, lock_seed: lockSeed }
	});
	expect(res.ok(), `POST ${GRIDS} -> ${res.status()} ${await res.text()}`).toBeTruthy();
	return (await res.json()).data.id as string;
}

export async function waitGridSettled(page: Page, token: string, gridId: string, timeout = 120000) {
	await expect
		.poll(
			async () => {
				const grid = (await apiGet(page, `${GRIDS}/${gridId}`, token)).data;
				const open = (grid.cells as Array<{ status: string }>).filter((cell) =>
					['queued', 'running'].includes(cell.status)
				);
				return open.length;
			},
			{ timeout, intervals: [1000] }
		)
		.toBe(0);
	return (await apiGet(page, `${GRIDS}/${gridId}`, token)).data;
}

export const gridStackCard = (page: Page, gridId: string) =>
	page
		.locator('[data-history-card]')
		.filter({ has: page.locator(`[data-testid="grid-stack-chip"][data-grid-id="${gridId}"]`) });

export const gridCellChips = (page: Page, gridId: string) =>
	page.locator(`[data-testid="grid-cell-chip"][data-grid-id="${gridId}"]`);

export async function openGridFromHistory(page: Page, gridId: string) {
	await page.goto('/history');
	const stack = gridStackCard(page, gridId);
	await expect(stack).toHaveCount(1, { timeout: 20000 });
	await stack.locator('.media-zoom').click();
	await expect(page.getByTestId('history-grid-view')).toBeVisible({ timeout: 15000 });
}
