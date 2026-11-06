import { describe, it, expect, afterEach, vi } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { modesRequest } from '../../src/lib/components/recipes/modeInstall/modeInstallFixtures';
import type { ModeConsentRequest } from '../../src/lib/services/api/recipeModes';

const { default: ModeInstallSheet } = await import('../../src/lib/components/recipes/modeInstall/ModeInstallSheet.svelte');

type SheetProps = { request: ModeConsentRequest | null; stepKey: string };

let instance: ReturnType<typeof mount> | null = null;

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	document.body.innerHTML = '';
	document.body.style.overflow = '';
});

function mountSheet(props: SheetProps) {
	instance = mount(ModeInstallSheet, {
		target: document.body.appendChild(document.createElement('div')),
		props: Object.assign(props, { onConfirm: vi.fn(), onCancel: vi.fn() })
	});
	flushSync();
}

const motion = () => document.body.querySelector<HTMLInputElement>('[data-mode-row="motion"] input')!;

describe('ModeInstallSheet choice lifetime', () => {
	it('keeps the ticked modes when the same step delivers a new consent object', () => {
		const props = $state<SheetProps>({ request: modesRequest(), stepKey: 'run:modes.plan' });
		mountSheet(props);
		expect(motion().checked).toBe(true);
		motion().click();
		flushSync();
		expect(motion().checked).toBe(false);
		props.request = modesRequest();
		flushSync();
		expect(motion().checked).toBe(false);
	});

	it('resets the choice when the step changes', () => {
		const props = $state<SheetProps>({ request: modesRequest(), stepKey: 'run:modes.plan' });
		mountSheet(props);
		motion().click();
		flushSync();
		expect(motion().checked).toBe(false);
		props.request = modesRequest();
		props.stepKey = 'run2:modes.plan';
		flushSync();
		expect(motion().checked).toBe(true);
	});

	it('fills in from the first request after loading', () => {
		const props = $state<SheetProps>({ request: null, stepKey: '' });
		mountSheet(props);
		expect(document.body.querySelector('[data-mode-install-loading]')).not.toBeNull();
		props.request = modesRequest();
		props.stepKey = 'run:modes.plan';
		flushSync();
		expect(motion().checked).toBe(true);
	});
});
