import { describe, expect, it } from 'vitest';
import { composeQuestionAnswer, deriveQuestionQueue } from './questionQueue';
import type { UnifiedChatMessageData } from '$lib/types/chat';

function message(overrides: Partial<UnifiedChatMessageData> = {}): UnifiedChatMessageData {
	return {
		role: 'assistant',
		content: '',
		timestamp: 1000,
		...overrides
	};
}

describe('deriveQuestionQueue', () => {
	it('returns an empty queue when there are no messages, or the latest reply has none', () => {
		expect(deriveQuestionQueue([], new Set())).toEqual([]);
		expect(
			deriveQuestionQueue(
				[message({ id: 'm1', parsed_content: { reply_contract: { improved: [], questions: [] } } })],
				new Set()
			)
		).toEqual([]);
		expect(deriveQuestionQueue([message({ id: 'm1' })], new Set())).toEqual([]);
	});

	it('derives entries from the latest assistant message only', () => {
		const messages = [
			message({
				id: 'm1',
				timestamp: 1000,
				parsed_content: {
					reply_contract: { improved: [], questions: [{ text: 'old question?', options: [] }] }
				}
			}),
			message({ role: 'user', content: 'thanks' }),
			message({
				id: 'm2',
				timestamp: 2000,
				parsed_content: {
					reply_contract: {
						improved: [],
						questions: [
							{ text: 'Keep the rain ambience or push golden hour?', options: ['Rain', 'Golden hour'] },
							{ text: 'Anything else?', options: [] }
						]
					}
				}
			})
		];

		const queue = deriveQuestionQueue(messages, new Set());
		expect(queue.map((e) => e.text)).toEqual([
			'Keep the rain ambience or push golden hour?',
			'Anything else?'
		]);
		expect(queue.every((e) => e.messageId === 'm2')).toBe(true);
		expect(queue[0]).toMatchObject({
			messageId: 'm2',
			messageTimestamp: 2000,
			index: 0,
			options: ['Rain', 'Golden hour'],
			total: 2
		});
	});

	it('filters out dismissed entries by (messageId, index)', () => {
		const messages = [
			message({
				id: 'm1',
				parsed_content: {
					reply_contract: {
						improved: [],
						questions: [
							{ text: 'first?', options: [] },
							{ text: 'second?', options: [] }
						]
					}
				}
			})
		];

		const queue = deriveQuestionQueue(messages, new Set(['m1:0']));
		expect(queue.map((e) => e.text)).toEqual(['second?']);
	});

	it('expires an older reply\'s questions once a newer assistant message arrives, even if not dismissed', () => {
		const messages = [
			message({
				id: 'm1',
				parsed_content: {
					reply_contract: { improved: [], questions: [{ text: 'old?', options: [] }] }
				}
			}),
			message({
				id: 'm2',
				parsed_content: { reply_contract: { improved: [], questions: [] } }
			})
		];

		// Nothing dismissed — m1's question simply isn't consulted anymore.
		expect(deriveQuestionQueue(messages, new Set())).toEqual([]);
	});

	it('shows nothing for a still-streaming latest assistant message (no persisted id yet)', () => {
		const messages = [
			message({
				id: 'm1',
				parsed_content: {
					reply_contract: { improved: [], questions: [{ text: 'answered already?', options: [] }] }
				}
			}),
			message({ isStreaming: true, content: '' })
		];

		expect(deriveQuestionQueue(messages, new Set())).toEqual([]);
	});
});

describe('composeQuestionAnswer', () => {
	it('quotes the question, then the answer, then an explicit continuation cue', () => {
		expect(composeQuestionAnswer('Keep the rain ambience or push golden hour?', 'Golden hour')).toBe(
			'> Keep the rain ambience or push golden hour?\n\nGolden hour\n\nContinue.'
		);
	});
});
