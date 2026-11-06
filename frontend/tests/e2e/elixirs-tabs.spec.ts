import { test, expect, type Page } from '@playwright/test';
import { addGenerationTab, loginAsOwner, ownerToken, screenshot } from './helpers';
import {
	api,
	brewStatus,
	clearElixirs,
	createBlank,
	editorOf,
	noHorizontalScroll,
	openElixir,
	prepare,
	seedElixir,
	setKnobs,
	stripTab
} from './elixirs-fixtures';

const JOURNEY = 'elixirs-tabs';

async function openPicker(page: Page) {
	await page.getByRole('button', { name: 'Add new tab' }).click();
	const menu = page.getByTestId('new-tab-menu');
	await expect(menu).toBeVisible();
	await menu.locator('[data-tool="elixir"]').click();
	const picker = menu.getByTestId('elixir-picker');
	await expect(picker).toBeVisible();
	return { menu, picker };
}

async function openAllElixirs(page: Page) {
	const { picker } = await openPicker(page);
	await picker.getByTestId('elixir-picker-all').click();
	const dialog = page.getByTestId('all-elixirs');
	await expect(dialog).toBeVisible();
	return dialog;
}

async function startBrew(page: Page, elixirId: string) {
	const editor = await openElixir(page, elixirId);
	await expect(editor.getByRole('button', { name: /problem/ })).toContainText('0', { timeout: 30000 });
	await editor.locator('.elixir-header').getByRole('button', { name: 'Brew', exact: true }).click();
	return editor;
}

test.describe.configure({ mode: 'serial' });

test('the + menu drills into the Elixir picker, which searches, opens, switches and creates', async ({ page }) => {
	test.setTimeout(120000);
	await page.setViewportSize({ width: 1440, height: 900 });
	await loginAsOwner(page);
	const token = await ownerToken(page);
	await clearElixirs(page, token);
	const alpha = await createBlank(page, token, 'Alpha potion');
	const beta = await createBlank(page, token, 'Beta tonic');
	await page.goto('/generate');

	await page.getByRole('button', { name: 'Add new tab' }).click();
	const menu = page.getByTestId('new-tab-menu');
	await expect(menu).toBeVisible();
	const toolRow = menu.locator('[data-tool="elixir"]');
	await expect(toolRow).toHaveAttribute('aria-haspopup', 'menu');
	await expect(menu.getByTestId('elixir-picker')).toHaveCount(0);
	await toolRow.click();

	const picker = menu.getByTestId('elixir-picker');
	await expect(picker).toBeVisible();
	const search = picker.getByRole('combobox', { name: 'Find an elixir' });
	await expect(search).toBeFocused();
	const rows = picker.getByTestId('elixir-picker-row');
	await expect(rows).toHaveCount(2);
	await expect(rows.first()).toHaveAttribute('aria-selected', 'true');
	await search.press('ArrowDown');
	await expect(rows.nth(1)).toHaveAttribute('aria-selected', 'true');
	await expect(rows.first()).toHaveAttribute('aria-selected', 'false');
	await search.press('ArrowUp');
	await expect(rows.first()).toHaveAttribute('aria-selected', 'true');
	await expect(picker.getByTestId('elixir-picker-new')).toContainText('New elixir');
	await expect(picker.getByTestId('elixir-picker-all')).toContainText('All elixirs…');
	await expect(picker.getByTestId('elixir-picker-all')).toContainText('2');
	await screenshot(page, JOURNEY, 'picker-1440');

	await search.fill('beta');
	await expect(rows).toHaveCount(1);
	await expect(rows.first()).toHaveAttribute('data-elixir-id', beta);
	await expect(rows.first()).toContainText('Beta tonic');
	await search.press('Enter');
	await expect(menu).toHaveCount(0);
	await expect(editorOf(page, beta)).toBeVisible({ timeout: 30000 });
	await expect(stripTab(page, 'Beta tonic')).toHaveCount(1);

	const again = await openPicker(page);
	await expect(again.picker.locator(`[data-elixir-id="${beta}"]`).getByTestId('elixir-open-chip')).toBeVisible();
	await expect(again.picker.locator(`[data-elixir-id="${alpha}"]`).getByTestId('elixir-open-chip')).toHaveCount(0);
	await again.picker.locator(`[data-elixir-id="${beta}"]`).click();
	await expect(again.menu).toHaveCount(0);
	await expect(editorOf(page, beta)).toBeVisible();
	await expect(stripTab(page, 'Beta tonic')).toHaveCount(1);

	const typed = await openPicker(page);
	const typedSearch = typed.picker.getByRole('combobox', { name: 'Find an elixir' });
	await typedSearch.fill('zzz potion');
	await expect(typed.picker.getByTestId('elixir-picker-row')).toHaveCount(0);
	await expect(typed.picker.getByText('No elixir named “zzz potion”.')).toBeVisible();
	await expect(typed.picker.getByTestId('elixir-picker-new')).toContainText('New elixir “zzz potion”');
	await typedSearch.press('Enter');
	await expect(typed.menu).toHaveCount(0);
	await expect(stripTab(page, 'zzz potion')).toHaveCount(1, { timeout: 30000 });
	const listed = await api(page, token, 'get', '/api/elixirs');
	const created = listed.elixirs.find((item: any) => item.name === 'zzz potion');
	expect(created, 'the typed name created an elixir').toBeTruthy();
	await expect(editorOf(page, created.id).getByTestId('elixir-empty')).toBeVisible({ timeout: 30000 });

	const stepped = await openPicker(page);
	await page.keyboard.press('Escape');
	await expect(stepped.picker).toHaveCount(0);
	await expect(stepped.menu).toBeVisible();
	await expect(stepped.menu.locator('[data-tool="elixir"]')).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(stepped.menu).toHaveCount(0);

	const back = await openPicker(page);
	await back.picker.getByRole('button', { name: 'Back to new tab menu' }).click();
	await expect(back.picker).toHaveCount(0);
	await expect(back.menu).toBeVisible();
	await expect(back.menu.locator('[data-tool="elixir"]')).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(back.menu).toHaveCount(0);

	const all = await openPicker(page);
	await all.picker.getByTestId('elixir-picker-all').click();
	await expect(all.menu).toHaveCount(0);
	await expect(page.getByTestId('all-elixirs')).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(page.getByTestId('all-elixirs')).toHaveCount(0);
});

