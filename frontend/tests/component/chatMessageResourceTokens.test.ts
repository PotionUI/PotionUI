// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';

const { default: ChatMessage } = await import('../../src/lib/components/ChatMessage.svelte');
const { createClassComponent } = await import('svelte/legacy');

function mount(props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	createClassComponent({ component: ChatMessage as never, target, props });
	return target;
}

afterEach(() => {
	document.body.innerHTML = '';
});

const content =
	'Try this:\n<tool_action type="update_segment" segment_index="0" segment_id="seg-1"><Picture 2> hugs <Picture 7></tool_action>';

const resourceProps = {
	promptResources: [{ field: 'references', kind: 'image', label: 'Pictures', token: '<Picture @>' }],
	resourceFormValues: { references: ['uploads/a.png', 'uploads/b.png'] },
	resourceFieldLabels: { references: 'Reference images' },
	onApplyAction: () => {}
};

describe('ChatMessage resource tokens in the apply preview', () => {
	it('marks linked tokens as references and warns about unresolved ones', () => {
		const target = mount({ role: 'assistant', content, ...resourceProps });

		const linked = target.querySelectorAll('[data-resource-token="linked"]');
		expect(linked).toHaveLength(1);
		expect(linked[0].textContent).toBe('@Picture 2');
		const unresolved = target.querySelectorAll('[data-resource-token="unresolved"]');
		expect(Array.from(unresolved).map((node) => node.textContent)).toEqual(['<Picture 7>']);
		const warnings = target.querySelectorAll('[data-resource-warning]');
		expect(Array.from(warnings).map((node) => node.textContent)).toEqual([
			'<Picture 7> stays plain text: Reference images has 2 items'
		]);
	});

	it('shows the plain text without resources declared', () => {
		const target = mount({ role: 'assistant', content, onApplyAction: () => {} });

		expect(target.querySelector('[data-resource-token]')).toBeNull();
		expect(target.querySelector('[data-resource-warning]')).toBeNull();
		expect(target.querySelector('.prompt-copy')?.textContent).toContain('<Picture 2> hugs <Picture 7>');
	});
});
