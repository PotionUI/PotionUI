import { describe, it, expect, afterEach, vi } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { settle } from './helpers/settle';
import { modesRequest, slot, variant } from '../../src/lib/components/recipes/modeInstall/modeInstallFixtures';
import type { ModeConsentRequest } from '../../src/lib/services/api/recipeModes';

const { default: ModeInstallSheet } = await import('../../src/lib/components/recipes/modeInstall/ModeInstallSheet.svelte');

const GB = 1_000_000_000;
let instance: ReturnType<typeof mount> | null = null;

function render(request: ModeConsentRequest, props: Record<string, unknown> = {}) {
	const onConfirm = vi.fn();
	const onCancel = vi.fn();
	const target = document.body.appendChild(document.createElement('div'));
	instance = mount(ModeInstallSheet, { target, props: { request, onConfirm, onCancel, ...props } });
	flushSync();
	return { onConfirm, onCancel };
}

const q = <T extends Element>(selector: string) => document.body.querySelector(selector) as T | null;
const all = (selector: string) => Array.from(document.body.querySelectorAll(selector));
const text = () => document.body.textContent ?? '';
const installButton = () =>
	Array.from(document.body.querySelectorAll('button')).find((b) => /^Install( |$)/.test((b.textContent ?? '').trim()) && !b.hasAttribute('data-install-local')) as HTMLButtonElement;

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	document.body.innerHTML = '';
	document.body.style.overflow = '';
});

