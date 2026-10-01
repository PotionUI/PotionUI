import { test, expect, type Page, type Route } from '@playwright/test';
import { loginAsOwner, ownerToken, shotPath, waitForFormPublished } from './helpers';
import { installAndSelectImagePreset } from './presetPreamble';

const JOURNEY = 'formulas-drawer';

async function json(route: Route, data: unknown, status = 200) {
	await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
}

async function openForm(page: Page) {
	await loginAsOwner(page);
	const preset = await installAndSelectImagePreset(page);
	expect(preset, 'a native image preset must exist').toBeTruthy();
	await waitForFormPublished(page);
	return preset!;
}

function resolutionTrigger(page: Page) {
	return page.locator('[data-field-name="resolution"] button').first();
}

async function currentResolution(page: Page): Promise<string> {
	return (await resolutionTrigger(page).innerText()).trim();
}

async function pickResolution(page: Page, index: number): Promise<string> {
	await resolutionTrigger(page).click();
	await page.getByRole('option').nth(index).click();
	await expect(page.getByRole('option')).toHaveCount(0);
	return currentResolution(page);
}

async function saveSizeFormula(page: Page, name: string) {
	const drawer = page.getByRole('dialog', { name: 'Formulas' });
	await drawer.getByRole('button', { name: 'Save as formula' }).click();
	const boxes = drawer.getByRole('checkbox', { name: /^Keep / });
	for (let i = 0; i < (await boxes.count()); i++) {
		const box = boxes.nth(i);
		const want = (await box.getAttribute('aria-label')) === 'Keep Size';
		if (want) await box.check();
		else await box.uncheck();
	}
	await drawer.locator('#formula-name').fill(name);
	await drawer.getByRole('button', { name: /^Save/ }).last().click();
	await expect(drawer.locator(`[data-row-id]`, { hasText: name })).toBeVisible();
}

async function noHorizontalScroll(page: Page) {
	const overflow = await page.evaluate(
		() => document.documentElement.scrollWidth - document.documentElement.clientWidth
	);
	expect(overflow).toBeLessThanOrEqual(0);
}

