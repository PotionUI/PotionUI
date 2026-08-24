/**
 * Pure utility functions shared across AI chat panel components.
 */

import type { ParsedContent } from '$lib/types/chat';

/**
 * Format a token count with a 'k' suffix for values >= 1000.
 */
export function formatTokenCount(n: number): string {
	if (n >= 1000) {
		return (n / 1000).toFixed(1) + 'k';
	}
	return n.toString();
}

/**
 * Strip common markdown formatting from text, returning plain text.
 */
function stripMarkdown(text: string): string {
	return text
		// Bold: **text** or __text__
		.replace(/\*\*(.*?)\*\*/g, '$1')
		.replace(/__(.*?)__/g, '$1')
		// Italic: *text* or _text_
		.replace(/\*(.*?)\*/g, '$1')
		.replace(/_(.*?)_/g, '$1')
		// Strikethrough: ~~text~~
		.replace(/~~(.*?)~~/g, '$1')
		// Inline code: `text`
		.replace(/`(.*?)`/g, '$1')
		// Headers: # text
		.replace(/^#{1,6}\s+/gm, '')
		// Remove extra whitespace
		.replace(/\s+/g, ' ')
		.trim();
}

/**
 * Parse an assistant response to extract structured content from <prompt> and
 * <improvements> XML-style tags. Strips markdown from the prompt content.
 */
export function parseAssistantResponse(content: string): ParsedContent {
	const promptMatch = content.match(/<prompt>([\s\S]*?)<\/prompt>/i);
	const improvementsMatch = content.match(/<improvements>([\s\S]*?)<\/improvements>/i);

	const rawPrompt = promptMatch?.[1]?.trim();
	const cleanPrompt = rawPrompt ? stripMarkdown(rawPrompt) : undefined;

	return {
		prompt: cleanPrompt,
		improvements: improvementsMatch?.[1]?.trim(),
		raw: content
	};
}

export interface SessionDateGroup<T> {
	label: 'Today' | 'Yesterday' | 'This week' | 'Older';
	sessions: T[];
}

/**
 * Bucket chat sessions for the history view by their last activity.
 * Preserves the input order inside each bucket; empty buckets are omitted.
 */
export function groupSessionsByDate<T extends { updated_at?: string; created_at?: string }>(
	sessions: T[],
	now: Date = new Date()
): SessionDateGroup<T>[] {
	const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
	const today = startOfDay(now);

	const buckets: Record<SessionDateGroup<T>['label'], T[]> = {
		Today: [],
		Yesterday: [],
		'This week': [],
		Older: []
	};

	for (const session of sessions) {
		const raw = session.updated_at || session.created_at;
		const date = raw ? new Date(raw) : null;
		if (!date || Number.isNaN(date.getTime())) {
			buckets.Older.push(session);
			continue;
		}
		const dayDiff = Math.round((today - startOfDay(date)) / 86_400_000);
		if (dayDiff <= 0) buckets.Today.push(session);
		else if (dayDiff === 1) buckets.Yesterday.push(session);
		else if (dayDiff < 7) buckets['This week'].push(session);
		else buckets.Older.push(session);
	}

	return (Object.keys(buckets) as SessionDateGroup<T>['label'][])
		.filter((label) => buckets[label].length > 0)
		.map((label) => ({ label, sessions: buckets[label] }));
}
