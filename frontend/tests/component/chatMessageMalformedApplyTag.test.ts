// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';

const { default: ChatMessage } = await import('../../src/lib/components/ChatMessage.svelte');
const { createClassComponent } = await import('svelte/legacy');

function mount(props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	createClassComponent({ component: ChatMessage as never, target, props });
	return target;
}

afterEach(() => {
	document.body.innerHTML = '';
});

const reported = 'Here is a version.\n\n<tool_action update_director_segment segment_id="seg-1">a dog running on a beach';

function applyButton(target: HTMLElement) {
	return target.querySelector('[data-testid="segment-action-apply"]') as HTMLButtonElement | null;
}

describe('ChatMessage with a malformed Apply tag', () => {
	it('renders an Apply button for the reported tag and reports the director action', () => {
		const onApplyAction = vi.fn();
		const target = mount({ role: 'assistant', content: reported, messageId: 'm1', onApplyAction });

		expect(target.querySelector('.prompt-card-title strong')?.textContent).toBe('Director Segment');
		expect(target.textContent).not.toContain('<tool_action');
		applyButton(target)!.click();
		expect(onApplyAction).toHaveBeenCalledWith(
			expect.objectContaining({ type: 'update_director_segment', segmentId: 'seg-1', content: 'a dog running on a beach' }),
			0
		);
	});

	it('shows no Apply for the trailing tag while it is still streaming', () => {
		const target = mount({ role: 'assistant', content: reported, messageId: 'm1', isStreaming: true, onApplyAction: () => {} });

		expect(applyButton(target)).toBeNull();
	});
});