test.describe('Formulas drawer', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('shows no Formulas button when the preset declares none', async ({ page }) => {
		await page.route(/\/api\/presets\/[^/]+\/form(\?|$)/, async (route) => {
			const response = await route.fetch();
			const body = await response.json();
			delete body.data.formulas;
			await json(route, body);
		});
		await openForm(page);
		await expect(page.getByTestId('formulas-button')).toHaveCount(0);
	});

	test('saves, manages, previews, applies and undoes a formula', async ({ page }) => {
		await openForm(page);
		const button = page.getByTestId('formulas-button');
		await expect(button).toBeVisible();

		const saved = await pickResolution(page, 1);
		await button.click();
		const drawer = page.getByRole('dialog', { name: 'Formulas' });
		await expect(drawer.getByText('No formulas for this mode yet')).toBeVisible();
		await expect(drawer).not.toContainText('Prompt, media and seed are never included');
		await page.screenshot({ path: shotPath(JOURNEY, 'empty') });

		await drawer.getByRole('button', { name: 'Save as formula' }).click();
		const submit = drawer.getByRole('button', { name: /^Save/ }).last();
		await expect(submit).toBeDisabled();
		await drawer.locator('#formula-name').fill('Wide frame');
		await page.screenshot({ path: shotPath(JOURNEY, 'save') });
		await drawer.getByRole('button', { name: 'Cancel' }).click();
		await saveSizeFormula(page, 'Wide frame');
		await expect(drawer.locator('[data-highlight]')).toHaveCount(1);
		await expect(page.locator('[data-testid="toast"], .toast')).toHaveCount(0);
		await expect(button.locator('span.absolute')).toHaveText('1');
		await page.screenshot({ path: shotPath(JOURNEY, 'list') });

		await drawer.getByLabel('Search formulas').fill('zzz');
		await expect(drawer.getByText('No formulas match')).toBeVisible();
		await drawer.getByLabel('Search formulas').fill('wide');
		await expect(drawer.getByText('Wide frame')).toBeVisible();
		await drawer.getByLabel('Search formulas').fill('');

		await drawer.getByRole('button', { name: 'More actions for Wide frame' }).click();
		await page.getByRole('menuitem', { name: 'Duplicate' }).click();
		await expect(drawer.locator('[data-row-id]', { hasText: 'Wide frame copy' })).toBeVisible();
		await drawer.getByRole('button', { name: 'More actions for Wide frame copy' }).click();
		await page.getByRole('menuitem', { name: 'Rename' }).click();
		await drawer.getByLabel('Rename Wide frame copy').fill('Spare');
		await drawer.getByLabel('Rename Wide frame copy').press('Enter');
		await expect(drawer.locator('[data-row-id]', { hasText: 'Spare' })).toBeVisible();
		await drawer.getByRole('button', { name: 'More actions for Spare' }).click();
		await page.getByRole('menuitem', { name: 'Delete' }).click();
		await page.getByRole('alertdialog').getByRole('button', { name: 'Confirm' }).click();
		await expect(drawer.locator('[data-row-id]', { hasText: 'Spare' })).toHaveCount(0);
		await expect(drawer.locator('[data-row-id]')).toHaveCount(1);

		await page.keyboard.press('Escape');
		await expect(drawer).toHaveCount(0);
		const other = await pickResolution(page, 3);
		expect(other).not.toBe(saved);

		await button.click();
		await drawer.getByText('Wide frame').click();
		const chips = drawer.getByTestId('formula-plan-chips');
		await expect(chips).toContainText('1');
		await expect(chips).toContainText('will change');
		await page.screenshot({ path: shotPath(JOURNEY, 'preview') });

		const apply = drawer.getByRole('button', { name: /^Apply/ });
		await drawer.getByRole('checkbox', { name: 'Apply Resolution' }).uncheck();
		await expect(apply).toBeDisabled();
		await drawer.getByRole('checkbox', { name: 'Apply Resolution' }).check();
		await expect(apply).toBeEnabled();

		await apply.click();
		await expect(drawer).toHaveCount(0);
		const bar = page.getByTestId('formula-applied-bar');
		await expect(bar).toContainText('Wide frame');
		await expect.poll(() => currentResolution(page)).toBe(saved);
		await expect(page.getByTestId('formula-previous-value').first()).toBeVisible();
		await expect(page.getByTestId('formula-tab-count').first()).toHaveText('1');
		await page.screenshot({ path: shotPath(JOURNEY, 'applied') });

		await bar.getByRole('button', { name: 'Undo' }).click();
		await expect(bar).toHaveCount(0);
		await expect.poll(() => currentResolution(page)).toBe(other);
	});

	test('keeps the drawer open after applying when Keep open is on', async ({ page }) => {
		await openForm(page);
		const button = page.getByTestId('formulas-button');
		const saved = await pickResolution(page, 1);
		await button.click();
		const drawer = page.getByRole('dialog', { name: 'Formulas' });
		await saveSizeFormula(page, 'Kept');
		await drawer.getByRole('switch', { name: 'Keep open' }).click();
		await page.keyboard.press('Escape');
		const other = await pickResolution(page, 3);

		await button.click();
		await drawer.getByText('Kept').click();
		await drawer.getByRole('button', { name: /^Apply/ }).click();
		await expect(drawer).toBeVisible();
		await expect(drawer.getByTestId('formula-applied-bar')).toContainText('Kept');
		await expect.poll(() => currentResolution(page)).toBe(saved);
		await drawer.getByRole('button', { name: 'Undo' }).click();
		await expect.poll(() => currentResolution(page)).toBe(other);
		await page.screenshot({ path: shotPath(JOURNEY, 'keep-open') });
	});

	test('refreshes the preview when the form changes while it is open', async ({ page }) => {
		await openForm(page);
		const button = page.getByTestId('formulas-button');
		await pickResolution(page, 1);
		await button.click();
		const drawer = page.getByRole('dialog', { name: 'Formulas' });
		await saveSizeFormula(page, 'Live');
		await drawer.getByRole('switch', { name: 'Keep open' }).click();
		await page.keyboard.press('Escape');
		await pickResolution(page, 3);
		await button.click();
		await drawer.getByText('Live').click();
		await expect(drawer.getByTestId('formula-plan-chips')).toContainText('will change');
		await resolutionTrigger(page).click();
		await page.getByRole('option').nth(1).click();
		await expect(drawer.getByTestId('formula-plan-chips')).toContainText('already match');
		await expect(drawer.getByText('The form already matches this formula.')).toBeVisible();
	});

	test('opening sessions closes Formulas and the other way round', async ({ page }) => {
		await openForm(page);
		const button = page.getByTestId('formulas-button');
		const formulas = page.getByRole('dialog', { name: 'Formulas' });
		const sessions = page.getByRole('dialog', { name: 'Sessions' });
		await button.click();
		await expect(formulas).toBeVisible();
		await page.locator('button[aria-label="Session"]').first().click();
		await expect(sessions).toBeVisible();
		await expect(formulas).toHaveCount(0);
		await button.evaluate((el) => (el as HTMLElement).click());
		await expect(formulas).toBeVisible();
		await expect(sessions).toHaveCount(0);
	});

	test('drops the applied bar when another preset is selected', async ({ page }) => {
		await loginAsOwner(page);
		const first = await installAndSelectImagePreset(page);
		expect(first).toBeTruthy();
		const token = await ownerToken(page);
		const headers = { Authorization: `Bearer ${token}` };
		const list = await (await page.request.get('/api/presets?include_uninstalled=true', { headers })).json();
		const second = (list.data as Array<{ id: string; name: string; engine?: string; category?: string; installed?: boolean }>).find(
			(p) => p.id !== first!.id && p.engine === 'native' && p.category === 'image'
		);
		test.skip(!second, 'a second native image preset is needed to switch presets');
		if (!second) return;
		if (!second.installed) await page.request.post(`/api/presets/${second.id}/install`, { headers });
		const me = await (await page.request.get('/api/auth/me', { headers })).json();
		await page.request.post(`/api/presets/${second.id}/assign`, { headers, data: { user_ids: [me.data.id] } });

		await page.reload();
		const chooser = page.locator('button[aria-haspopup="dialog"]', { hasText: 'Choose a preset' });
		if (await chooser.isVisible({ timeout: 5000 }).catch(() => false)) {
			await chooser.click();
			await page.locator('[role="listbox"][aria-label="Presets"]').getByText(first!.name, { exact: true }).click();
			await page.getByRole('button', { name: /Use this preset|Keep selected/ }).click();
		}
		await waitForFormPublished(page);
		const button = page.getByTestId('formulas-button');
		await pickResolution(page, 1);
		await button.click();
		const drawer = page.getByRole('dialog', { name: 'Formulas' });
		await saveSizeFormula(page, 'Switch me');
		await page.keyboard.press('Escape');
		await pickResolution(page, 3);
		await button.click();
		await drawer.getByText('Switch me').click();
		await drawer.getByRole('button', { name: /^Apply/ }).click();
		await expect(page.getByTestId('formula-applied-bar')).toBeVisible();

		await page.locator('button[aria-haspopup="dialog"]', { hasText: first!.name }).first().click();
		await page.locator('[role="listbox"][aria-label="Presets"]').getByText(second.name, { exact: true }).click();
		await page.getByRole('button', { name: /Use this preset|Keep selected/ }).click();
		await waitForFormPublished(page);
		await expect(page.getByTestId('formula-applied-bar')).toHaveCount(0);
	});
});

