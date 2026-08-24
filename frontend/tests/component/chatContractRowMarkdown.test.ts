// @vitest-environment jsdom
//
// The reply-contract IMPROVED list and the docked QUESTIONS text are built
// from plain strings the backend never re-escapes (see reply_contract.py) —
// a model emitting `**Structure**: …` must render bold, not literal
// asterisks. Mounts the real components rather than asserting against
// `processMarkdown`'s own unit tests, which only prove the helper works, not
// that these templates call it.
import { describe, it, expect, afterEach } from 'vitest';

const { default: ChatMessage } = await import('../../src/lib/components/ChatMessage.svelte');
const { default: ApprovalDock } = await import('../../src/lib/components/chat/ApprovalDock.svelte');
const { createClassComponent } = await import('svelte/legacy');

function mount(component: unknown, props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	createClassComponent({ component: component as never, target, props });
	return target;
}

afterEach(() => {
	document.body.innerHTML = '';
});

describe('IMPROVED rows render inline markdown', () => {
	it('renders bold and inline code instead of literal ** and `', () => {
		const target = mount(ChatMessage, {
			role: 'assistant',
			content: 'Done.',
			parsedContent: {
				reply_contract: {
					improved: [
						'**Structure**: Applied the mandatory three-field `MiniMax` format.',
						'plain row with no markdown'
					]
				}
			}
		});

		expect(target.textContent).toContain('Structure');
		expect(target.textContent).not.toContain('**Structure**');
		expect(target.textContent).not.toContain('`MiniMax`');
		const strong = target.querySelector('strong');
		expect(strong?.textContent).toBe('Structure');
		expect(strong?.className).toContain('font-semibold');
		expect(target.querySelector('code')?.textContent).toBe('MiniMax');
		expect(target.textContent).toContain('plain row with no markdown');
	});
});

describe('docked question text renders inline markdown', () => {
	it('renders bold instead of literal ** in the current question', () => {
		const target = mount(ApprovalDock, {
			messages: [
				{
					id: 'msg-1',
					role: 'assistant',
					content: 'Done.',
					timestamp: Date.now(),
					parsed_content: {
						reply_contract: {
							questions: [{ text: 'keep the **rain** ambience?', options: ['rain', 'golden hour'] }]
						}
					}
				}
			]
		});

		expect(target.textContent).not.toContain('**rain**');
		const strong = target.querySelector('strong');
		expect(strong?.textContent).toBe('rain');
	});
});
