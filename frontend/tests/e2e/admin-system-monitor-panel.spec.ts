import { test, expect, type Page, type Route } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';

const JOURNEY = 'admin-system-monitor-panel';

function gpu(index: number, name: string, total: number, used: number | null, util: number | null = null, temp: number | null = null) {
	return {
		index,
		name,
		vram_total_gb: total,
		vram_used_gb: used,
		vram_usage_percent: used == null ? null : Math.round((used / total) * 1000) / 10,
		temperature_c: temp,
		utilization_percent: util
	};
}

const SNAPSHOTS = [
	{
		id: 'local', name: 'Local GPU', engine: 'native', driver: 'native.local', kind: 'local', status: 'online', detail: null,
		jobs: { running: 1, queued: 2, capacity: 1 },
		hardware: {
			cpu: { usage_percent: 34.5, cores: 16 },
			ram: { total_gb: 64, used_gb: 41.2, usage_percent: 64.4 },
			gpus: [gpu(0, 'NVIDIA GeForce RTX 4090', 24, 21.5, 96, 71), gpu(1, 'NVIDIA GeForce RTX 3060', 12, 1.2, 2, 38)]
		}
	},
	{
		id: 'comfy', name: 'Studio ComfyUI', engine: 'comfyui', driver: 'comfyui', kind: 'comfyui', status: 'online', detail: null,
		jobs: { running: 0, queued: 0, capacity: 1 },
		hardware: { cpu: null, ram: { total_gb: 32, used_gb: 9.8, usage_percent: 30.6 }, gpus: [gpu(0, 'cuda:0 NVIDIA RTX A5000 : cudaMallocAsync', 24, 6.3)] }
	},
	{
		id: 'comfy-down', name: 'Garage ComfyUI', engine: 'comfyui', driver: 'comfyui', kind: 'comfyui', status: 'unreachable', detail: 'No answer in time.',
		jobs: { running: 0, queued: 0, capacity: 1 }, hardware: null
	},
	{
		id: 'worker', name: 'Rack worker with a deliberately long backend name to test truncation', engine: 'native', driver: 'native.remote', kind: 'worker', status: 'degraded',
		detail: 'Worker reports a different build than this installation.',
		jobs: { running: 2, queued: 0, capacity: 2 },
		hardware: { cpu: { usage_percent: null, cores: 32 }, ram: { total_gb: 256, used_gb: null, usage_percent: null }, gpus: [gpu(0, 'NVIDIA H100 80GB HBM3', 80, 64), gpu(1, 'NVIDIA H100 80GB HBM3', 80, null)] }
	},
	{
		id: 'cloud', name: 'OpenRouter', engine: 'cloud', driver: 'openrouter', kind: 'cloud', status: 'online', detail: null,
		jobs: { running: 3, queued: 1, capacity: 4 }, hardware: null
	},
	{
		id: 'off', name: 'Spare backend', engine: 'comfyui', driver: 'comfyui', kind: 'comfyui', status: 'inactive', detail: 'The backend is not running.',
		jobs: { running: 0, queued: 0, capacity: null }, hardware: null
	}
];

async function mockBackends(page: Page, handler: (route: Route) => Promise<void> | void) {
	await page.route('**/api/system/backends', handler);
}

async function mountWidget(page: Page, isAdmin = true): Promise<void> {
	const token = await ownerToken(page);
	await page.evaluate(async ({ authToken, admin }) => {
		const module = await import(/* @vite-ignore */ `${location.origin}/api/plugins/system-monitor/assets/SystemMonitorWidget.js`);
		const target = document.createElement('div');
		target.style.cssText = 'position:fixed;left:8px;top:8px;z-index:50';
		target.id = 'harness-widget';
		document.body.appendChild(target);
		module.mountPlugin(target, {
			context: {
				apiBaseUrl: location.origin,
				token: authToken,
				isAdmin: admin,
				wsUrl: (path: string, t: string) => `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}${path}?token=${t}`
			}
		});
	}, { authToken: token, admin: isAdmin });
}

async function openPanel(page: Page) {
	const ring = page.getByRole('button', { name: /^(GPU memory|System memory|CPU) / }).first();
	await expect(ring).toBeVisible({ timeout: 20000 });
	await ring.click();
	const dialog = page.getByRole('dialog', { name: 'Backends' });
	await expect(dialog).toBeVisible();
	return dialog;
}