test.describe('Formulas drawer errors', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('shows the server message inline when a name is taken', async ({ page }) => {
		await page.route(/\/api\/formulas(\?.*)?$/, async (route) => {
			if (route.request().method() !== 'POST') return route.fallback();
			await json(
				route,
				{ detail: { error: 'formula_name_exists', message: "A formula named 'Taken' already exists for this preset and mode" } },
				409
			);
		});
		await openForm(page);
		await page.getByTestId('formulas-button').click();
		const drawer = page.getByRole('dialog', { name: 'Formulas' });
		await drawer.getByRole('button', { name: 'Save as formula' }).click();
		await drawer.getByRole('checkbox', { name: 'Keep Size' }).check();
		await drawer.locator('#formula-name').fill('Taken');
		await drawer.getByRole('button', { name: /^Save/ }).last().click();
		await expect(drawer.getByRole('alert')).toContainText("already exists");
		await expect(drawer.locator('#formula-name')).toHaveValue('Taken');
		await page.screenshot({ path: shotPath(JOURNEY, 'name-taken') });
	});

	test('leaves the form untouched when the plan is not found', async ({ page }) => {
		await openForm(page);
		await pickResolution(page, 1);
		await page.getByTestId('formulas-button').click();
		const drawer = page.getByRole('dialog', { name: 'Formulas' });
		await saveSizeFormula(page, 'Gone soon');
		await page.keyboard.press('Escape');
		const before = await pickResolution(page, 3);
		await page.route(/\/api\/formulas\/[^/]+\/plan$/, (route) =>
			json(route, { detail: { error: 'formula_not_found', message: 'Formula not found' } }, 404)
		);
		await page.getByTestId('formulas-button').click();
		await drawer.getByText('Gone soon').click();
		await expect(drawer.getByRole('alert')).toContainText('Formula not found');
		await expect(drawer.getByRole('button', { name: /^Apply/ })).toBeDisabled();
		await drawer.getByRole('button', { name: 'Cancel' }).click();
		await page.keyboard.press('Escape');
		expect(await currentResolution(page)).toBe(before);
		await expect(page.getByTestId('formula-applied-bar')).toHaveCount(0);
	});

	test('names the setting in every skipped row', async ({ page }) => {
		await openForm(page);
		await pickResolution(page, 1);
		await page.getByTestId('formulas-button').click();
		const drawer = page.getByRole('dialog', { name: 'Formulas' });
		await saveSizeFormula(page, 'Skippy');
		await page.keyboard.press('Escape');
		await pickResolution(page, 3);
		await page.route(/\/api\/formulas\/[^/]+\/plan$/, (route) =>
			json(route, {
				success: true,
				data: {
					changes: [],
					same: [],
					skips: [
						{ name: 'sampler_old', label: 'sampler_old', group_id: 'speed', code: 'field_removed', reason: 'This setting is not in the form any more.', detail: {} }
					]
				}
			})
		);
		await page.getByTestId('formulas-button').click();
		await drawer.getByText('Skippy').click();
		await expect(drawer.getByTestId('formula-skips')).toContainText('sampler_old');
		await page.screenshot({ path: shotPath(JOURNEY, 'preview-skip') });
	});
});

