// @vitest-environment jsdom
//
// Covers the scope-mismatch notice on the locked mode chip (see
// deriveModeScopeMismatch in $lib/chat/modeScopeNotice.ts): a conversation
// that started on one page's scope and is now viewed from another page's
// scope must say so and offer the same "new chat" action ChatHistoryView's
// control triggers, and must render nothing once the scopes agree.
import { describe, it, expect, vi } from 'vitest';

const { default: ChatModeSelector } = await import(
	'$lib/components/chat/ChatModeSelector.svelte'
);
const { createClassComponent } = await import('svelte/legacy');

const modes = [
	{
		id: 'history',
		name: 'History',
		description: '',
		default_route_prefixes: ['/history'],
		tools: [],
		source: 'core'
	},
	{
		id: 'models',
		name: 'Models',
		description: '',
		default_route_prefixes: ['/models'],
		tools: [],
		source: 'core'
	}
];

function mount(props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: ChatModeSelector as never, target, props });
	return { target, component };
}

describe('ChatModeSelector scope-mismatch notice', () => {
	it('tells the user the locked conversation stays in its own scope and offers a new chat', () => {
		const onNewChat = vi.fn();
		const { target } = mount({
			modes,
			selected: 'history',
			locked: true,
			onSelect: vi.fn(),
			pageModeId: 'models',
			onNewChat
		});

		const notice = target.querySelector('[data-testid="chat-mode-scope-notice"]');
		expect(notice).not.toBeNull();
		expect(notice?.textContent).toContain('Started in History');
		expect(notice?.textContent).toContain('to use Models');

		const newChatButton = target.querySelector<HTMLButtonElement>(
			'[data-testid="chat-mode-scope-new-chat"]'
		);
		expect(newChatButton).not.toBeNull();
		newChatButton?.click();
		expect(onNewChat).toHaveBeenCalledOnce();
	});

	it('renders nothing once the conversation and page scopes agree', () => {
		const { target } = mount({
			modes,
			selected: 'history',
			locked: true,
			onSelect: vi.fn(),
			pageModeId: 'history',
			onNewChat: vi.fn()
		});

		expect(target.querySelector('[data-testid="chat-mode-scope-notice"]')).toBeNull();
	});

	it('renders nothing while the conversation is still unlocked, even if scopes differ', () => {
		const { target } = mount({
			modes,
			selected: 'history',
			locked: false,
			onSelect: vi.fn(),
			pageModeId: 'models',
			onNewChat: vi.fn()
		});

		expect(target.querySelector('[data-testid="chat-mode-scope-notice"]')).toBeNull();
	});
});
