import { test, expect, type Page, type Route } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';

const JOURNEY = 'admin-running-generations';

async function fulfillJson(route: Route, data: unknown, status = 200) {
	await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
}

const NOW = Date.now() / 1000;

function running(id: string, userId: string, extra: Record<string, unknown> = {}) {
	return {
		generation_id: id,
		backend_id: 'b1',
		preset_id: 'flux-dev',
		tab_id: userId === 'setup' ? null : 'tab-1',
		user_id: userId,
		progress: 0.4,
		current_step: null,
		created_at: NOW - 2000,
		started_at: NOW - 1900,
		...extra
	};
}

function pending(id: string, position: number, extra: Record<string, unknown> = {}) {
	return {
		generation_id: id,
		backend_id: 'b1',
		preset_id: 'flux-dev',
		tab_id: 'tab-2',
		user_id: 'u-alice',
		queue_position: position,
		created_at: NOW - 60,
		...extra
	};
}

async function setup(
	page: Page,
	initial: { running: any[]; pending: any[] },
	options: { cancelStatus?: number } = {}
) {
	const state = { queue: initial, cancels: [] as string[], keepAfterCancel: false };
	await page.route('**/api/admin/generations/queue', (route) =>
		fulfillJson(route, { success: true, data: state.queue })
	);
	await page.route('**/api/generations/*/cancel', (route) => {
		const id = route.request().url().split('/').slice(-2)[0];
		state.cancels.push(id);
		if (options.cancelStatus && options.cancelStatus >= 400) {
			return fulfillJson(
				route,
				{ detail: { error: 'cancel_failed', message: 'The backend refused to stop this generation.' } },
				options.cancelStatus
			);
		}
		if (!state.keepAfterCancel) {
			state.queue = {
				running: state.queue.running.filter((r) => r.generation_id !== id),
				pending: state.queue.pending.filter((p) => p.generation_id !== id)
			};
		}
		return fulfillJson(route, { success: true, message: 'Generation cancelled successfully' });
	});
	await page.route('**/api/users', (route) => {
		if (route.request().method() !== 'GET') return route.fallback();
		return fulfillJson(route, {
			success: true,
			data: [{ id: 'u-alice', username: 'alice', email: 'a@example.com', account_type: 'USER' }]
		});
	});
	return state;
}

async function openGenerations(page: Page) {
	await page.goto('/admin?tab=generations');
	await expect(page.getByRole('heading', { name: 'Generations' }).first()).toBeVisible({ timeout: 15000 });
}

const panel = (page: Page) => page.locator('[data-running-generations]');
const row = (page: Page, id: string) => page.locator(`[data-running-row="${id}"]`);

