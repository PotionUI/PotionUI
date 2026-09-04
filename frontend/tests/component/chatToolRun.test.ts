// @vitest-environment jsdom
//
// ChatToolRun replaced the always-visible chip strip with a collapsible
// record. These prove the three product rulings that survive the redesign:
// the summary counts and names only what it renders, a failed row always
// shows its `result.error` inline (no click-to-reveal), and a pending
// execution never appears here at all — that's ApprovalDock's surface.
import { describe, it, expect, afterEach } from 'vitest';
import type { ToolExecution } from '../../src/lib/types/chat';

const { default: ChatToolRun } = await import('../../src/lib/components/chat/ChatToolRun.svelte');
const { createClassComponent } = await import('svelte/legacy');

function execution(overrides: Partial<ToolExecution> = {}): ToolExecution {
	return {
		tool_name: 'read_active_prompt',
		arguments: {},
		result: { success: true, data: '' },
		duration_ms: 300,
		...overrides
	};
}

function mount(executions: ToolExecution[]) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	createClassComponent({ component: ChatToolRun as never, target, props: { executions } });
	return target;
}

afterEach(() => {
	document.body.innerHTML = '';
});

describe('ChatToolRun', () => {
	it('renders nothing when there are no executions', () => {
		const target = mount([]);
		expect(target.querySelector('details')).toBeNull();
	});

	it('summarizes completed executions by count and humanized name', () => {
		const target = mount([
			execution({ tool_name: 'read_active_prompt' }),
			execution({ tool_name: 'read_generation_settings' })
		]);

		const details = target.querySelector('details');
		expect(details).not.toBeNull();
		expect(details?.textContent).toContain('2 tools used');
		expect(details?.textContent).toContain('Read Active Prompt');
		expect(details?.textContent).toContain('Read Generation Settings');
	});

	it('shows a failed execution\'s result.error inline and opens by default', () => {
		const target = mount([
			execution({
				tool_name: 'update_form_settings',
				result: { success: false, data: '', error: 'Field "steps" is out of range' }
			})
		]);

		const details = target.querySelector('details') as HTMLDetailsElement;
		expect(details.open).toBe(true);
		expect(details.textContent).toContain('Field "steps" is out of range');
	});

	it('excludes pending-approval executions entirely', () => {
		const target = mount([
			execution({ tool_name: 'start_generation', pending_approval: true }),
			execution({ tool_name: 'read_active_prompt' })
		]);

		const details = target.querySelector('details');
		expect(details?.textContent).toContain('1 tool used');
		expect(details?.textContent).not.toContain('Start Generation');
	});
});
