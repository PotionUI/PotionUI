// @vitest-environment jsdom
//
// The output-limit note (`metadata.behavior_trace.completion.reason ===
// 'length'`) is distinct from the transport-level `isPartial` note: it must
// render only for a genuine model completion outcome, on both a live
// (freshly streamed) and a reloaded (persisted) message, and must never
// render for an older message that predates the field or for any other
// completion reason. Mounts the real component rather than asserting
// against the reactive expression in isolation.
import { describe, it, expect, afterEach } from 'vitest';

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

describe('output-limit note', () => {
	it('renders when the persisted completion reason is "length"', () => {
		const target = mount({
			role: 'assistant',
			content: 'this got cut off mid',
			metadata: { behavior_trace: { completion: { reason: 'length', raw: 'length' } } }
		});

		const note = target.querySelector('[data-testid="output-limit-note"]');
		expect(note?.textContent).toContain('Stopped at the output limit');
	});

	it('does not render for a normal "stop" completion', () => {
		const target = mount({
			role: 'assistant',
			content: 'a complete answer',
			metadata: { behavior_trace: { completion: { reason: 'stop', raw: 'stop' } } }
		});

		expect(target.querySelector('[data-testid="output-limit-note"]')).toBeNull();
	});

	it('does not render when completion is absent (older persisted message)', () => {
		const target = mount({
			role: 'assistant',
			content: 'an older reply with no completion field at all',
			metadata: { behavior_trace: { thinking_mode: null } }
		});

		expect(target.querySelector('[data-testid="output-limit-note"]')).toBeNull();
	});

	it('does not render when metadata itself is absent (mid-stream message)', () => {
		const target = mount({
			role: 'assistant',
			content: 'streaming in...',
			isStreaming: true
		});

		expect(target.querySelector('[data-testid="output-limit-note"]')).toBeNull();
	});

	it('is independent of the transport-level partial note', () => {
		const target = mount({
			role: 'assistant',
			content: 'reconnected mid-reply',
			isPartial: true,
			metadata: { behavior_trace: { completion: { reason: 'length', raw: 'length' } } }
		});

		expect(target.querySelector('[data-testid="partial-reply-note"]')).not.toBeNull();
		expect(target.querySelector('[data-testid="output-limit-note"]')).not.toBeNull();
	});
});