test.describe('running generations - desktop', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('lists running and queued generations with owners', async ({ page }) => {
		await loginAsOwner(page);
		await setup(page, {
			running: [running('r-setup', 'setup'), running('r-alice', 'u-alice', { progress: 0.75 })],
			pending: [pending('q-1', 2)]
		});
		await openGenerations(page);
		await expect(panel(page)).toBeVisible({ timeout: 15000 });
		await expect(panel(page).getByText('2 running')).toBeVisible();
		await expect(panel(page).getByText('1 queued')).toBeVisible();
		await expect(row(page, 'r-setup').getByText('Setup / recipe')).toBeVisible();
		await expect(row(page, 'r-alice').getByText('alice')).toBeVisible();
		await expect(row(page, 'r-alice').getByText('75%')).toBeVisible();
		await expect(row(page, 'q-1').getByText('Queued #3')).toBeVisible();
		await screenshot(page, JOURNEY, 'desktop-running-list');
	});

	test('stop asks for confirmation, shows Stopping and then the row leaves', async ({ page }) => {
		await loginAsOwner(page);
		const state = await setup(page, { running: [running('r-setup', 'setup'), running('r-alice', 'u-alice')], pending: [] });
		state.keepAfterCancel = true;
		await openGenerations(page);
		await expect(row(page, 'r-setup')).toBeVisible({ timeout: 15000 });
		await row(page, 'r-setup').getByRole('button', { name: 'Stop', exact: true }).click();
		await expect(page.getByText('Stop this generation?')).toBeVisible();
		await screenshot(page, JOURNEY, 'desktop-stop-confirm');
		await page.getByRole('button', { name: 'Confirm' }).click();
		await expect(row(page, 'r-setup').getByRole('button', { name: 'Stopping…' })).toBeVisible({ timeout: 10000 });
		expect(state.cancels).toEqual(['r-setup']);
		await screenshot(page, JOURNEY, 'desktop-stopping');
		state.queue = { running: state.queue.running.filter((r) => r.generation_id !== 'r-setup'), pending: [] };
		await expect(row(page, 'r-setup')).toHaveCount(0, { timeout: 10000 });
		await expect(row(page, 'r-alice')).toBeVisible();
	});

	test('a refused cancel shows the message inline and keeps the generation', async ({ page }) => {
		await loginAsOwner(page);
		const state = await setup(page, { running: [running('r-setup', 'setup')], pending: [] }, { cancelStatus: 400 });
		await openGenerations(page);
		await expect(row(page, 'r-setup')).toBeVisible({ timeout: 15000 });
		await row(page, 'r-setup').getByRole('button', { name: 'Stop', exact: true }).click();
		await page.getByRole('button', { name: 'Confirm' }).click();
		await expect(row(page, 'r-setup').getByRole('alert')).toHaveText('The backend refused to stop this generation.', {
			timeout: 10000
		});
		await expect(row(page, 'r-setup').getByRole('button', { name: 'Stop', exact: true })).toBeEnabled();
		expect(state.cancels).toEqual(['r-setup']);
		await screenshot(page, JOURNEY, 'desktop-cancel-refused');
	});

	test('stop all cancels every listed generation after a confirm', async ({ page }) => {
		await loginAsOwner(page);
		const state = await setup(page, { running: [running('r1', 'setup')], pending: [pending('q1', 0), pending('q2', 1)] });
		await openGenerations(page);
		await expect(panel(page)).toBeVisible({ timeout: 15000 });
		await panel(page).getByRole('button', { name: 'Stop all' }).click();
		await expect(page.getByText('Stop all 3 generations?')).toBeVisible();
		await page.getByRole('button', { name: 'Confirm' }).click();
		await expect.poll(() => state.cancels.length).toBe(3);
		await expect(panel(page)).toHaveCount(0, { timeout: 10000 });
	});

	test('the section is hidden when nothing is running', async ({ page }) => {
		await loginAsOwner(page);
		await setup(page, { running: [], pending: [] });
		await openGenerations(page);
		await page.waitForTimeout(1500);
		await expect(panel(page)).toHaveCount(0);
	});

	test('a long queue is paged', async ({ page }) => {
		await loginAsOwner(page);
		const pendingRows = Array.from({ length: 11 }, (_, i) => pending(`q${i}`, i));
		await setup(page, { running: [running('r1', 'setup')], pending: pendingRows });
		await openGenerations(page);
		await expect(panel(page)).toBeVisible({ timeout: 15000 });
		await expect(panel(page).locator('[data-running-row]')).toHaveCount(8);
		await expect(panel(page).getByText('1–8 of 12')).toBeVisible();
		await panel(page).getByRole('button', { name: 'Next' }).click();
		await expect(panel(page).locator('[data-running-row]')).toHaveCount(4);
		await expect(panel(page).getByText('9–12 of 12')).toBeVisible();
		await screenshot(page, JOURNEY, 'desktop-paged');
	});
});

test.describe('running generations - mobile', () => {
	test.use({ viewport: { width: 390, height: 844 } });

	test('rows fit a phone', async ({ page }) => {
		await loginAsOwner(page);
		await setup(page, { running: [running('r-setup', 'setup'), running('r-alice', 'u-alice')], pending: [pending('q-1', 0)] });
		await openGenerations(page);
		await expect(panel(page)).toBeVisible({ timeout: 15000 });
		await screenshot(page, JOURNEY, 'mobile-running-list');
		expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
	});
});
