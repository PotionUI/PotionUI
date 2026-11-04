import { test, expect, type Locator, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';
import { api, clearElixirs, editorOf, headers, noHorizontalScroll, problems, stripTab } from './elixirs-fixtures';

const JOURNEY = 'elixirs-canvas';
const KREA = '4TK1KBQZ2XMB8ME0PTMXS1YJQP';
const PNG = Buffer.from(
	'iVBORw0KGgoAAAANSUhEUgAAAGAAAABACAIAAABqVuVZAAAAnElEQVR4nO3QQRHAIADAMEDpJOAEy1Ow65M9EgW9zmefwbd1O+DvDAoGBYOCQcGgYFAwKBgUDAoGBYOCQcGgYFAwKBgUDAoGBYOCQcGgYFAwKBgUDAoGBYOCQcGgYFAwKBgUDAoGBYOCQcGgYFAwKBgUDAoGBYOCQcGgYFAwKBgUDAoGBYOCQcGgYFB4AWvuAd84bOuJAAAAAElFTkSuQmCC',
	'base64'
);

async function seed(page: Page, token: string) {
	const session = await page.request.post('/api/sessions/save', {
		headers: headers(token),
		data: {
			preset_id: KREA,
			name: 'Studio sharpen',
			data: { enhance: { selectedVariant: 'custom', prompt: 'crisp studio light, clean edges', formData: { quantity: 1, seed: 42 } } }
		}
	});
	expect(session.ok(), `session save -> ${session.status()}`).toBeTruthy();
	const upload = await page.request.post('/api/media/upload', {
		headers: headers(token),
		multipart: { file: { name: 'product-shot.png', mimeType: 'image/png', buffer: PNG } }
	});
	expect(upload.ok(), `upload -> ${upload.status()}`).toBeTruthy();
}

async function saved(editor: Locator) {
	await expect(editor.getByRole('status', { includeHidden: true }).filter({ hasText: /^Saved$/ })).toHaveCount(1, { timeout: 15000 });
}

test('elixirs: chooser, card lane, files, session copy, source slot, column width and conflict', async ({ page }) => {
	test.setTimeout(180000);
	await page.setViewportSize({ width: 1440, height: 900 });
	await loginAsOwner(page);
	const token = await ownerToken(page);
	await clearElixirs(page, token);
	await seed(page, token);

	await page.goto('/elixirs');
	await page.waitForURL('**/generate');
	const chooser = page.getByTestId('elixir-chooser');
	await expect(chooser).toBeVisible({ timeout: 30000 });
	await expect(chooser.getByText('No elixirs yet.')).toBeVisible({ timeout: 30000 });
	await expect(page.locator('a[href="/elixirs"]').first()).toBeVisible();
	await screenshot(page, JOURNEY, 'chooser-empty-1440');
	await page.setViewportSize({ width: 390, height: 844 });
	await noHorizontalScroll(page);
	await screenshot(page, JOURNEY, 'chooser-empty-390');
	await page.setViewportSize({ width: 1440, height: 900 });

	await chooser.getByTestId('elixir-picker-new').click();
	await expect(page.getByTestId('elixir-tab')).toBeVisible({ timeout: 30000 });
	const elixirId = (await page.getByTestId('elixir-tab').getAttribute('data-elixir-id'))!;
	expect(elixirId).toBeTruthy();
	const editor = editorOf(page, elixirId);
	await expect(editor.getByTestId('elixir-empty')).toBeVisible({ timeout: 20000 });
	await expect(editor.getByText('Start your elixir')).toBeVisible();
	await screenshot(page, JOURNEY, 'editor-empty-1440');

	await editor.getByRole('button', { name: /^Files/ }).click();
	const panel = editor.getByRole('complementary', { name: /settings$/ });
	await expect(panel.getByRole('button', { name: 'From Library' })).toBeVisible();
	await panel.getByLabel('Name').fill('Product photos');
	await panel.getByLabel('Name').press('Enter');
	await panel.getByRole('button', { name: 'From Library' }).click();
	const picker = page.getByRole('dialog');
	await expect(picker.getByLabel('Select product-shot.png')).toBeVisible({ timeout: 20000 });
	await picker.getByLabel('Select product-shot.png').check();
	await picker.getByRole('button', { name: 'Use selected' }).click();
	const photos = editor.getByRole('article', { name: 'Product photos, files' });
	await expect(photos.getByText('1 image')).toBeVisible();
	await expect(photos.getByText('Nothing uses these files yet. Add a step after them.')).toBeVisible();
	await saved(editor);

	await editor.locator('.elixir-header').getByRole('button', { name: 'Preset', exact: true }).click();
	await expect(panel.getByLabel('Preset and mode')).toBeVisible();
	await panel.getByLabel('Name').fill('Sharpen');
	await panel.getByLabel('Name').press('Enter');
	await panel.getByLabel('Preset and mode').selectOption({ label: 'Krea-2 · Enhance' });
	const sessionModes = panel.getByRole('group', { name: 'Where this step takes its settings from' });
	await expect(sessionModes.getByRole('button', { name: 'New' })).toHaveAttribute('aria-pressed', 'true');
	await sessionModes.getByRole('button', { name: 'Copy' }).click();
	const sessions = page.getByRole('dialog', { name: 'Copy a session' });
	await sessions.getByRole('button', { name: /Studio sharpen/ }).click();
	await sessions.getByRole('button', { name: 'Copy settings' }).click();
	await expect(sessions).toHaveCount(0);
	const sharpenNode = editor.getByRole('article', { name: 'Sharpen, preset step' });
	await expect(sharpenNode.getByText('Krea-2 · Enhance')).toBeVisible();
	await expect(sharpenNode.getByText('Copy of Studio sharpen')).toBeVisible();
	await expect(sharpenNode.getByRole('button', { name: /^Prompt: crisp studio light, clean edges/ })).toBeVisible();
	await expect(problems(editor)).toContainText('1');

	const sections = panel.getByRole('group', { name: 'Node settings sections' });
	await expect(sections.getByRole('button', { name: 'Quick' })).toHaveAttribute('aria-pressed', 'true');
	await expect(panel.locator('input#seed')).toHaveCount(0);
	await sections.getByRole('button', { name: 'All settings' }).click();
	const seedInput = panel.locator('input#seed');
	await expect(seedInput).toHaveValue('42', { timeout: 20000 });
	await seedInput.fill('777');
	await seedInput.press('Tab');
	await expect(sections.getByRole('button', { name: 'All settings · Edited' })).toBeVisible();
	await saved(editor);
	const edited = await api(page, token, 'get', `/api/elixirs/${elixirId}`);
	const presetNode = edited.definition.nodes.find((node: any) => node.kind === 'preset');
	expect(presetNode.overrides.formData).toEqual({ seed: 777 });
	expect(presetNode.session_snapshot.formData.seed).toBe(42);
	const preview = panel.getByLabel('Request preview');
	await preview.locator('summary').first().click();
	await expect(preview.getByText('crisp studio light, clean edges', { exact: true })).toBeVisible();
	await preview.getByText('Full request').click();
	await expect(preview.locator('pre')).toContainText('"seed": 777');
	await expect(preview.locator('pre')).toContainText('"mode": "enhance"');
	await screenshot(page, JOURNEY, 'editor-node-settings-1440');
	await panel.getByRole('button', { name: 'Reset to session' }).click();
	await expect(sections.getByRole('button', { name: 'All settings' })).toBeVisible();
	await expect(panel.locator('input#seed')).toHaveValue('42');
	await saved(editor);

	await sections.getByRole('button', { name: 'Quick' }).click();
	await sessionModes.getByRole('button', { name: 'Follow' }).click();
	await expect(sharpenNode.getByText('Following Studio sharpen')).toBeVisible();
	await expect(sessionModes.getByRole('button', { name: 'Follow' })).toHaveAttribute('aria-pressed', 'true');
	await expect(panel.getByRole('button', { name: /^Followed session: Studio sharpen/ })).toBeVisible();
	await sections.getByRole('button', { name: 'All settings' }).click();
	await expect(panel.getByText('Edit this in the session.', { exact: false })).toBeVisible();
	await expect(panel.locator('input#seed')).toHaveCount(0);
	const following = await api(page, token, 'get', `/api/elixirs/${elixirId}`);
	const followedNode = following.definition.nodes.find((node: any) => node.kind === 'preset');
	expect(followedNode.session_mode).toBe('follow');
	expect(followedNode.overrides).toEqual({});
	await sections.getByRole('button', { name: 'Quick' }).click();
	await panel.getByRole('button', { name: 'Make a copy' }).click();
	await expect(sharpenNode.getByText('Copy of Studio sharpen')).toBeVisible();
	await expect(sessionModes.getByRole('button', { name: 'Copy' })).toHaveAttribute('aria-pressed', 'true');
	await saved(editor);

	await page.keyboard.press('Escape');
	await expect(panel).toHaveCount(0);
	await sharpenNode.getByRole('button', { name: 'Sharpen settings' }).click();
	await expect(panel.getByLabel('Preset and mode')).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(panel).toHaveCount(0);
	await expect(sharpenNode.getByRole('button', { name: 'Sharpen settings' })).toBeFocused();
	await photos.locator('.files').click();
	await expect(panel.getByRole('button', { name: 'From Library' })).toBeVisible();
	await expect(photos).toHaveClass(/border-signal/);
	await page.keyboard.press('Escape');
	await expect(panel).toHaveCount(0);
	await sharpenNode.locator('.lead').click();
	await expect(panel.getByLabel('Preset and mode')).toBeVisible();
	await sharpenNode.locator('.lead').click();
	await expect(panel.getByLabel('Preset and mode')).toBeVisible();
	await page.keyboard.press('Escape');
	await expect(panel).toHaveCount(0);

	await sharpenNode.getByRole('button', { name: /^Source image: From the session/ }).click();
	const sourceMenu = page.getByRole('menu', { name: 'Source image from' });
	await expect(sourceMenu.getByRole('group', { name: 'Not available' })).toHaveCount(0);
	await expect(panel).toHaveCount(0);
	await screenshot(page, JOURNEY, 'editor-source-menu-1440');
	await sourceMenu.getByRole('menuitemradio', { name: /Product photos/ }).click();
	await expect(sourceMenu).toHaveCount(0);
	await expect(sharpenNode.getByRole('button', { name: /^Source image: Product photos/ })).toBeVisible();
	await expect(photos.getByText('Nothing uses these files yet. Add a step after them.')).toHaveCount(0);
	await saved(editor);
	await expect(problems(editor)).toContainText('0', { timeout: 15000 });
	await expect(sharpenNode.getByText('Not brewed yet')).toBeVisible();
	await expect(editor.locator('[data-lane-node]').first().locator('[data-lane-node]')).toHaveCount(1);
	await screenshot(page, JOURNEY, 'editor-connected-1440');

	const column = editor.getByRole('separator', { name: 'Width of column 2' });
	await column.focus();
	await page.keyboard.press('Shift+ArrowRight');
	await page.keyboard.press('Shift+ArrowRight');
	await expect.poll(async () => Math.round((await sharpenNode.boundingBox())!.width)).toBe(512);
	await column.dblclick();
	await expect.poll(async () => Math.round((await sharpenNode.boundingBox())!.width)).toBe(384);

	await sharpenNode.getByRole('button', { name: 'Sharpen settings' }).click();
	await expect(panel.getByRole('group', { name: 'Node settings sections' })).toBeVisible();
	await screenshot(page, JOURNEY, 'editor-drawer-1440');
	await page.setViewportSize({ width: 390, height: 844 });
	await expect(panel.getByRole('group', { name: 'Node settings sections' })).toBeVisible();
	await noHorizontalScroll(page);
	await screenshot(page, JOURNEY, 'editor-panel-390');
	await panel.getByRole('button', { name: 'Close node settings' }).click();
	await expect(panel).toHaveCount(0);
	await expect(editor.getByRole('separator')).toHaveCount(0);
	await expect(sharpenNode.getByRole('button', { name: /^Source image: Product photos/ })).toBeVisible();
	const smallest = await editor.locator('.lane-wrap').evaluate((root) =>
		Math.min(...[...root.querySelectorAll('*')].filter((el) => el.textContent?.trim()).map((el) => parseFloat(getComputedStyle(el).fontSize)))
	);
	expect(smallest).toBeGreaterThanOrEqual(12);
	await noHorizontalScroll(page);
	await screenshot(page, JOURNEY, 'editor-cards-390');
	await sharpenNode.getByRole('button', { name: /^Source image: Product photos/ }).click();
	await page.getByRole('menu', { name: 'Source image from' }).getByRole('menuitem', { name: /From the session/ }).click();
	await saved(editor);
	await editor.getByRole('button', { name: 'More', exact: true }).click();
	await expect(page.getByRole('menuitem', { name: /problem/ })).toContainText('1', { timeout: 15000 });
	await screenshot(page, JOURNEY, 'editor-menu-390');
	await page.keyboard.press('Escape');
	await expect(page.getByRole('menu')).toHaveCount(0);
	await sharpenNode.getByRole('button', { name: /^Source image: From the session/ }).click();
	await page.getByRole('menu', { name: 'Source image from' }).getByRole('menuitemradio', { name: /Product photos/ }).click();
	await saved(editor);
	await editor.getByRole('button', { name: 'More', exact: true }).click();
	await expect(page.getByRole('menuitem', { name: /problem/ })).toContainText('0', { timeout: 15000 });
	await page.keyboard.press('Escape');
	await page.setViewportSize({ width: 1440, height: 900 });

	const current = await api(page, token, 'get', `/api/elixirs/${elixirId}`);
	const bump = await page.request.put(`/api/elixirs/${elixirId}`, {
		headers: headers(token),
		data: { expected_revision: current.revision, name: 'Renamed elsewhere' }
	});
	expect(bump.ok()).toBeTruthy();
	await editor.getByLabel('Elixir name').fill('Product campaign');
	await editor.getByLabel('Elixir name').press('Enter');
	await expect(editor.getByText('This elixir was changed in another tab or window.')).toBeVisible({ timeout: 15000 });
	await screenshot(page, JOURNEY, 'editor-conflict-1440');
	await editor.getByRole('button', { name: 'Reload' }).click();
	await expect(editor.getByLabel('Elixir name')).toHaveValue('Renamed elsewhere');
	await expect(stripTab(page, 'Renamed elsewhere')).toHaveCount(1);

	await page.getByRole('button', { name: 'Add new tab' }).click();
	await page.getByTestId('new-tab-menu').locator('[data-tool="elixir"]').click();
	const row = page.getByTestId('elixir-picker-row').filter({ hasText: 'Renamed elsewhere' });
	await expect(row).toHaveCount(1);
	await expect(row).toHaveAttribute('data-elixir-id', elixirId);
	await expect(row).toContainText('2 steps');
	await screenshot(page, JOURNEY, 'picker-1440');
	await page.keyboard.press('Escape');
	await page.keyboard.press('Escape');
	await expect(page.getByTestId('new-tab-menu')).toHaveCount(0);

	await api(page, token, 'delete', `/api/elixirs/${elixirId}`);
});
