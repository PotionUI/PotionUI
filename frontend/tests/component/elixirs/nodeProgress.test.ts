import { afterEach, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

const { default: NodeStatusRow } = await import('$lib/components/elixirs/NodeStatusRow.svelte');
const { nodeRunView } = await import('$lib/elixirs/runs');

let component: ReturnType<typeof mount> | undefined;
let target: HTMLDivElement;

afterEach(() => {
	if (component) unmount(component);
	component = undefined;
	target?.remove();
});

function render(invocations: Record<string, unknown>[], nodeStatus: string) {
	const run = { id: 'r1', status: 'running', node_states: { photo: { status: nodeStatus } }, invocations };
	const view = nodeRunView(run as never, { id: 'photo', kind: 'preset' } as never);
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(NodeStatusRow, { target, props: { view } });
	flushSync();
}

const one = (over: Record<string, unknown>) => ({ node_id: 'photo', edge_id: null, input_key: 'a', ordinal: 0, attempt: 1, ...over });

it('shows a progress bar with its value while the step generates', () => {
	render([one({ status: 'running', progress: 0.45 })], 'running');
	const bar = target.querySelector('[role="progressbar"]')!;
	expect(bar.getAttribute('aria-valuenow')).toBe('45');
	expect(bar.getAttribute('aria-valuemin')).toBe('0');
	expect(bar.getAttribute('aria-valuemax')).toBe('100');
	expect(target.textContent).toContain('45%');
});

it('aggregates several invocations into a count and one percentage', () => {
	render(
		[
			one({ input_key: 'a', ordinal: 0, status: 'completed' }),
			one({ input_key: 'b', ordinal: 1, status: 'completed' }),
			one({ input_key: 'c', ordinal: 2, status: 'running', progress: 0.35 })
		],
		'running'
	);
	expect(target.textContent).toContain('Generating · 2 of 3');
	expect(target.querySelector('[role="progressbar"]')!.getAttribute('aria-valuenow')).toBe('78');
	expect(target.querySelector('[data-testid="node-progress-percent"]')!.textContent).toBe('78%');
});

it('shows no bar once the step is done', () => {
	render([one({ status: 'completed', progress: 1 })], 'done');
	expect(target.querySelector('[role="progressbar"]')).toBeNull();
	expect(target.textContent).not.toContain('%');
});
