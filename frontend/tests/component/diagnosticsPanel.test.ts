import { describe, it, expect, vi, afterEach } from 'vitest';

const { default: DiagnosticsPanel } = await import(
	'../../src/routes/admin/components/settings/DiagnosticsPanel.svelte'
);
const { createClassComponent } = await import('svelte/legacy');

async function settle() {
	for (let i = 0; i < 8; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

function mount(settings: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const onSettingChange = vi.fn();
	const instance = createClassComponent({
		component: DiagnosticsPanel as never,
		target,
		props: { onSettingChange, settings } as Record<string, unknown>
	}) as unknown as { $set: (props: Record<string, unknown>) => void };
	return { target, onSettingChange, instance };
}

function switchIn(target: HTMLElement, key: string): HTMLInputElement {
	const el = target.querySelector<HTMLInputElement>(`[data-testid="runtime-setting-${key}"] input[role="switch"]`);
	if (!el) throw new Error(`switch ${key} not rendered`);
	return el;
}

afterEach(() => {
	document.body.innerHTML = '';
	vi.clearAllMocks();
});

describe('DiagnosticsPanel', () => {
	it('renders only the profiling and census switches, with the defaults when settings are empty', () => {
		const { target } = mount({});
		expect(target.querySelectorAll('input[role="switch"]')).toHaveLength(2);
		expect(switchIn(target, 'profiling_enabled').checked).toBe(false);
		expect(switchIn(target, 'profiling_census').checked).toBe(true);
		expect(target.textContent).not.toContain('Sol-Attn');
		expect(target.textContent).not.toContain('Debug logging');
	});

	it('sends a boolean when profiling is switched on', async () => {
		const { target, onSettingChange } = mount({ profiling_enabled: false });
		switchIn(target, 'profiling_enabled').click();
		await settle();
		expect(onSettingChange).toHaveBeenCalledWith('profiling_enabled', true);
	});

	it('disables the census while profiling is off', () => {
		const { target } = mount({ profiling_enabled: false });
		expect(switchIn(target, 'profiling_census').disabled).toBe(true);
	});

	it('enables the census once profiling is on, and sends its own boolean', async () => {
		const { target, onSettingChange } = mount({ profiling_enabled: true, profiling_census: true });
		const census = switchIn(target, 'profiling_census');
		expect(census.disabled).toBe(false);
		census.click();
		await settle();
		expect(onSettingChange).toHaveBeenCalledWith('profiling_census', false);
	});

	it('re-enables the census when the profiling setting turns on', async () => {
		const { target, instance } = mount({ profiling_enabled: false });
		instance.$set({ settings: { profiling_enabled: true } });
		await settle();
		expect(switchIn(target, 'profiling_census').disabled).toBe(false);
	});

	it('explains where profiles are stored, how to read them, and when the census runs', () => {
		const { target } = mount({});
		const text = target.textContent ?? '';
		expect(text).toContain('profiles/<generation id>/');
		expect(text).toContain('profile.jsonl and generation.log');
		expect(text).toContain('Resource profile');
		expect(text).toContain('runs after the generation finishes');
		expect(text).toContain('Default: Off');
		expect(text).toContain('Default: On');
	});
});