test('an elixir has one tab with its name and flask, cannot be renamed by double-click and closes without a confirm', async ({ page }) => {
	test.setTimeout(120000);
	await page.setViewportSize({ width: 1440, height: 900 });
	await loginAsOwner(page);
	const token = await ownerToken(page);
	await clearElixirs(page, token);
	const elixirId = await createBlank(page, token, 'Strip potion');

	await openElixir(page, elixirId);
	await openElixir(page, elixirId);
	const tab = stripTab(page, 'Strip potion');
	await expect(tab).toHaveCount(1);
	await expect(tab).toContainText('Elixir:');
	await expect(tab.getByTestId('tab-tool-thumb').locator('svg')).toBeVisible();
	await expect(tab.getByTestId('tab-status')).toHaveCount(0);
	await screenshot(page, JOURNEY, 'tab-idle-1440');

	await tab.locator('.book-tab').dblclick();
	await expect(tab.locator('input.tab-input')).toHaveCount(0);
	await expect(tab).toContainText('Strip potion');

	await tab.hover();
	await tab.locator('.close-button').click();
	await expect(stripTab(page, 'Strip potion')).toHaveCount(0);
	await expect(page.getByRole('dialog')).toHaveCount(0);
	await expect(page.getByText(/keeps brewing/)).toHaveCount(0);
});

