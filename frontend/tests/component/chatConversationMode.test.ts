import { describe, it, expect, vi, afterEach } from 'vitest';
import { tick } from 'svelte';

const { default: ChatModeSelector } = await import('$lib/components/chat/ChatModeSelector.svelte');
const { default: ChatInput } = await import('$lib/components/chat/ChatInput.svelte');
const { createClassComponent } = await import('svelte/legacy');

const modes = [
	{
		id: 'generation',
		name: 'Generation Assistant',
		description: 'Configure generations',
		icon: 'wand-sparkles',
		default_route_prefixes: ['/'],
		tools: ['get_form_state', 'write_memory'],
		source: 'builtin',
		offers_tools: true,
		app_context: true
	},
	{
		id: 'conversation',
		name: 'Conversation',
		description: 'Just talk to the model: no tools and nothing from the page',
		icon: 'chat',
		default_route_prefixes: [],
		tools: [],
		source: 'builtin',
		offers_tools: false,
		app_context: false
	}
];

const tools = [
	{ name: 'write_memory', description: '', hint: '', modes: null, group: 'Memory', label: 'Write memory' }
];

let mounted: Array<{ $destroy: () => void }> = [];

function mount(component: unknown, props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	mounted.push(createClassComponent({ component: component as never, target, props }) as never);
	return target;
}

function toolsButton(target: HTMLElement) {
	return [...target.querySelectorAll<HTMLButtonElement>('button.composer-tool')].find((b) =>
		b.textContent?.includes('Tools')
	);
}

afterEach(() => {
	mounted.forEach((m) => m.$destroy());
	mounted = [];
	document.body.innerHTML = '';
});

describe('Conversation chat mode', () => {
	it('is offered in the mode menu and picking it selects it', async () => {
		const onSelect = vi.fn();
		const target = mount(ChatModeSelector, { modes, selected: 'generation', onSelect });

		target.querySelector<HTMLButtonElement>('[data-testid="chat-mode-selector-trigger"]')?.click();
		await tick();
		const menu = document.querySelector('[data-testid="chat-mode-selector-menu"]');
		const option = [...(menu?.querySelectorAll<HTMLButtonElement>('button') ?? [])].find((b) =>
			b.textContent?.includes('Conversation')
		);
		expect(option).toBeDefined();
		expect(option?.textContent).toContain('no tools and nothing from the page');
		option?.click();

		expect(onSelect).toHaveBeenCalledWith('conversation');
	});

	it('renders no Tools control or tools popover when the mode offers no tools', async () => {
		const target = mount(ChatInput, {
			mode: 'conversation',
			visibleTools: [],
			enableTools: true,
			toolsAvailable: false,
			onToggleEnableTools: vi.fn(),
			onOpenToolPreferences: vi.fn()
		});
		await tick();

		expect(toolsButton(target)).toBeUndefined();
		expect(document.querySelector('.tools-popover')).toBeNull();
		expect(document.body.textContent).not.toContain('Enable tools');
	});

	it('keeps the Tools control for a mode that offers tools', async () => {
		const target = mount(ChatInput, {
			mode: 'generation',
			visibleTools: tools,
			enableTools: true,
			onToggleEnableTools: vi.fn()
		});
		await tick();

		const button = toolsButton(target);
		expect(button).toBeDefined();
		button?.click();
		await tick();
		expect(document.body.textContent).toContain('Enable tools');
	});
});
