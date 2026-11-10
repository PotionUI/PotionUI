// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { ToolExecution } from '../../src/lib/types/chat';
import { actionCardIcon } from '../../src/lib/chat/actionCard';

const { default: ChatActionPreview } = await import('../../src/lib/components/chat/ChatActionPreview.svelte');

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;

afterEach(() => {
	if (component) unmount(component);
	component = null;
	target?.remove();
});

const execution: ToolExecution = {
	tool_name: 'set_regional_prompt',
	arguments: {},
	result: { success: true, data: '{}' },
	duration_ms: 0,
	approval_status: 'pending',
	preview: {
		action: 'Set regional prompt',
		target: '2 regions',
		items: [],
		kind: 'regions',
		rows: [
			{ position: '10%, 20%', size: '50% × 40%', text: 'A lighthouse on a cliff' },
			{ position: '60%, 5%', size: '30% × 20%', text: 'Title "EXPLORE"' }
		]
	}
};

function render() {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(ChatActionPreview as any, { target, props: { execution } });
	flushSync();
	return target;
}

describe('regions approval preview', () => {
	it('renders one numbered row per region with position, size and description', () => {
		const rows = render().querySelectorAll('[data-testid="action-regions"] .action-timeline-row');
		expect(rows).toHaveLength(2);
		expect(rows[0].textContent).toContain('1');
		expect(rows[0].textContent).toContain('10%, 20% · 50% × 40%');
		expect(rows[0].textContent).toContain('A lighthouse on a cliff');
		expect(rows[1].textContent).toContain('Title "EXPLORE"');
		expect(rows[0].querySelector('.mono')).not.toBeNull();
	});

	it('does not fall back to the generic argument tree', () => {
		const root = render();
		expect(root.querySelector('.action-legacy')).toBeNull();
	});

	it('has a card icon', () => {
		expect(actionCardIcon(execution)).toBe('chat-sliders');
	});
});