test('the tab strip counts a brew, marks one that finished in the background, and keeps brewing after the tab closes', async ({ page }) => {
	test.setTimeout(240000);
	await page.setViewportSize({ width: 1440, height: 900 });
	const { token, modelRef } = await prepare(page);
	await clearElixirs(page, token);
	await setKnobs(page, token, { duration_seconds: 8.0 });

	const slowId = await seedElixir(page, token, modelRef, 'automatic', 'Slow potion');
	const slow = await startBrew(page, slowId);
	const slowTab = stripTab(page, 'Slow potion');
	const slowStatus = slowTab.getByTestId('tab-status');
	await expect(slowStatus).toHaveAttribute('data-tone', 'signal', { timeout: 30000 });
	await expect(slowStatus).toHaveText(/^\s*\d\/2$/, { timeout: 30000 });
	await screenshot(page, JOURNEY, 'tab-brewing-1440');

	await addGenerationTab(page);
	await expect(slow).not.toBeVisible();
	await expect(slowStatus).toHaveAttribute('data-tone', 'signal');
	await expect(slowStatus).toHaveAttribute('data-tone', 'success', { timeout: 120000 });
	await expect(slowTab).toContainText('Brew done');
	await screenshot(page, JOURNEY, 'tab-finished-background-1440');
	await slowTab.locator('.book-tab').click();
	await expect(slow).toBeVisible();
	await expect(brewStatus(slow)).toHaveText('Done');
	await expect(slowTab.getByTestId('tab-status')).toHaveCount(0);

	const closeId = await seedElixir(page, token, modelRef, 'automatic', 'Closing potion');
	const closing = await startBrew(page, closeId);
	const closingTab = stripTab(page, 'Closing potion');
	await expect(closingTab.getByTestId('tab-status')).toHaveAttribute('data-tone', 'signal', { timeout: 30000 });
	await closingTab.hover();
	await closingTab.locator('.close-button').click();
	await expect(stripTab(page, 'Closing potion')).toHaveCount(0);
	await expect(page.getByRole('dialog')).toHaveCount(0);
	await expect(page.getByText(/Closing potion keeps brewing\. Reopen it from \+ or the sidebar\./)).toBeVisible();
	await screenshot(page, JOURNEY, 'tab-closed-brewing-toast-1440');
	await page.getByRole('button', { name: 'Reopen' }).click();
	await expect(stripTab(page, 'Closing potion')).toHaveCount(1);
	await expect(closing).toBeVisible({ timeout: 30000 });
	await expect(brewStatus(closing)).toContainText(/Brewing|Starting/, { timeout: 30000 });
	await closing.locator('.elixir-header').getByRole('button', { name: 'Stop', exact: true }).click();
	await expect(brewStatus(closing)).toHaveText('Stopped', { timeout: 60000 });
});

test('the tab strip shows a brew waiting for a choice and one that needs attention', async ({ page }) => {
	test.setTimeout(240000);
	await page.setViewportSize({ width: 1440, height: 900 });
	const { token, modelRef } = await prepare(page);
	await clearElixirs(page, token);

	const choiceId = await seedElixir(page, token, modelRef, 'review', 'Choice potion');
	const choice = await startBrew(page, choiceId);
	await expect(brewStatus(choice)).toHaveText('Waiting for your choice', { timeout: 90000 });
	const choiceTab = stripTab(page, 'Choice potion');
	await expect(choiceTab.getByTestId('tab-status')).toHaveAttribute('data-tone', 'signal');
	await expect(choiceTab.getByTestId('tab-status')).not.toContainText(/\d\/\d/);
	await expect(choiceTab.locator('.status-ring')).toHaveCount(1);
	await expect(choiceTab).toContainText('Waiting for your choice');
	await screenshot(page, JOURNEY, 'tab-waiting-1440');

	await setKnobs(page, token, { fail_kind: 'failed' });
	const failingId = await seedElixir(page, token, modelRef, 'automatic', 'Failing potion');
	const failing = await startBrew(page, failingId);
	await expect(brewStatus(failing)).toHaveText('Needs attention', { timeout: 90000 });
	const failingTab = stripTab(page, 'Failing potion');
	await expect(failingTab.getByTestId('tab-status')).toHaveAttribute('data-tone', 'danger');
	await expect(failingTab).toContainText('Needs attention');
	await screenshot(page, JOURNEY, 'tab-failed-1440');
	await setKnobs(page, token, { fail_kind: '' });
});

