import { writable } from 'svelte/store';
import type { UnifiedChatMessageData } from '$lib/types/chat';

/**
 * One pending question from the latest assistant reply's
 * `parsed_content.reply_contract.questions`, anchored back to the owning
 * message and its index within that reply.
 */
export interface QuestionQueueEntry {
	messageId: string;
	messageTimestamp: number | undefined;
	index: number;
	text: string;
	options: string[];
	/** Total question count on the owning reply, for a "N of total" header. */
	total: number;
}

const dismissedKey = (messageId: string, index: number) => `${messageId}:${index}`;

/**
 * Questions the user answered or skipped in the dock, in-memory only (gone
 * on reload, same as `appliedSegmentActions`). Keyed by (messageId, index)
 * rather than cleared per-message: `deriveQuestionQueue` only ever reads the
 * latest assistant message, so an older message's dismissals just stop being
 * consulted once a newer reply arrives — no explicit expiry needed.
 */
function createDismissedQuestionsStore() {
	const { subscribe, update } = writable<Set<string>>(new Set());
	return {
		subscribe,
		dismiss(messageId: string, index: number) {
			update((current) => {
				const next = new Set(current);
				next.add(dismissedKey(messageId, index));
				return next;
			});
		}
	};
}

export const dismissedQuestions = createDismissedQuestionsStore();

/**
 * Composes a docked question's answer as a user turn: the quoted question so
 * the backend's reply-contract "Resuming" rule recognizes it as an answer,
 * plus an explicit continuation cue so the model resumes the task instead of
 * just acknowledging the answer and stopping.
 */
export function composeQuestionAnswer(question: string, answer: string): string {
	return `> ${question}\n\n${answer}\n\nContinue.`;
}

/**
 * Pending questions from the LATEST assistant message only — scans back from
 * the end of `messages` for the most recent assistant turn and reads its
 * `reply_contract.questions`. A newer assistant reply automatically expires
 * an older reply's questions simply by no longer being the one consulted;
 * they never appear in the returned queue again regardless of `dismissed`.
 */
export function deriveQuestionQueue(
	messages: UnifiedChatMessageData[],
	dismissed: Set<string>
): QuestionQueueEntry[] {
	for (let i = messages.length - 1; i >= 0; i--) {
		const message = messages[i];
		if (message.role !== 'assistant') continue;
		// No persisted id yet (still streaming) — the backend only fills
		// parsed_content on the `done` event, so there's nothing to show.
		if (!message.id) return [];
		const questions = message.parsed_content?.reply_contract?.questions;
		if (!questions?.length) return [];
		return questions
			.map((q, index) => ({
				messageId: message.id!,
				messageTimestamp: message.timestamp,
				index,
				text: q.text,
				options: q.options || [],
				total: questions.length
			}))
			.filter((entry) => !dismissed.has(dismissedKey(entry.messageId, entry.index)));
	}
	return [];
}
