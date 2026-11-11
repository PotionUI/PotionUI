// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';

const tool = (name: string, over: Record<string, unknown> = {}) => ({
	name,
	label: name,
	group: 'Generation',
	description: `Description of ${name}.`,
	writes: false,
	spends: false,
	unavailable_reason: null,
	state: 'on',
	reviewed: true,
	...over
});

const summary = (tools: any[]) => ({
	exposed: tools.filter((t) => t.state === 'on').length,
	total: tools.filter((t) => !['unavailable', 'locked_by_admin'].includes(t.state)).length,
	writes_exposed: 0,
	unavailable: tools.filter((t) => t.state === 'unavailable').length,
	locked: tools.filter((t) => t.state === 'locked_by_admin').length,
	unreviewed: 0
});

const defaultTools = [
	tool('search_gallery'),
	tool('start_generation', { writes: true, spends: true, reviewed: false, state: 'off' }),
	tool('enhance_prompt', { spends: true, reviewed: false, state: 'off' }),
	tool('run_media_tool', { writes: true, state: 'locked_by_admin' }),
	tool('run_generation', { writes: true, state: 'unavailable', unavailable_reason: 'Use start_generation over MCP.' })
];
const keyTools = defaultTools.map((t) => (t.name === 'start_generation' || t.name === 'enhance_prompt' ? { ...t, state: 'off_in_default' } : t));

const hoisted = vi.hoisted(() => ({ getMcpTools: vi.fn(), getMcpKeyTools: vi.fn(), error: vi.fn() }));

vi.mock('$lib/services/api/index', () => ({
	api: { getMcpTools: hoisted.getMcpTools, getMcpKeyTools: hoisted.getMcpKeyTools, patchMcpTools: vi.fn(), patchMcpKeyTools: vi.fn() }
}));
vi.mock('$lib/stores/toast', () => ({ toasts: { error: hoisted.error, success: vi.fn() } }));

const { default: McpToolsCard } = await import('../../src/lib/components/apikeys/McpToolsCard.svelte');
const { makeKey } = await import('../../src/lib/components/apikeys/fixtures');

let target: HTMLDivElement;
let instance: ReturnType<typeof mount> | null = null;

async function settle() {
	for (let i = 0; i < 8; i++) await new Promise((r) => setTimeout(r, 0));
	flushSync();
}

async function open(props: Record<string, unknown>) {
	target = document.createElement('div');
	document.body.appendChild(target);
	instance = mount(McpToolsCard, { target, props: { keys: [], mcpEnabled: true, ...props } });
	await settle();
}

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	target?.remove();
	vi.clearAllMocks();
});

const row = (name: string) => target.querySelector<HTMLElement>(`[data-tool="${name}"]`)!;

describe('McpToolsCard against the recorded route shapes', () => {
	it('renders the default level with every state, the count and the new chip', async () => {
		hoisted.getMcpTools.mockResolvedValue({
			success: true,
			data: { level: 'default', key_id: null, policy: 'read_only_on', ceiling_policy: 'all_on', summary: summary(defaultTools), tools: defaultTools }
		});
		await open({});
		expect(target.textContent).toContain('1 of 3 on');
		expect(target.textContent).not.toContain('not available yet');
		expect(row('start_generation').textContent).toContain('new');
		expect(row('run_media_tool').textContent).toContain('Turned off by your administrator.');
		expect(row('run_generation').textContent).toContain('Use start_generation over MCP.');
		expect(target.querySelector('[role="radiogroup"][aria-label="New tools"]')).not.toBeNull();
	});

	it('draws a tool that spends exactly like any other row', async () => {
		hoisted.getMcpTools.mockResolvedValue({
			success: true,
			data: { level: 'default', key_id: null, policy: 'read_only_on', ceiling_policy: 'all_on', summary: summary(defaultTools), tools: defaultTools }
		});
		await open({});
		expect(row('enhance_prompt').textContent?.replace(/\s+/g, ' ').trim()).toBe('enhance_prompt new Description of enhance_prompt.');
		expect(target.textContent).not.toMatch(/spend/i);
	});

	it('loads one key, shows off in your default with a link back, and no policy row', async () => {
		hoisted.getMcpTools.mockResolvedValue({
			success: true,
			data: { level: 'default', key_id: null, policy: 'read_only_on', ceiling_policy: 'all_on', summary: summary(defaultTools), tools: defaultTools }
		});
		hoisted.getMcpKeyTools.mockResolvedValue({
			success: true,
			data: { level: 'key', key_id: 'k1', ceiling_policy: 'all_on', summary: summary(keyTools), tools: keyTools }
		});
		await open({ keys: [makeKey({ id: 'k1' })], selectedKeyId: 'k1' });
		expect(hoisted.getMcpKeyTools).toHaveBeenCalledWith('k1');
		expect(row('start_generation').textContent).toContain('Off in your default.');
		expect(target.querySelector('[role="radiogroup"]')).toBeNull();
		expect(target.textContent).toContain('Match default');
		const link = Array.from(row('start_generation').querySelectorAll('button')).find((b) => b.textContent?.trim() === 'Change default')!;
		link.click();
		await settle();
		expect(hoisted.getMcpTools).toHaveBeenCalledTimes(1);
	});

	it('shows the unavailable state only when the default route answers 404', async () => {
		hoisted.getMcpTools.mockRejectedValue({ response: { status: 404 } });
		await open({});
		expect(target.textContent).toContain('MCP tools are not available yet.');
	});

	it('does not call a missing key unavailable', async () => {
		hoisted.getMcpTools.mockResolvedValue({
			success: true,
			data: { level: 'default', key_id: null, policy: 'read_only_on', ceiling_policy: 'all_on', summary: summary(defaultTools), tools: defaultTools }
		});
		hoisted.getMcpKeyTools.mockRejectedValue({ response: { status: 404 } });
		await open({ keys: [makeKey({ id: 'k1' })], selectedKeyId: 'k1' });
		expect(target.textContent).not.toContain('not available yet');
	});
});
