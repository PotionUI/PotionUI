import { describe, it, expect, afterEach } from 'vitest';
import { flushSync } from 'svelte';
import type { ToolExecution } from '../../src/lib/types/chat';

const { default: ChatBehaviorTrace } = await import('../../src/lib/components/chat/ChatBehaviorTrace.svelte');
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

afterEach(() => {
	document.body.innerHTML = '';
});

describe('streamed tool disclosure', () => {
	it('stays open while later tool and trace updates stream in', async () => {
		const target = document.createElement('div');
		document.body.appendChild(target);
		const component = createClassComponent({
			component: ChatBehaviorTrace as never,
			target,
			props: {
				executions: [execution()],
				traceSteps: [{ seq: 0, step: 'tools', state: 'completed', detail: {} }],
				isStreaming: true
			}
		});
		const details = target.querySelector('details') as HTMLDetailsElement;
		details.open = true;
		details.dispatchEvent(new Event('toggle'));

		for (let i = 0; i < 4; i++) {
			component.$set({
				executions: [execution(), execution({ tool_name: `tool_${i}`, status: i % 2 ? 'running' : undefined })],
				traceSteps: [
					{ seq: 0, step: 'tools', state: 'completed', detail: {} },
					{ seq: 1 + i, step: 'thinking', state: 'started', detail: {} }
				]
			});
			flushSync();
			await Promise.resolve();
			const current = target.querySelector('details') as HTMLDetailsElement;
			expect(current).toBe(details);
			expect(current.open).toBe(true);
		}
	});
});
