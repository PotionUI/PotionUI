// @vitest-environment jsdom
//
// SavePromptModal narrows the tab's full variable map down to what the
// segments being saved actually reference (plus transitive `when`
// dependencies) via selectReferencedVariables, and shows a one-line summary
// of that subset — end to end through the real mounted component, not a
// mock of the selector.
import { describe, it, expect, afterEach } from 'vitest';
import type { Segment } from '$lib/types/segments';
import type { VariablesMap } from '$lib/utils/variableDefs';

const { default: SavePromptModal } = await import('../../src/lib/components/modals/SavePromptModal.svelte');
const { createClassComponent } = await import('svelte/legacy');

function mountModal(segments: Segment[], variables: VariablesMap) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: SavePromptModal as never,
		target,
		props: { isOpen: true, segments, variables }
	});
	return { target, component, destroy: () => component.$destroy() };
}

let mounted: ReturnType<typeof mountModal> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	document.body.innerHTML = '';
});

describe('SavePromptModal variables summary', () => {
	it('shows referenced variables plus their transitive when-dependency, excluding unused ones', () => {
		const variables: VariablesMap = {
			music: { type: 'choice', options: ['hip hop', 'classical'], mode: 'shuffle', pinnedIndex: null },
			dance: {
				type: 'choice',
				mode: 'shuffle',
				pinnedIndex: null,
				options: [{ text: 'breaking', when: { var: 'music', values: ['hip hop'] } }, 'freestyle']
			},
			unused: { type: 'text', value: 'never referenced' }
		};
		const segments: Segment[] = [{ id: 's1', content: 'a ${dance} scene', enabled: true }];
		mounted = mountModal(segments, variables);

		expect(document.body.textContent).toContain('Includes 2 variables · 1 condition');
		expect(document.body.textContent).not.toContain('unused');
	});

	it('renders no summary line when the saved segments reference no variables', () => {
		const variables: VariablesMap = { mood: { type: 'text', value: 'moody' } };
		const segments: Segment[] = [{ id: 's1', content: 'a plain scene', enabled: true }];
		mounted = mountModal(segments, variables);

		expect(document.body.textContent).not.toContain('Includes');
	});
});