test('All elixirs lists, filters, opens, renames, duplicates and deletes', async ({ page }) => {
	test.setTimeout(240000);
	await page.setViewportSize({ width: 1440, height: 900 });
	const { token, modelRef } = await prepare(page);
	await clearElixirs(page, token);
	await setKnobs(page, token, { duration_seconds: 8.0 });
	const dialogId = await seedElixir(page, token, modelRef, 'automatic', 'Dialog potion');
	const plainId = await createBlank(page, token, 'Plain tonic');
	const busyId = await seedElixir(page, token, modelRef, 'automatic', 'Busy potion');
	const busy = await startBrew(page, busyId);
	await expect(brewStatus(busy)).toContainText(/Brewing|Starting/, { timeout: 30000 });

	const dialog = await openAllElixirs(page);
	await expect(page.getByRole('dialog', { name: 'Elixirs' })).toBeVisible();
	const rows = dialog.getByTestId('all-elixirs-row');
	const row = (id: string) => dialog.locator(`[data-testid="all-elixirs-row"][data-elixir-id="${id}"]`);
	await expect(rows).toHaveCount(3);
	await screenshot(page, JOURNEY, 'all-elixirs-1440');

	const search = dialog.getByLabel('Search elixirs');
	await search.fill('plain');
	await expect(rows).toHaveCount(1);
	await expect(rows.first()).toHaveAttribute('data-elixir-id', plainId);
	await search.fill('');
	await expect(rows).toHaveCount(3);

	const show = dialog.getByRole('radiogroup', { name: 'Show' });
	await show.getByRole('radio', { name: 'Brewing' }).click();
	await expect(show.getByRole('radio', { name: 'Brewing' })).toHaveAttribute('aria-checked', 'true');
	await expect(rows).toHaveCount(1, { timeout: 30000 });
	await expect(rows.first()).toHaveAttribute('data-elixir-id', busyId);
	await show.getByRole('radio', { name: 'Needs you' }).click();
	await expect(rows).toHaveCount(0);
	await expect(dialog.getByText('No elixir matches.')).toBeVisible();
	await show.getByRole('radio', { name: 'Failed' }).click();
	await expect(rows).toHaveCount(0);
	await show.getByRole('radio', { name: 'All' }).click();
	await expect(rows).toHaveCount(3);

	await expect(row(busyId).locator('.open-chip')).toBeVisible();
	await expect(row(plainId).locator('.open-chip')).toHaveCount(0);
	await row(plainId).getByRole('button', { name: 'Open', exact: true }).click();
	await expect(page.getByTestId('all-elixirs')).toHaveCount(0);
	await expect(editorOf(page, plainId)).toBeVisible({ timeout: 30000 });
	await expect(stripTab(page, 'Plain tonic')).toHaveCount(1);

	const reopened = await openAllElixirs(page);
	await expect(row(plainId).locator('.open-chip')).toBeVisible();
	const menu = page.getByTestId('all-elixirs-menu');
	await row(plainId).getByRole('button', { name: 'Actions for Plain tonic' }).click();
	await expect(menu.getByRole('menuitem', { name: 'Go to its tab' })).toBeVisible();
	await menu.getByRole('menuitem', { name: 'Rename' }).click();
	const renameInput = row(plainId).getByLabel('Rename Plain tonic');
	await expect(renameInput).toBeFocused();
	await renameInput.fill('Discarded name');
	await renameInput.press('Escape');
	await expect(row(plainId).getByLabel('Rename Plain tonic')).toHaveCount(0);
	await expect(reopened).toBeVisible();
	await expect(row(plainId)).toContainText('Plain tonic');
	const unchanged = await api(page, token, 'get', `/api/elixirs/${plainId}`);
	expect(unchanged.name).toBe('Plain tonic');

	await row(plainId).getByRole('button', { name: 'Actions for Plain tonic' }).click();
	await menu.getByRole('menuitem', { name: 'Rename' }).click();
	await row(plainId).getByLabel('Rename Plain tonic').fill('Quiet tonic');
	await row(plainId).getByLabel('Rename Plain tonic').press('Enter');
	await expect(row(plainId)).toContainText('Quiet tonic');
	await expect(stripTab(page, 'Quiet tonic')).toHaveCount(1);
	await expect(stripTab(page, 'Plain tonic')).toHaveCount(0);
	const renamed = await api(page, token, 'get', `/api/elixirs/${plainId}`);
	expect(renamed.name).toBe('Quiet tonic');

	await row(dialogId).getByRole('button', { name: 'Actions for Dialog potion' }).click();
	await expect(menu.getByRole('menuitem', { name: 'Open in a tab' })).toBeVisible();
	await menu.getByRole('menuitem', { name: /^Duplicate/ }).click();
	await expect(page.getByText('Duplicated as "Dialog potion copy"')).toBeVisible({ timeout: 30000 });
	await expect(rows.filter({ hasText: 'Dialog potion copy' })).toHaveCount(1, { timeout: 30000 });
	await expect(rows).toHaveCount(4);
	const listed = await api(page, token, 'get', '/api/elixirs');
	const copy = listed.elixirs.find((item: any) => item.name === 'Dialog potion copy');
	expect(copy, 'the duplicate exists').toBeTruthy();
	const copyFull = await api(page, token, 'get', `/api/elixirs/${copy.id}`);
	expect(copyFull.definition.nodes).toHaveLength(2);
	const copyRuns = await api(page, token, 'get', `/api/elixirs/${copy.id}/runs`);
	expect(copyRuns.runs).toHaveLength(0);
	await expect(rows.filter({ hasText: 'Dialog potion copy' })).toContainText('2 steps');

	const busyTrigger = row(busyId).getByRole('button', { name: 'Actions for Busy potion' });
	await busyTrigger.click();
	const blocked = menu.getByRole('menuitem', { name: /^Delete/ });
	await expect(blocked).toBeDisabled();
	await expect(blocked).toContainText('Stop its brew first');
	await busyTrigger.click();
	await expect(menu).toHaveCount(0);

	await row(plainId).getByRole('button', { name: 'Actions for Quiet tonic' }).click();
	await expect(menu.getByRole('menuitem', { name: 'Go to its tab' })).toBeVisible();
	await menu.getByRole('menuitem', { name: /^Delete/ }).click();
	await page.getByRole('button', { name: 'Delete elixir' }).click();
	await expect(row(plainId)).toHaveCount(0, { timeout: 30000 });
	await expect(stripTab(page, 'Quiet tonic')).toHaveCount(0);
	await expect
		.poll(async () => (await page.request.get(`/api/elixirs/${plainId}`, { headers: { Authorization: `Bearer ${token}` } })).status(), { timeout: 30000 })
		.toBe(404);

	await page.keyboard.press('Escape');
	await expect(page.getByTestId('all-elixirs')).toHaveCount(0);
	await clearElixirs(page, token);
});

