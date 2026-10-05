import { test, expect, type Route } from '@playwright/test';
import { loginAsOwner, screenshot } from './helpers';

const JOURNEY = 'admin-plugin-categories';

const VIEWPORTS = [
	{ name: '1440', width: 1440, height: 900 },
	{ name: '390', width: 390, height: 844 }
];

function plugin(id: string, name: string, category: string, type: string, description: string, extra: Record<string, unknown> = {}) {
	return {
		id,
		name,
		version: '1.0.0',
		type,
		enabled: true,
		manifest_path: `/plugins/${id}/manifest.yml`,
		description,
		author: 'PotionUI',
		category,
		tags: [],
		capabilities: [],
		source: 'marketplace',
		...extra
	};
}

const PLUGINS = [
	plugin('comfyui-backend', 'ComfyUI Backend', 'backends', 'full-stack', 'Run generations on a ComfyUI server.', {
		capabilities: ['comfyui-engine', 'workflow-import']
	}),
	plugin('openrouter-provider', 'OpenRouter', 'backends', 'full-stack', 'Cloud image and video models through OpenRouter.'),
	plugin('civitai-provider', 'CivitAI Provider', 'sources', 'backend-only', 'Browse and download models from CivitAI.'),
	plugin('huggingface-provider', 'HuggingFace Provider', 'sources', 'backend-only', 'Browse and download models from HuggingFace.'),
	plugin('nvidia-rtx-upscale', 'NVIDIA RTX Upscale', 'steps', 'backend-only', 'Fast RTX upscaling step.'),
	plugin('image-modal', 'Image Modal', 'tools', 'frontend-only', 'A richer viewer for generated images.'),
	plugin('a1111-metadata-export', 'A1111 Metadata Export', 'tools', 'full-stack', 'Export generation metadata in A1111 format.'),
	plugin('oidc-auth', 'OIDC Sign-in', 'security', 'full-stack', 'Sign in with an OpenID Connect provider.'),
	plugin('system-monitor', 'System Monitor', 'monitoring', 'full-stack', 'GPU and memory readouts.'),
	plugin('example-extensions', 'Example Extensions', 'developer', 'full-stack', 'Reference implementations of extension points.'),
	plugin('legacy-plugin', 'Third-party Plugin', 'workflow', 'backend-only', 'Ships an older category value.')
];

const HOOKS = [
	{ id: 1, plugin_id: 'comfyui-backend', hook_name: 'backend.register', hook_type: 'backend', handler_path: 'backend.hooks.register', sort_order: 10 },
	{ id: 2, plugin_id: 'comfyui-backend', hook_name: 'admin.plugin.tabs', hook_type: 'frontend', component_path: 'ImportWorkflowTab.js', position: 'import-workflow', sort_order: 20 }
];

const SETUP = [
	{
		plugin_id: 'openrouter-provider',
		complete: false,
		remaining: 3,
		steps: [
			{
				id: '0-backend.added',
				kind: 'backend.added',
				label: 'Add an OpenRouter backend with your API key',
				description: 'Add a backend for this plugin and enter its credentials, such as the API key.',
				status: 'todo',
				action: { kind: 'link', label: 'Add backend', href: '/admin?tab=backends&add=cloud.openrouter' }
			},
			{
				id: '1-cloud.models_enabled',
				kind: 'cloud.models_enabled',
				label: 'Turn on models in the catalog',
				description: 'Available once a backend is set up.',
				status: 'waiting'
			},
			{
				id: '2-presets.installed',
				kind: 'presets.installed',
				label: "Install the plugin's presets",
				description: 'None of its 2 presets are installed yet.',
				status: 'todo',
				action: { kind: 'link', label: 'Open presets', href: '/admin?tab=presets&id=p1' }
			}
		],
		guide: { title: 'OpenRouter', href: '/admin?tab=docs&doc=plugin%2Fopenrouter-provider%2FREADME' }
	},
	{ plugin_id: 'comfyui-backend', complete: true, remaining: 0, steps: [], guide: null }
];

async function json(route: Route, data: unknown) {
	await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ success: true, data }) });
}

