import { test, expect, type Page, type Route } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';

const JOURNEY = 'backends-index-feedback';

const ENGINES = [
	{ engine: 'native', driver: 'native.local', label: 'Native (local)', singleton: true, creatable: false, fields: [] },
	{ engine: 'comfyui', driver: 'comfyui.remote', label: 'ComfyUI', singleton: false, creatable: true, fields: [] }
];

function backend(id: string, name: string, engine: string, driver: string, isDefault: boolean) {
	return {
		id,
		name,
		engine,
		driver,
		enabled: true,
		is_default: isDefault,
		priority: 1,
		timeout_seconds: 300,
		scheduling_policy: 'fifo',
		scheduling_max_consecutive_same_model: 3,
		configured: true,
		quick_actions: []
	};
}

const BACKENDS = [
	backend('local', 'Local GPU', 'native', 'native.local', true),
	backend('comfy', 'Studio ComfyUI', 'comfyui', 'comfyui.remote', false)
];

const REMOTE_MESSAGE_RESULT = {
	backend_id: 'comfy',
	listed: 42,
	created: 7,
	matched: 35,
	removed: 0,
	size_conflicts: [],
	digest_conflicts: [],
	duplicates: [],
	ambiguous: []
};

async function json(route: Route, data: unknown, status = 200) {
	await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
}

interface Mocks {
	phase: { value: 'idle' | 'scanning' | 'indexing' | 'done' };
	indexCalls: string[];
	testCalls: string[];
}

async function mockBackends(page: Page): Promise<Mocks> {
	const mocks: Mocks = { phase: { value: 'idle' }, indexCalls: [], testCalls: [] };
	const statusFor = () => {
		switch (mocks.phase.value) {
			case 'scanning':
				return { state: 'scanning', scanned_roots: ['/models'] };
			case 'indexing':
				return { state: 'indexing', total: 40, processed: 18 };
			case 'done':
				return { state: 'done', found_on_disk: 40, indexed: 12 };
			default:
				return { state: 'idle' };
		}
	};
	await page.route(
		(url) => url.pathname.startsWith('/api/backends') || url.pathname === '/api/models/indexing/status',
		async (route) => {
			const request = route.request();
			const path = new URL(request.url()).pathname;
			const method = request.method();
			if (path === '/api/models/indexing/status') return json(route, { success: true, data: statusFor() });
			if (path === '/api/backends/engines') return json(route, { success: true, data: ENGINES });
			if (path === '/api/backends/health') {
				return json(route, {
					success: true,
					data: BACKENDS.map((b) => ({ backend_id: b.id, health: { status: 'healthy' } }))
				});
			}
			if (path === '/api/backends' && method === 'GET') return json(route, { success: true, data: BACKENDS });
			const indexMatch = path.match(/^\/api\/backends\/([^/]+)\/index-models$/);
			if (indexMatch && method === 'POST') {
				mocks.indexCalls.push(indexMatch[1]);
				if (indexMatch[1] === 'local') {
					mocks.phase.value = 'scanning';
					return json(route, { success: true, data: statusFor(), message: "Started rescanning model roots for 'Local GPU'" });
				}
				await new Promise((resolve) => setTimeout(resolve, 700));
				return json(route, { success: true, data: REMOTE_MESSAGE_RESULT });
			}
			const testMatch = path.match(/^\/api\/backends\/([^/]+)\/test$/);
			if (testMatch && method === 'POST') {
				mocks.testCalls.push(testMatch[1]);
				await new Promise((resolve) => setTimeout(resolve, 500));
				return json(route, { success: true, message: 'ok' });
			}
			if (path.endsWith('/stats')) {
				return json(route, {
					success: true,
					data: { backend_id: 'local', indexed_models: 0, total_size_bytes: 0, total_size_gb: 0, last_indexed_at: null }
				});
			}
			return route.continue();
		}
	);
	return mocks;
}

for (const viewport of [
	{ name: '1440', width: 1440, height: 900 },
	{ name: '390', width: 390, height: 844 }
]) {
	test.describe(`backends index feedback @${viewport.name}`, () => {
		test.use({ viewport: { width: viewport.width, height: viewport.height } });

		test('native detail shows busy and progress from the shared status, then the result', async ({ page }) => {
			const mocks = await mockBackends(page);
			await loginAsOwner(page);
			await page.goto('/admin?tab=backends&backend=local');
			const indexButton = page.getByRole('button', { name: 'Index models' });
			await expect(indexButton).toBeVisible();
			await expect(indexButton).toBeEnabled();

			await indexButton.click();
			await expect(indexButton).toBeDisabled();
			await expect(page.getByText('Scanning /models')).toBeVisible();

			mocks.phase.value = 'indexing';
			await expect(page.getByText('18 / 40')).toBeVisible({ timeout: 6000 });
			await expect(indexButton).toBeDisabled();
			await screenshot(page, JOURNEY, `detail-indexing-${viewport.name}`);

			mocks.phase.value = 'done';
			await expect(page.getByText('new ·')).toBeVisible({ timeout: 6000 });
			await expect(page.getByText('Indexed 40 model files — 12 new, 28 already indexed.')).toBeVisible();
			await expect(indexButton).toBeEnabled();
			await screenshot(page, JOURNEY, `detail-done-${viewport.name}`);
			expect(mocks.indexCalls).toEqual(['local']);
		});

		test('remote index stays busy until the call returns and toasts the message', async ({ page }) => {
			const mocks = await mockBackends(page);
			await loginAsOwner(page);
			await page.goto('/admin?tab=backends&backend=comfy');
			const indexButton = page.getByRole('button', { name: 'Index models' });
			await indexButton.click();
			await expect(indexButton).toBeDisabled();
			await expect(page.getByText('Indexed 42 models on "Studio ComfyUI" — 7 new, 35 matched, 0 removed')).toBeVisible();
			await expect(indexButton).toBeEnabled();
			expect(mocks.indexCalls).toEqual(['comfy']);
		});

		test('list rows carry actions that do not open the row and busy per row', async ({ page }) => {
			const mocks = await mockBackends(page);
			await loginAsOwner(page);
			await page.goto('/admin?tab=backends');
			const row = page.getByRole('row').filter({ hasText: 'Studio ComfyUI' }).first();
			const visibleRow = viewport.width >= 640 ? row : page.locator('.dt-mobile [role="row"]').filter({ hasText: 'Studio ComfyUI' });
			await expect(visibleRow).toBeVisible();
			await visibleRow.hover();
			await screenshot(page, JOURNEY, `list-row-actions-${viewport.name}`);

			const indexButton = visibleRow.getByRole('button', { name: 'Index models' });
			await indexButton.click();
			await expect(indexButton).toBeDisabled();
			const otherRowIndex = page
				.locator(viewport.width >= 640 ? '.dt-scroll > .dt-row' : '.dt-mobile [role="row"]')
				.filter({ hasText: 'Local GPU' })
				.getByRole('button', { name: 'Index models' });
			await expect(otherRowIndex).toBeEnabled();
			await expect(page.getByText('Indexed 42 models')).toBeVisible();
			expect(page.url()).not.toContain('backend=');

			await visibleRow.getByRole('button', { name: 'Test connection' }).click();
			await expect(page.getByText('Connection to "Studio ComfyUI" successful')).toBeVisible();
			expect(page.url()).not.toContain('backend=');
			expect(mocks.testCalls).toEqual(['comfy']);

			await visibleRow.hover();
			await visibleRow.getByText('Studio ComfyUI').first().click();
			await expect(page).toHaveURL(/backend=comfy/);
		});
	});
}