test('/elixirs opens a chooser tab that a pick replaces, then the last elixir tab', async ({ page }) => {
	test.setTimeout(120000);
	await page.setViewportSize({ width: 1440, height: 900 });
	await loginAsOwner(page);
	const token = await ownerToken(page);
	await clearElixirs(page, token);
	const chosenId = await createBlank(page, token, 'Chooser potion');
	await createBlank(page, token, 'Second tonic');

	await page.goto('/elixirs');
	await page.waitForURL('**/generate');
	const chooser = page.getByTestId('elixir-chooser');
	await expect(chooser).toBeVisible({ timeout: 30000 });
	await expect(chooser.getByText('Open an elixir')).toBeVisible();
	const strip = page.locator('.book-tab-wrapper');
	const tabCount = await strip.count();
	await expect(chooser.getByTestId('elixir-picker-row')).toHaveCount(2);
	await screenshot(page, JOURNEY, 'chooser-1440');

	await chooser.getByTestId('elixir-picker-row').filter({ hasText: 'Chooser potion' }).click();
	await expect(page.getByTestId('elixir-chooser')).toHaveCount(0);
	await expect(editorOf(page, chosenId)).toBeVisible({ timeout: 30000 });
	await expect(stripTab(page, 'Chooser potion')).toHaveCount(1);
	await expect(strip).toHaveCount(tabCount);

	await page.goto('/elixirs');
	await page.waitForURL('**/generate');
	await expect(editorOf(page, chosenId)).toBeVisible({ timeout: 30000 });
	await expect(page.getByTestId('elixir-chooser')).toHaveCount(0);
	await expect(strip).toHaveCount(tabCount);

	const tab = stripTab(page, 'Chooser potion');
	await tab.hover();
	await tab.locator('.close-button').click();
	await expect(stripTab(page, 'Chooser potion')).toHaveCount(0);
	await page.goto('/elixirs');
	await page.waitForURL('**/generate');
	await expect(page.getByTestId('elixir-chooser')).toBeVisible({ timeout: 30000 });
	const fresh = await strip.count();

	await page.getByTestId('elixir-chooser').getByTestId('elixir-picker-new').click();
	const created = page.getByTestId('elixir-tab');
	await expect(created).toBeVisible({ timeout: 30000 });
	const createdId = (await created.getAttribute('data-elixir-id'))!;
	const editor = editorOf(page, createdId);
	await expect(page.getByTestId('elixir-chooser')).toHaveCount(0);
	await expect(strip).toHaveCount(fresh);
	await expect(editor.getByTestId('elixir-empty')).toBeVisible();
	await expect(editor.getByText('Start your elixir')).toBeVisible();
	await expect(editor.getByRole('button', { name: /^Preset step/ })).toBeVisible();
	await expect(editor.getByRole('button', { name: /^Files/ })).toBeVisible();
	await expect(editor.getByLabel('Elixir name')).toHaveValue('Untitled elixir');
	await expect(editor.locator('.elixir-header').getByRole('button', { name: 'Brew', exact: true })).toBeDisabled();
	await screenshot(page, JOURNEY, 'new-elixir-empty-1440');
});

