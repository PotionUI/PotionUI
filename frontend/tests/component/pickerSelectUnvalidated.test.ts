import { describe, it, expect, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { settle } from './helpers/settle';

const { default: PickerSelect } = await import('../../src/lib/components/preset/PickerSelect.svelte');
const { presetModeOptions } = await import('../../src/lib/utils/presetModeOptions');

let instance: ReturnType<typeof mount> | null = null;

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	document.body.innerHTML = '';
});

async function openWith(options: ReturnType<typeof presetModeOptions>, value: string) {
	const target = document.body.appendChild(document.createElement('div'));
	instance = mount(PickerSelect, { target, props: { label: 'Mode', value, options, onchange: () => {} } });
	flushSync();
	target.querySelector('button')!.click();
	flushSync();
	await settle();
	return target;
}

describe('mode picker unvalidated badge', () => {
	const options = presetModeOptions([
		{ id: 'txt2img', label: 'Text to image' },
		{ id: 'inpaint', label: 'Inpaint', unvalidated: true }
	]);

	it('badges only the unvalidated mode row', async () => {
		await openWith(options, 'txt2img');
		const rows = Array.from(document.body.querySelectorAll('[role="option"]'));
		const byName = (name: string) => rows.find((row) => row.textContent?.includes(name))!;
		expect(byName('Inpaint').querySelector('[data-unvalidated-badge]')?.textContent).toContain('Not validated');
		expect(byName('Text to image').querySelector('[data-unvalidated-badge]')).toBeNull();
	});

	it('badges the closed trigger when the selected mode is unvalidated', async () => {
		const target = document.body.appendChild(document.createElement('div'));
		instance = mount(PickerSelect, { target, props: { label: 'Mode', value: 'inpaint', options, onchange: () => {} } });
		flushSync();
		expect(target.querySelector('[data-unvalidated-badge]')?.textContent).toContain('Not validated');
	});

	it('shows no badge when no mode is flagged', async () => {
		await openWith(presetModeOptions([{ id: 'txt2img', label: 'Text to image' }]), 'txt2img');
		expect(document.body.querySelector('[data-unvalidated-badge]')).toBeNull();
	});
});
