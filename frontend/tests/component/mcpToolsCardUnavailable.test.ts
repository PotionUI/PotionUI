// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';

const hoisted = vi.hoisted(() => ({ error: vi.fn() }));

vi.mock('$lib/services/api/index', () => ({
	api: { getMcpTools: vi.fn().mockRejectedValue({ response: { status: 404 } }), getMcpKeyTools: vi.fn() }
}));
vi.mock('$lib/stores/toast', () => ({ toasts: { error: hoisted.error, success: vi.fn() } }));

const { default: McpToolsCard } = await import('../../src/lib/components/apikeys/McpToolsCard.svelte');

let target: HTMLDivElement;
let instance: ReturnType<typeof mount> | null = null;

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	target?.remove();
});

describe('McpToolsCard when the routes answer 404', () => {
	it('shows a plain unavailable state without a toast or an error', async () => {
		target = document.createElement('div');
		document.body.appendChild(target);
		instance = mount(McpToolsCard, { target, props: { keys: [], mcpEnabled: true } });
		for (let i = 0; i < 6; i++) await new Promise((r) => setTimeout(r, 0));
		flushSync();
		expect(target.textContent).toContain('MCP tools are not available yet');
		expect(target.querySelector('[data-root]')).toBeNull();
		expect(target.textContent).not.toContain('could not be loaded');
		expect(hoisted.error).not.toHaveBeenCalled();
	});
});