for (const viewport of [
	{ name: '1440', width: 1440, height: 900 },
	{ name: '390', width: 390, height: 844 }
]) {
	test.describe(`backends panel at ${viewport.name}`, () => {
		test.use({ viewport: { width: viewport.width, height: viewport.height } });

		test('lists every backend kind with hardware, jobs and states', async ({ page }) => {
			await mockBackends(page, (route) => route.fulfill({ json: { success: true, data: SNAPSHOTS } }));
			await loginAsOwner(page);
			await mountWidget(page);
			const dialog = await openPanel(page);

			const local = dialog.getByTestId('backend-local');
			await expect(local).toContainText('This machine');
			await expect(local).toContainText('NVIDIA GeForce RTX 4090');
			await expect(local).toContainText('NVIDIA GeForce RTX 3060');
			await expect(local).toContainText('21.5 / 24 GB');
			await expect(dialog.getByTestId('backend-comfy')).toContainText('RTX A5000');
			await expect(dialog.getByTestId('backend-comfy-down')).toContainText('Unreachable');
			await expect(dialog.getByTestId('backend-worker')).toContainText('Degraded');
			await expect(dialog.getByTestId('backend-worker')).toContainText('GPU 1');
			await expect(dialog.getByTestId('backend-cloud')).toContainText("Runs on the provider's hardware");
			await expect(dialog.getByTestId('backend-off')).toContainText('Not running');

			const overflowX = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
			expect(overflowX, 'panel must not cause horizontal page scroll').toBeLessThanOrEqual(0);
			const box = await dialog.boundingBox();
			expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width + 1);

			await screenshot(page, JOURNEY, `panel-${viewport.name}`);
			await dialog.locator('.panel-body').evaluate((el) => el.scrollTo(0, el.scrollHeight));
			await screenshot(page, JOURNEY, `panel-bottom-${viewport.name}`);
		});

		test('shows the empty state when no backend is enabled', async ({ page }) => {
			await mockBackends(page, (route) => route.fulfill({ json: { success: true, data: [] } }));
			await loginAsOwner(page);
			await mountWidget(page);
			const dialog = await openPanel(page);
			await expect(dialog).toContainText('No backend is enabled');
			await screenshot(page, JOURNEY, `empty-${viewport.name}`);
		});

		test('says so when the backends cannot be read, and keeps old numbers when updates fail', async ({ page }) => {
			let calls = 0;
			await mockBackends(page, (route) => {
				calls += 1;
				return calls === 1
					? route.fulfill({ json: { success: true, data: SNAPSHOTS.slice(0, 1) } })
					: route.fulfill({ status: 500, json: { success: false } });
			});
			await loginAsOwner(page);
			await mountWidget(page);
			const dialog = await openPanel(page);
			await expect(dialog.getByTestId('backend-local')).toBeVisible();
			await expect(dialog).toContainText('last update failed', { timeout: 15000 });
			await expect(dialog.getByTestId('backend-local')).toBeVisible();
			await screenshot(page, JOURNEY, `stale-${viewport.name}`);
		});

		test('closes with Escape and polls only while open', async ({ page }) => {
			await page.clock.install();
			let calls = 0;
			await mockBackends(page, (route) => {
				calls += 1;
				return route.fulfill({ json: { success: true, data: SNAPSHOTS.slice(0, 1) } });
			});
			await loginAsOwner(page);
			await mountWidget(page);
			const dialog = await openPanel(page);
			await expect(dialog.getByTestId('backend-local')).toBeVisible();
			const whileOpen = calls;
			await page.clock.runFor(5100);
			await expect.poll(() => calls).toBeGreaterThan(whileOpen);
			await page.keyboard.press('Escape');
			await expect(dialog).toHaveCount(0);
			const afterClose = calls;
			await page.clock.runFor(6000);
			expect(calls).toBe(afterClose);
		});

		test('a non-admin clicking the rings opens nothing and asks for nothing', async ({ page }) => {
			let calls = 0;
			await mockBackends(page, (route) => {
				calls += 1;
				return route.fulfill({ json: { success: true, data: SNAPSHOTS } });
			});
			await loginAsOwner(page);
			await mountWidget(page, false);
			const ring = page.getByRole('button', { name: /^(GPU memory|System memory|CPU) / }).first();
			await expect(ring).toBeVisible({ timeout: 20000 });
			await ring.click();
			await expect(page.getByRole('dialog', { name: 'Backends' })).toHaveCount(0);
			expect(calls).toBe(0);
		});
	});
}

test.describe('sidebar entry point', () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test('the sidebar rings open the panel for an admin', async ({ page }) => {
		await mockBackends(page, (route) => route.fulfill({ json: { success: true, data: SNAPSHOTS } }));
		await page.route('**/api/plugins/sidebar-widgets', (route) =>
			route.fulfill({
				json: {
					success: true,
					data: [{ plugin_id: 'system-monitor', widget_id: 'system-monitor', position: 'bottom', component: 'SystemMonitorWidget.js', order: 10, label: 'System Monitor' }]
				}
			})
		);
		await loginAsOwner(page);
		const dialog = await openPanel(page);
		await expect(dialog.getByTestId('backend-local')).toBeVisible();
		await screenshot(page, JOURNEY, 'sidebar-open');
	});
});