describe('ModeInstallSheet', () => {
	it('lists every mode, locks installed ones and shows the size of missing ones', () => {
		render(modesRequest());
		const video = q<HTMLInputElement>('[data-mode-row="video"] input')!;
		expect(video.checked).toBe(true);
		expect(video.disabled).toBe(true);
		expect(q('[data-mode-row="video"]')!.textContent).toContain('Installed');
		const cine = q<HTMLInputElement>('[data-mode-row="cinemagraph"] input')!;
		expect(cine.checked).toBe(true);
		expect(cine.disabled).toBe(false);
		expect(q('[data-mode-row="cinemagraph"]')!.textContent).toContain('+ 1.9 GB');
		expect(text()).toContain('Modes 1 of 3 ready');
	});

	it('shows the live totals and updates them when a mode is unticked', async () => {
		render(modesRequest());
		expect(q('[data-total-download]')!.textContent?.trim()).toBe('4.7 GB');
		expect(q('[data-total-free]')!.textContent?.trim()).toBe('745 GB');
		const motion = q<HTMLInputElement>('[data-mode-row="motion"] input')!;
		motion.click();
		flushSync();
		expect(q('[data-total-download]')!.textContent?.trim()).toBe('1.9 GB');
		expect(installButton().textContent).toContain('Install 1 mode');
	});

	it('hides the time cell until the API returns a real speed', () => {
		render(modesRequest({ estimate: null }));
		expect(q('[data-total-time]')).toBeNull();
	});

	it('shows the time estimate with the speed it is based on', () => {
		render(modesRequest());
		expect(q('[data-total-time]')!.textContent).toContain('About 3 min');
		expect(q('[data-total-time]')!.textContent).toContain('31 MB/s');
	});

	it('Select all missing ticks every missing mode, and an unticked mode can be cleared one by one', () => {
		render(modesRequest(), { preselect: ['cinemagraph'] });
		expect(q<HTMLInputElement>('[data-mode-row="motion"] input')!.checked).toBe(false);
		q<HTMLButtonElement>('[data-select-all-missing]')!.click();
		flushSync();
		expect(q<HTMLInputElement>('[data-mode-row="motion"] input')!.checked).toBe(true);
		expect(q<HTMLInputElement>('[data-mode-row="cinemagraph"] input')!.checked).toBe(true);
		expect(q('[data-select-all-missing]')!.textContent).toContain('Select all missing');
		q<HTMLInputElement>('[data-mode-row="motion"] input')!.click();
		q<HTMLInputElement>('[data-mode-row="cinemagraph"] input')!.click();
		flushSync();
		expect(installButton().disabled).toBe(true);
		expect(q('[data-mode-blocker]')!.textContent).toContain('Pick a mode to install');
	});

	it('preselects only the mode a door asked for', () => {
		render(modesRequest(), { preselect: ['motion'] });
		expect(q<HTMLInputElement>('[data-mode-row="motion"] input')!.checked).toBe(true);
		expect(q<HTMLInputElement>('[data-mode-row="cinemagraph"] input')!.checked).toBe(false);
		expect(installButton().textContent).toContain('Install 1 mode');
	});

	it('warns about gated files with a licence link', () => {
		render(modesRequest());
		const gated = q('[data-mode-gated]')!;
		expect(gated.textContent).toContain('Motion transfer');
		expect(gated.textContent).toContain('gated on Hugging Face');
		expect(gated.querySelector('a')!.getAttribute('href')).toBe('https://example.test/licence');
	});

	it('keeps per-slot detail collapsed and preselects the installed variant when opened', () => {
		render(modesRequest());
		expect(q('[data-mode-customize]')).toBeNull();
		expect(text()).toContain('Balanced for your RTX 5090 · 32 GB');
		q<HTMLButtonElement>('[data-customize-toggle]')!.click();
		flushSync();
		const int8 = q('[data-variant="int8"]')!;
		const nvfp4 = q('[data-variant="nvfp4"]')!;
		expect(int8.getAttribute('aria-checked')).toBe('true');
		expect(int8.textContent).toContain('Installed');
		expect(nvfp4.getAttribute('aria-checked')).toBe('false');
		expect(nvfp4.textContent).toContain('Recommended for your RTX 5090');
		expect(q('[data-variant="bf16"]')!.textContent).toContain('Needs about 40 GB');
	});

	it('moves the total and the button label when another variant is picked, and sends the pick', () => {
		const { onConfirm } = render(modesRequest());
		q<HTMLButtonElement>('[data-customize-toggle]')!.click();
		flushSync();
		q<HTMLButtonElement>('[data-variant="nvfp4"]')!.click();
		flushSync();
		expect(q('[data-total-download]')!.textContent?.trim()).toBe('14.9 GB');
		expect(installButton().textContent).toContain('Install 2 modes and nvfp4');
		installButton().click();
		expect(onConfirm).toHaveBeenCalledTimes(1);
		expect(onConfirm.mock.calls[0][0]).toEqual({
			selections: { model: 'nvfp4' },
			modes: ['video', 'cinemagraph', 'motion'],
			extras: [],
			target: 'native'
		});
	});

	it('offers extras unticked and sends them when ticked', () => {
		const { onConfirm } = render(modesRequest());
		q<HTMLButtonElement>('[data-customize-toggle]')!.click();
		flushSync();
		const extra = q<HTMLInputElement>('[data-customize-extras] input')!;
		expect(extra.checked).toBe(false);
		extra.click();
		flushSync();
		installButton().click();
		expect(onConfirm.mock.calls[0][0].extras).toEqual(['preview_decoder']);
	});

	it('disables Install and states the reason when the target disk is too small', () => {
		const request = modesRequest();
		request.target = { ...request.target, backend_id: 'pod', label: 'Worker pod', kind: 'remote' };
		request.disk = [{ id: 'd0', target: 'pod', label: 'Models folder', free_bytes: 3.1 * GB, reserve_bytes: 2 * GB }];
		const { onConfirm } = render(request);
		expect(q('[data-mode-disk-short]')!.textContent).toContain('Not enough space');
		expect(q('[data-mode-blocker]')!.textContent).toContain('Deselect');
		expect(installButton().disabled).toBe(true);
		installButton().click();
		expect(onConfirm).not.toHaveBeenCalled();
	});

	it('re-plans for the other target and swaps in its disk and modes', async () => {
		const request = modesRequest();
		const replanned = modesRequest({
			disk: [{ id: 'd0', target: 'pod', label: 'Models folder', free_bytes: 3.1 * GB, reserve_bytes: 2 * GB }]
		});
		replanned.target = { ...replanned.target, backend_id: 'pod', kind: 'remote', label: 'Worker pod' };
		const onReplan = vi.fn().mockResolvedValue(replanned);
		render(request, { onReplan });
		expect(q('[data-mode-target]')).not.toBeNull();
		q<HTMLButtonElement>('[data-target="pod"]')!.click();
		await settle();
		flushSync();
		expect(onReplan).toHaveBeenCalledWith('pod', ['cinemagraph', 'motion']);
		expect(q('[data-mode-disk-short]')).not.toBeNull();
		expect(q('[data-target="pod"]')!.getAttribute('aria-checked')).toBe('true');
		q<HTMLButtonElement>('[data-install-local]')!.click();
		expect(onReplan).toHaveBeenLastCalledWith('native', ['cinemagraph', 'motion']);
	});

	it('shows no target picker when there is only one place to install', () => {
		const request = modesRequest();
		request.target.options = [request.target.options[0]];
		render(request);
		expect(q('[data-mode-target]')).toBeNull();
	});

	it('cancels through the footer button', () => {
		const { onCancel } = render(modesRequest());
		const cancel = Array.from(document.body.querySelectorAll('button')).find((b) => (b.textContent ?? '').includes('Cancel')) as HTMLButtonElement;
		cancel.click();
		expect(onCancel).toHaveBeenCalled();
	});

	it('lists manual slots as a file to pick in Models and never counts them', () => {
		const request = modesRequest();
		request.slots[1] = slot('effect_lora@cinemagraph', {
			label: 'Cinemagraph LoRA',
			manual: true,
			used_by: ['cinemagraph'],
			variants: [variant('manual', { size_bytes: null })]
		});
		render(request, { preselect: ['cinemagraph'] });
		expect(q('[data-total-download]')!.textContent?.trim()).toBe('0 B');
		q<HTMLButtonElement>('[data-customize-toggle]')!.click();
		flushSync();
		expect(q('[data-other-slot="effect_lora@cinemagraph"]')!.textContent).toContain('Pick your own file in Models');
	});
});
