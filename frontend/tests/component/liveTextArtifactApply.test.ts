import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { get } from 'svelte/store';
import { flushSync } from 'svelte';

const { tabsStore } = await import('$lib/stores/tabs');
const { generationMessageRegistry } = await import('$lib/registries/generationMessageRegistry');
await import('$lib/generation/messages/pipeArtifact');
const { default: LiveTextArtifacts } = await import(
	'$lib/components/generation/LiveTextArtifacts.svelte'
);
const { default: TextArtifact } = await import(
	'$lib/components/generation/artifacts/TextArtifact.svelte'
);
const { createClassComponent } = await import('svelte/legacy');

const GENERATED_ABC = 'X:1\nT:Generated\nM:4/4\nL:1/8\nK:D\n"D"d2 f2 a2 f2 | "G"g2 b2 d\'4 |]\n';

const FIXTURE_MESSAGE = {
	type: 'pipe_artifact',
	generation_id: 'gen-1',
	pipe_id: 1,
	pipe_name: 'generator',
	output_type: 'text',
	index: 0,
	artifact_type: 'text',
	artifact_data: {
		index: 0,
		title: 'ABC transcription · melody',
		text: GENERATED_ABC,
		mono: true,
		action: { label: 'Use as ABC', field: 'abc', values: { cot: 'melody' } }
	}
};

function currentTab(tabId: string) {
	const tab = get(tabsStore).tabs.find((t) => t.id === tabId);
	if (!tab) throw new Error('tab missing');
	return tab;
}

function deliver(tabId: string, message: Record<string, unknown>) {
	const handler = generationMessageRegistry.get('pipe_artifact');
	if (!handler) throw new Error('pipe_artifact handler not registered');
	handler.handle(message as never, {
		tabId,
		tab: currentTab(tabId),
		tabsStore,
		generationId: 'gen-1',
		unsubscribe: () => {}
	});
}

function mount(component: unknown, props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const instance = createClassComponent({ component: component as never, target, props });
	flushSync();
	return {
		target,
		destroy: () => {
			instance.$destroy();
			target.remove();
		}
	};
}

function button(root: ParentNode, text: string): HTMLButtonElement | undefined {
	return Array.from(root.querySelectorAll('button')).find(
		(b) => (b.textContent || '').trim() === text
	) as HTMLButtonElement | undefined;
}

describe('text artifact apply', () => {
	let tabId: string;
	let cleanup: (() => void) | null = null;

	beforeEach(() => {
		tabsStore.reset();
		tabId = tabsStore.addTabWithData('YuE2', {
			selectedPreset: 'YuE2',
			formData: { style: 'folk, acoustic guitar', cot: 'off', abc: '', seed: 4242 }
		});
	});

	afterEach(() => {
		cleanup?.();
		cleanup = null;
	});

	it('writes the delivered ABC into the abc field and switches cot to the matching mode', () => {
		deliver(tabId, FIXTURE_MESSAGE);
		const mounted = mount(LiveTextArtifacts, { tab: currentTab(tabId) });
		cleanup = mounted.destroy;

		expect(mounted.target.textContent).toContain('ABC transcription · melody');
		expect(mounted.target.querySelector('pre')?.textContent).toBe(GENERATED_ABC);

		const apply = button(mounted.target, 'Use as ABC');
		expect(apply).toBeDefined();
		apply!.click();
		flushSync();

		expect(currentTab(tabId).formData).toEqual({
			style: 'folk, acoustic guitar',
			cot: 'melody',
			abc: GENERATED_ABC,
			seed: 4242
		});
	});

	it('renders nothing when the run carried no text artifact', () => {
		deliver(tabId, {
			...FIXTURE_MESSAGE,
			artifact_type: 'seed',
			output_type: 'seed',
			artifact_data: { seed: 1 }
		});
		const mounted = mount(LiveTextArtifacts, { tab: currentTab(tabId) });
		cleanup = mounted.destroy;

		expect(mounted.target.querySelector('[data-text-artifact]')).toBeNull();
	});

	it('offers copy but no apply where there is no form to write into', () => {
		const mounted = mount(TextArtifact, { artifact: { artifact_data: FIXTURE_MESSAGE.artifact_data } });
		cleanup = mounted.destroy;

		expect(button(mounted.target, 'Use as ABC')).toBeUndefined();
		expect(mounted.target.querySelector('button[aria-label="Copy ABC transcription · melody"]')).not.toBeNull();
	});
});