test('the sidebar has no Elixirs item and /elixirs still opens an elixir tab on the Generate page', async ({ page }) => {
	test.setTimeout(60000);
	await page.setViewportSize({ width: 1440, height: 900 });
	await loginAsOwner(page);
	const token = await ownerToken(page);
	await clearElixirs(page, token);

	await expect(page.locator('a[href="/elixirs"]')).toHaveCount(0);
	await page.goto('/elixirs');
	await page.waitForURL('**/generate');
	await expect(page.getByTestId('elixir-chooser')).toBeVisible({ timeout: 30000 });
	await expect(page).toHaveURL(/\/generate$/);
});

test('on a phone the Tabs sheet opens the Elixir picker as a sheet', async ({ page }) => {
	test.setTimeout(120000);
	await page.setViewportSize({ width: 390, height: 844 });
	await loginAsOwner(page);
	const token = await ownerToken(page);
	await clearElixirs(page, token);
	const firstId = await createBlank(page, token, 'Phone potion');
	const secondId = await createBlank(page, token, 'Phone tonic');

	const first = await openElixir(page, firstId);
	await expect(page.getByTestId('phone-tool-tab')).toBeVisible();
	await expect(first.getByTestId('elixir-dock').getByRole('button', { name: 'Brew', exact: true })).toBeVisible();
	await noHorizontalScroll(page);

	await page.getByRole('button', { name: /^Open tabs/ }).click();
	await expect(page.getByTestId('tabs-sheet')).toBeVisible();
	await expect(page.getByTestId('tabs-sheet-row').filter({ hasText: 'Phone potion' })).toHaveCount(1);
	const elixirButton = page.getByTestId('tabs-sheet-new').locator('[data-tool="elixir"]');
	await expect(elixirButton).toBeVisible();
	await elixirButton.click();

	const sheet = page.getByTestId('tabs-sheet-picker');
	await expect(sheet).toBeVisible();
	await expect(sheet.getByText('Open an elixir')).toBeVisible();
	const rows = sheet.getByTestId('elixir-picker-row');
	await expect(rows).toHaveCount(2);
	await expect(sheet.locator(`[data-elixir-id="${firstId}"]`).getByTestId('elixir-open-chip')).toBeVisible();
	await expect(sheet.locator(`[data-elixir-id="${secondId}"]`).getByTestId('elixir-open-chip')).toHaveCount(0);
	await noHorizontalScroll(page);
	await screenshot(page, JOURNEY, 'picker-sheet-390');

	await sheet.locator(`[data-elixir-id="${secondId}"]`).click();
	await expect(page.getByTestId('tabs-sheet-picker')).toHaveCount(0);
	await expect(editorOf(page, secondId)).toBeVisible({ timeout: 30000 });
	await expect(page.getByTestId('phone-tool-tab')).toBeVisible();
	await noHorizontalScroll(page);
	await screenshot(page, JOURNEY, 'phone-second-390');
});