test.describe('Formulas drawer on a phone', () => {
	test.use({ viewport: { width: 390, height: 844 }, hasTouch: true });

	test('opens full screen from the preset sheet without horizontal scroll and lands in Settings after applying', async ({
		page
	}) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		await openForm(page);
		await pickResolution(page, 1);
		await page.getByTestId('formulas-button').click();
		await saveSizeFormula(
			page,
			'A very long formula name that must truncate instead of pushing the sheet'
		);
		await page.keyboard.press('Escape');
		await pickResolution(page, 3);
		await page.setViewportSize({ width: 390, height: 844 });
		await page.getByRole('button', { name: 'Open preset and session' }).click();

		const button = page.getByTestId('formulas-button');
		await expect(button).toBeVisible();
		await page.waitForTimeout(500);
		await page.screenshot({ path: shotPath(JOURNEY, 'phone-sheet') });
		await button.click();

		const drawer = page.getByRole('dialog', { name: 'Formulas' });
		await expect(drawer).toBeVisible();
		const box = await drawer.boundingBox();
		expect(box?.width).toBeGreaterThanOrEqual(388);
		await noHorizontalScroll(page);
		await page.screenshot({ path: shotPath(JOURNEY, 'phone-list') });

		await drawer.locator('[data-row-id] button[data-nav]').first().click();
		await expect(drawer.getByTestId('formula-plan-chips')).toBeVisible();
		await noHorizontalScroll(page);
		await page.screenshot({ path: shotPath(JOURNEY, 'phone-preview') });

		await drawer.getByRole('button', { name: /^Apply/ }).click();
		await expect(drawer).toHaveCount(0);
		await expect(page.getByRole('dialog', { name: 'Settings' })).toBeVisible();
		await expect(page.getByTestId('formula-applied-bar')).toBeVisible();
		await noHorizontalScroll(page);
		await page.screenshot({ path: shotPath(JOURNEY, 'phone-applied') });
	});
});
