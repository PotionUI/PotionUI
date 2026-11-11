// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import McpToolsList from '../../src/lib/components/apikeys/McpToolsList.svelte';
import ApiKeysCard from '../../src/lib/components/apikeys/ApiKeysCard.svelte';
import { LIMITS, makeKey, makeListing, SAMPLE_TOOLS } from '../../src/lib/components/apikeys/fixtures';

let target: HTMLDivElement;
let instance: ReturnType<typeof mount> | null = null;

function render(component: any, props: Record<string, unknown>) {
	target = document.createElement('div');
	document.body.appendChild(target);
	instance = mount(component, { target, props });
	flushSync();
}

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	target?.remove();
});

const row = (name: string) => target.querySelector<HTMLElement>(`[data-tool="${name}"]`)!;

describe('McpToolsList', () => {
	it('shows the count line, the policy row and the writes marking at the default level', () => {
		render(McpToolsList, { listing: makeListing(SAMPLE_TOOLS), onChange: vi.fn() });
		expect(target.textContent).toContain('3 of 6 on');
		expect(target.querySelector('[role="radiogroup"][aria-label="New tools"]')).not.toBeNull();
		expect(row('start_generation').textContent).toContain('writes');
		expect(target.textContent).toContain('Turn off writes');
	});

	it('emits one override for one toggle', () => {
		const onChange = vi.fn();
		render(McpToolsList, { listing: makeListing(SAMPLE_TOOLS), onChange });
		row('search_gallery').querySelector<HTMLInputElement>('input[role="switch"]')!.click();
		expect(onChange).toHaveBeenCalledWith({ overrides: { search_gallery: false } });
	});

	it('shows the reason for an unavailable tool and disables locked rows', () => {
		render(McpToolsList, { listing: makeListing(SAMPLE_TOOLS), onChange: vi.fn() });
		expect(row('run_generation').textContent).toContain('Use start_generation over MCP.');
		expect(row('run_media_tool').textContent).toContain('Turned off by your administrator.');
		expect(row('run_media_tool').querySelector<HTMLInputElement>('input')!.disabled).toBe(true);
	});

	it('marks an unreviewed tool with the new chip', () => {
		render(McpToolsList, { listing: makeListing(SAMPLE_TOOLS), onChange: vi.fn() });
		expect(row('organize_gallery').textContent).toContain('new');
	});

	it('at a key level hides the policy row, offers Match default and links back to the default', async () => {
		const onChangeDefault = vi.fn();
		const onChange = vi.fn();
		render(McpToolsList, { listing: makeListing(SAMPLE_TOOLS, { level: 'key', key_id: 'k1', policy: undefined }), onChange, onChangeDefault });
		expect(target.querySelector('[role="radiogroup"]')).toBeNull();
		const open = Array.from(target.querySelectorAll<HTMLButtonElement>('button')).find((b) => b.textContent?.includes('Saved prompts'))!;
		open.click();
		flushSync();
		const link = Array.from(row('list_prompts').querySelectorAll('button')).find((b) => b.textContent?.trim() === 'Change default')!;
		link.click();
		expect(onChangeDefault).toHaveBeenCalled();
		expect(row('list_prompts').textContent).toContain('Off in your default.');
		const match = Array.from(target.querySelectorAll<HTMLButtonElement>('button')).find((b) => b.textContent?.trim() === 'Match default')!;
		expect(match.disabled).toBe(false);
	});

	it('turning a key tool back on sends null, never true', () => {
		const onChange = vi.fn();
		const tools = SAMPLE_TOOLS.map((t) => (t.name === 'organize_gallery' ? { ...t, state: 'off' as const } : t));
		render(McpToolsList, { listing: makeListing(tools, { level: 'key', key_id: 'k1', policy: undefined }), onChange });
		row('organize_gallery').querySelector<HTMLInputElement>('input')!.click();
		expect(onChange).toHaveBeenCalledWith({ overrides: { organize_gallery: null } });
	});

	it('shows an empty view with a way out when the search matches nothing', () => {
		render(McpToolsList, { listing: makeListing(SAMPLE_TOOLS), onChange: vi.fn() });
		const input = target.querySelector<HTMLInputElement>('input[type="search"]')!;
		input.value = 'zzzz';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		expect(target.textContent).toContain('No tools match “zzzz”');
	});
});

describe('ApiKeysCard', () => {
	const base = { limits: LIMITS, loading: false, failed: false, onCreate: vi.fn(), onEdit: vi.fn(), onRevoke: vi.fn(), onRetry: vi.fn() };

	it('lists active keys, hides inactive ones behind a toggle and shows no pager for one page', () => {
		const keys = [makeKey({ id: 'a' }), makeKey({ id: 'b', status: 'revoked' }), makeKey({ id: 'c', status: 'expired' })];
		render(ApiKeysCard, { ...base, keys });
		expect(target.querySelectorAll('[data-key-row]')).toHaveLength(1);
		expect(target.querySelector('nav[aria-label="Pagination"]')).toBeNull();
		const toggle = Array.from(target.querySelectorAll('button')).find((b) => b.textContent?.includes('Show 2 inactive'))!;
		toggle.click();
		flushSync();
		expect(target.querySelectorAll('[data-key-row]')).toHaveLength(3);
		expect(target.querySelector('[data-key-row="b"] button')).toBeNull();
		expect(target.textContent).toContain('Hide inactive');
	});

	it('pages past ten keys', () => {
		const keys = Array.from({ length: 12 }, (_, i) => makeKey({ id: `k${i}` }));
		render(ApiKeysCard, { ...base, keys });
		expect(target.querySelectorAll('[data-key-row]')).toHaveLength(10);
		expect(target.querySelector('nav[aria-label="Pagination"]')).not.toBeNull();
	});

	it('shows the empty state with one Create key button', () => {
		render(ApiKeysCard, { ...base, keys: [] });
		expect(target.textContent).toContain('No keys yet');
		expect(Array.from(target.querySelectorAll('button')).filter((b) => b.textContent?.includes('Create key'))).toHaveLength(1);
	});

	it('disables Create and says why when the administrator switched keys off', () => {
		render(ApiKeysCard, { ...base, keys: [makeKey()], limits: { ...LIMITS, keys_enabled: false } });
		expect(target.textContent).toContain('API keys are turned off by your administrator. You can still revoke them.');
		const create = Array.from(target.querySelectorAll<HTMLButtonElement>('button')).find((b) => b.textContent?.includes('Create key'))!;
		expect(create.disabled).toBe(true);
		expect(target.querySelector('[data-key-row] [aria-label="Revoke key"]')).not.toBeNull();
	});

	it('shows a badge only for a key that is not normal', () => {
		render(ApiKeysCard, { ...base, keys: [makeKey({ id: 'a' }), makeKey({ id: 'b', expires_at: new Date(Date.now() + 2 * 864e5).toISOString() })] });
		expect(target.querySelector('[data-key-row="a"]')!.textContent).not.toMatch(/Expires|Revoked|Expired/);
		expect(target.querySelector('[data-key-row="b"]')!.textContent).toMatch(/Expires in/);
	});
});
