import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';

const JOURNEY = 'plugin-host-ui';
const PLUGIN_ID = 'example-extensions';

async function apiPost(page: Page, url: string, token: string) {
	const res = await page.request.post(url, { headers: { Authorization: `Bearer ${token}` }, data: {} });
	expect(res.ok(), `POST ${url} -> ${res.status()}`).toBeTruthy();
}

test('plugin frontend renders host ui components with app styling', async ({ page }) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	await loginAsOwner(page);
	const token = await ownerToken(page);

	await apiPost(page, '/api/plugins/scan', token);
	const list = await page.request.get('/api/plugins', { headers: { Authorization: `Bearer ${token}` } });
	const row = ((await list.json()).data || []).find((p: any) => p.id === PLUGIN_ID);
	if (!row) {
		test.skip(true, `'${PLUGIN_ID}' was not discovered on this throwaway instance.`);
		return;
	}
	if (!row.enabled) await apiPost(page, `/api/plugins/${PLUGIN_ID}/enable`, token);

	await page.goto('/admin');
	await page.getByRole('link', { name: 'Example Extension' }).click();

	const root = page.getByTestId('example-host-ui');
	const button = root.getByRole('button', { name: 'Host button' });
	await expect(button).toBeVisible({ timeout: 20000 });
	await expect(root.getByText('Host badge')).toBeVisible();

	const styles = await button.evaluate((el) => {
		const cs = getComputedStyle(el);
		return { background: cs.backgroundColor, radius: cs.borderTopLeftRadius };
	});
	expect(styles.background).not.toBe('rgba(0, 0, 0, 0)');
	expect(styles.radius).not.toBe('0px');

	const spacing = await page
		.getByTestId('example-host-ui-title')
		.evaluate((el) => ({ letter: getComputedStyle(el).letterSpacing, font: getComputedStyle(el).fontSize }));
	expect(parseFloat(spacing.letter)).toBeCloseTo(parseFloat(spacing.font) * 0.37, 1);

	await button.click();
	await expect(page.getByTestId('example-host-ui-clicks')).toHaveText('1');

	await root.getByText('Hover me').hover();
	await expect(page.getByText('Rendered by the host Tooltip')).toBeVisible();

	await screenshot(page, JOURNEY, 'host-ui-1440');
});
