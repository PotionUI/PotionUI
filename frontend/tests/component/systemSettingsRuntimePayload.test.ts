import { describe, it, expect, vi, afterEach } from 'vitest';

vi.mock('$lib/services/admin-api', () => ({
	getSettings: vi.fn(),
	updateSettings: vi.fn(() => Promise.resolve({ success: true })),
	restartApp: vi.fn()
}));

const adminApi = await import('$lib/services/admin-api');
const { page } = await import('./stubs/appStores');
const { default: SystemSettingsTab } = await import('../../src/routes/admin/components/SystemSettingsTab.svelte');
const { createClassComponent } = await import('svelte/legacy');

async function settle() {
	for (let i = 0; i < 10; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

function rowIn(target: HTMLElement, key: string): HTMLElement {
	const el = target.querySelector<HTMLElement>(`[data-testid="runtime-setting-${key}"]`);
	if (!el) throw new Error(`row ${key} not rendered`);
	return el;
}

async function mountOn(view: string, settings: Record<string, unknown>) {
	page.update((p) => ({ ...p, url: new URL(`http://localhost/admin?tab=settings&view=${view}`) }));
	vi.mocked(adminApi.getSettings).mockResolvedValue({ success: true, data: settings } as never);
	const target = document.createElement('div');
	document.body.appendChild(target);
	createClassComponent({ component: SystemSettingsTab as never, target, props: {} });
	await settle();
	return target;
}

async function save(target: HTMLElement) {
	const saveButton = [...target.querySelectorAll('button')].find((b) => b.textContent?.trim().startsWith('Save'));
	if (!saveButton) throw new Error('save button not rendered');
	saveButton.click();
	await settle();
	return vi.mocked(adminApi.updateSettings).mock.calls.at(-1)![0] as Record<string, unknown>;
}

afterEach(() => {
	document.body.innerHTML = '';
	vi.clearAllMocks();
});

describe('System Settings save body for the diagnostics settings', () => {
	it('sends the profiling switches as booleans and none of the engine knobs', async () => {
		const target = await mountOn('diagnostics', {
			profiling_enabled: false,
			profiling_census: true,
			native_fp8_matmul: true,
			native_sol_attn_debug: true
		});
		rowIn(target, 'profiling_enabled').querySelector<HTMLInputElement>('input[role="switch"]')!.click();
		await settle();

		const body = await save(target);
		expect(body.profiling_enabled).toBe(true);
		expect(body.profiling_census).toBe(true);
		expect(Object.keys(body).filter((k) => k.startsWith('native_'))).toEqual([]);
	});

	it('does not offer a Performance & engine section', async () => {
		const target = await mountOn('diagnostics', {});
		expect(target.textContent).toContain('Diagnostics');
		expect(target.textContent).not.toContain('Performance & engine');
	});
});
