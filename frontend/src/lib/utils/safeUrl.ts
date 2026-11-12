const NAMED_ENTITIES: Record<string, string> = {
	amp: '&',
	lt: '<',
	gt: '>',
	quot: '"',
	apos: "'",
	nbsp: '\u00a0',
	colon: ':',
	tab: '\t',
	newline: '\n',
	sol: '/',
	bsol: '\\',
	lpar: '(',
	rpar: ')',
	period: '.',
	comma: ',',
	semi: ';',
	equals: '=',
	quest: '?',
	num: '#',
	percnt: '%',
	excl: '!',
	lbrack: '[',
	rbrack: ']',
	lsqb: '[',
	rsqb: ']',
	lcub: '{',
	rcub: '}',
	lbrace: '{',
	rbrace: '}',
	grave: '`',
	plus: '+',
	dollar: '$',
	commat: '@',
	ast: '*',
	lowbar: '_',
	hyphen: '-',
	dash: '-',
	verbar: '|',
	vert: '|'
};

const ENTITY_RE = /&(?:#(\d+)|#[xX]([0-9a-fA-F]+)|([a-zA-Z][a-zA-Z0-9]*));?/g;
const MAX_DECODE_PASSES = 16;
const STRIPPED_CHARS_RE =
	/[\u0000-\u0020\u007f-\u009f\u00ad\u061c\u115f\u1160\u180e\u200b-\u200f\u2028-\u202f\u2060-\u206f\u3000\u3164\ufeff\uffa0]/g;
const SAFE_LINK_SCHEMES = new Set(['http:', 'https:', 'mailto:']);
const SAFE_IMAGE_SCHEMES = new Set(['http:', 'https:']);

function codePointToString(code: number): string {
	if (!Number.isFinite(code) || code < 0 || code > 0x10ffff || (code >= 0xd800 && code <= 0xdfff)) {
		return '\ufffd';
	}
	return String.fromCodePoint(code);
}

function decodeEntitiesOnce(input: string): string {
	return input.replace(ENTITY_RE, (match, dec: string, hex: string, name: string) => {
		if (dec !== undefined) return codePointToString(parseInt(dec, 10));
		if (hex !== undefined) return codePointToString(parseInt(hex, 16));
		const decoded = NAMED_ENTITIES[name.toLowerCase()];
		return decoded === undefined ? match : decoded;
	});
}

function decodeUntilStable(input: string): string | null {
	let current = input;
	for (let i = 0; i < MAX_DECODE_PASSES; i++) {
		const next = decodeEntitiesOnce(current);
		if (next === current) return current;
		current = next;
	}
	return null;
}

export function escapeAttribute(value: string): string {
	return value
		.replace(/&/g, '&amp;')
		.replace(/</g, '&lt;')
		.replace(/>/g, '&gt;')
		.replace(/"/g, '&quot;')
		.replace(/'/g, '&#39;');
}

function isRelative(normalized: string): boolean {
	if (normalized.startsWith('//') || normalized.startsWith('/\\') || normalized.startsWith('\\')) return false;
	return (
		normalized.startsWith('/') ||
		normalized.startsWith('#') ||
		normalized.startsWith('?') ||
		normalized.startsWith('./') ||
		normalized.startsWith('../')
	);
}

function sanitize(raw: string, schemes: Set<string>, allowMailto: boolean): string | null {
	if (!raw) return null;
	const decoded = decodeUntilStable(raw);
	if (decoded === null) return null;

	const normalized = decoded.replace(STRIPPED_CHARS_RE, '').toLowerCase();
	if (!normalized) return null;

	if (!isRelative(normalized)) {
		const scheme = /^([a-z][a-z0-9+.-]*:)/.exec(normalized)?.[1];
		if (!scheme || !schemes.has(scheme)) return null;
		if (scheme === 'mailto:' && !allowMailto) return null;
		try {
			new URL(normalized);
		} catch {
			return null;
		}
	}

	const visible = decoded.replace(
		/[\u0000-\u001f\u007f-\u009f\u00ad\u061c\u180e\u200b-\u200f\u2028-\u202e\u2060-\u206f\ufeff]/g,
		''
	);
	return escapeAttribute(visible.trim().replace(/\s/g, '%20'));
}

export function sanitizeLinkUrl(raw: string): string | null {
	return sanitize(raw, SAFE_LINK_SCHEMES, true);
}

export function sanitizeImageUrl(raw: string): string | null {
	return sanitize(raw, SAFE_IMAGE_SCHEMES, false);
}