async function mockPlugins(page: import('@playwright/test').Page) {
	await page.route(/\/api\/plugins(\/[^/?]+)?(\?.*)?$/, async (route) => {
		const request = route.request();
		if (request.method() !== 'GET') return route.fallback();
		const path = new URL(request.url()).pathname;
		if (path === '/api/plugins') return json(route, PLUGINS);
		if (path === '/api/plugins/setup') return json(route, SETUP);
		const id = path.slice('/api/plugins/'.length);
		const match = PLUGINS.find((p) => p.id === id);
		if (!match) return route.fallback();
		return json(route, {
			...match,
			hooks: HOOKS.filter((h) => h.plugin_id === id),
			settings_schema: [],
			settings_values: {}
		});
	});
}

for (const viewport of VIEWPORTS) {
	test(`plugins are grouped by what they add and keep build details out of the cards at ${viewport.name}`, async ({ page }) => {
		await page.setViewportSize({ width: viewport.width, height: viewport.height });
		await mockPlugins(page);
		await loginAsOwner(page);
		await page.goto('/admin?tab=plugins');

		const catalog = page.locator('[role="list"][aria-label="Plugin catalog"]');
		await expect(catalog.locator('[data-library-card]').first()).toBeVisible({ timeout: 20000 });

		const cards = catalog.locator('[data-library-card]');
		expect(await cards.count()).toBe(PLUGINS.length);
		const catalogText = (await catalog.textContent()) ?? '';
		expect(catalogText).not.toMatch(/full-stack|backend-only|frontend-only/i);
		expect(catalogText).toContain('Backends & compute');
		expect(catalogText).toContain('Model sources');

		await screenshot(page, JOURNEY, `list-${viewport.name}`);

		await page.goto('/admin?tab=plugins&category=backends');
		await expect(catalog.locator('[data-library-card]').first()).toBeVisible({ timeout: 20000 });
		const filtered = (await catalog.textContent()) ?? '';
		expect(filtered).toContain('ComfyUI');
		expect(filtered).toContain('OpenRouter');
		expect(filtered).not.toContain('CivitAI');
		await screenshot(page, JOURNEY, `section-backends-${viewport.name}`);

		await catalog.locator('[data-library-card]').filter({ hasText: 'ComfyUI' }).first().click();
		await expect(page.locator('nav[aria-label="Plugin details"]')).toBeVisible({ timeout: 15000 });

		const body = page.locator('body');
		await expect(body).toContainText('Technical details');
		await expect(body).not.toContainText(/FULL-STACK|BACKEND-ONLY|FRONTEND-ONLY/);
		const toggle = page.locator('section', { hasText: 'Technical details' }).locator('button[aria-expanded]').last();
		await expect(toggle).toHaveAttribute('aria-expanded', 'false');
		await screenshot(page, JOURNEY, `detail-collapsed-${viewport.name}`);

		await toggle.click();
		await expect(toggle).toHaveAttribute('aria-expanded', 'true');
		await expect(body).toContainText(/Server and interface parts|Server part only|Interface part only/);
		await screenshot(page, JOURNEY, `detail-expanded-${viewport.name}`);
	});
}

test('a provider plugin with setup left shows a badge on its card and its next step on the detail page', async ({ page }) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	await mockPlugins(page);
	await loginAsOwner(page);
	await page.goto('/admin?tab=plugins&category=backends');

	const catalog = page.locator('[role="list"][aria-label="Plugin catalog"]');
	const openrouter = catalog.locator('[data-library-card]').filter({ hasText: 'OpenRouter' }).first();
	const comfy = catalog.locator('[data-library-card]').filter({ hasText: 'ComfyUI' }).first();
	await expect(openrouter).toContainText('Setup needed', { timeout: 20000 });
	await expect(comfy).not.toContainText('Setup needed');
	await screenshot(page, JOURNEY, 'setup-badge');

	await openrouter.click();
	await expect(page.locator('nav[aria-label="Plugin details"]')).toBeVisible({ timeout: 15000 });
	const setup = page.locator('section').filter({ has: page.locator('ol[aria-label="Setup steps"]') }).first();
	await expect(setup).toContainText('Next: Add an OpenRouter backend with your API key');
	await expect(setup).toContainText('0 of 3 done');
	await expect(setup.getByRole('button', { name: 'Add backend' })).toHaveAttribute('href', '/admin?tab=backends&add=cloud.openrouter');
	await expect(setup.getByRole('button', { name: 'Open presets' })).toHaveAttribute('href', '/admin?tab=presets&id=p1');
	await expect(setup).toContainText('Read the OpenRouter guide');
	await screenshot(page, JOURNEY, 'setup-panel');
});
