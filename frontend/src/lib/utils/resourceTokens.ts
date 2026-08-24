/**
 * Single source of truth for @resource markers in chat message text.
 *
 * Two serialized forms (mirroring the #chip markers in chipParser.ts):
 *   @simple.uri-path      — word chars, dots, dashes
 *   @[uri with spaces]    — bracketed for anything else
 *
 * A marker only counts when the @ is at the start of the text or preceded by
 * a non-word character, so emails (user@host.com) and mid-word @ don't match.
 */

export interface ResourceTokenPart {
	type: 'text' | 'resource';
	/** Text content for 'text' parts; the decoded resource uri for 'resource' parts. */
	value: string;
}

const SIMPLE_URI = /^[\w][\w.-]*$/;

/** Fresh instance per call — the global flag makes shared regexes stateful. */
function tokenRegex(): RegExp {
	return /(?<![\w@])@(?:\[([^\]]+)\]|([\w][\w.-]*))/g;
}

/** Encode a resource uri as an inline text marker. */
export function encodeResourceToken(uri: string): string {
	return SIMPLE_URI.test(uri) ? `@${uri}` : `@[${uri}]`;
}

/** All resource uris referenced in the text, in order of appearance (with duplicates). */
export function parseResourceTokens(text: string): string[] {
	const uris: string[] = [];
	if (!text) return uris;
	const regex = tokenRegex();
	let match: RegExpExecArray | null;
	while ((match = regex.exec(text)) !== null) {
		uris.push(match[1] || match[2]);
	}
	return uris;
}

/** Split text into plain-text and resource parts for rendering. */
export function splitResourceTokens(text: string): ResourceTokenPart[] {
	const parts: ResourceTokenPart[] = [];
	if (!text) return parts;
	const regex = tokenRegex();
	let lastIndex = 0;
	let match: RegExpExecArray | null;
	while ((match = regex.exec(text)) !== null) {
		if (match.index > lastIndex) {
			parts.push({ type: 'text', value: text.slice(lastIndex, match.index) });
		}
		parts.push({ type: 'resource', value: match[1] || match[2] });
		lastIndex = match.index + match[0].length;
	}
	if (lastIndex < text.length) {
		parts.push({ type: 'text', value: text.slice(lastIndex) });
	}
	return parts;
}
