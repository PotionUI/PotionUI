import { describe, it, expect, vi } from 'vitest';

const { default: ChatHeader } = await import('$lib/components/chat/ChatHeader.svelte');
const { createClassComponent } = await import('svelte/legacy');

function mount(props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: ChatHeader as never, target, props });
	return { target, component };
}

describe('ChatHeader new chat button', () => {
	it('calls onNewChat when clicked', () => {
		const onNewChat = vi.fn();
		const { target } = mount({
			llmConfigs: [],
			selectedConfigId: '',
			onSelectConfig: vi.fn(),
			onSelectMode: vi.fn(),
			onNewChat,
			railCollapsed: false,
			onToggleRail: vi.fn(),
			onRename: vi.fn(),
			onExportTranscript: vi.fn(),
			onDeleteConversation: vi.fn()
		});

		const button = target.querySelector<HTMLButtonElement>('[aria-label="New chat"]');
		expect(button).not.toBeNull();
		button?.click();
		expect(onNewChat).toHaveBeenCalledOnce();
	});

	it('renders no new chat button when onNewChat is not provided', () => {
		const { target } = mount({
			llmConfigs: [],
			selectedConfigId: '',
			onSelectConfig: vi.fn(),
			onSelectMode: vi.fn(),
			railCollapsed: false,
			onToggleRail: vi.fn(),
			onRename: vi.fn(),
			onExportTranscript: vi.fn(),
			onDeleteConversation: vi.fn()
		});

		expect(target.querySelector('[aria-label="New chat"]')).toBeNull();
	});
});
