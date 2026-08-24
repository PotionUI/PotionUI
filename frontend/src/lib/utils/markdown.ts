export interface ToolAction {
	type: string;
	segmentIndex: number;
	segmentId: string;
	content: string;
}

export interface MarkdownOptions {
	/**
	 * How to render a single source newline outside block elements. Interactive
	 * content keeps the historical hard-break behavior by default, while long
	 * documents can treat source wrapping as normal flowing prose.
	 */
	softLineBreaks?: 'break' | 'space';
	/**
	 * Map of known prompt-variable name → tooltip text. When present, `${name}`
	 * occurrences for a KNOWN name render as small read-only variable chips (see
	 * decorateVariableChips); unknown names stay literal text. Omit to disable.
	 */
	variableChips?: Record<string, string>;
}

type ColumnAlign = 'left' | 'right' | 'center' | null;

// Splits a GFM table row into cells, honoring escaped pipes (`\|`) and
// optional leading/trailing pipes.
function splitTableRow(line: string): string[] {
	let l = line.trim().replace(/\\\|/g, '\x01PIPE\x01');
	if (l.startsWith('|')) l = l.slice(1);
	if (l.endsWith('|')) l = l.slice(0, -1);
	return l.split('|').map((cell) => cell.replace(/\x01PIPE\x01/g, '|').trim());
}

// A separator row is made up entirely of cells like `---`, `:--`, `--:`, `:-:`.
function isTableSeparatorRow(line: string): boolean {
	const trimmed = line.trim();
	if (!trimmed || !trimmed.includes('-')) return false;
	const cells = splitTableRow(trimmed);
	return cells.length > 0 && cells.every((c) => /^:?-+:?$/.test(c));
}

function columnAlign(sepCell: string): ColumnAlign {
	const left = sepCell.startsWith(':');
	const right = sepCell.endsWith(':');
	if (left && right) return 'center';
	if (right) return 'right';
	if (left) return 'left';
	return null;
}

function alignClass(align: ColumnAlign): string {
	if (align === 'right') return ' text-right';
	if (align === 'center') return ' text-center';
	return '';
}

function renderTable(header: string[], aligns: ColumnAlign[], rows: string[][]): string {
	const thCells = header
		.map(
			(cell, i) =>
				`<th class="text-left font-semibold px-3 py-1.5 border-b border-line-strong${alignClass(aligns[i])}">${cell}</th>`
		)
		.join('');
	const bodyRows = rows
		.map((row) => {
			const tds = header
				.map(
					(_, i) =>
						`<td class="px-3 py-1.5 border-b border-line align-top${alignClass(aligns[i])}">${row[i] ?? ''}</td>`
				)
				.join('');
			return `<tr>${tds}</tr>`;
		})
		.join('');
	return `<div class="overflow-x-auto"><table class="w-full text-sm my-3 border-collapse"><thead><tr>${thCells}</tr></thead><tbody>${bodyRows}</tbody></table></div>`;
}

// Groups consecutive `- ` / `N. ` lines into real <ul>/<ol> elements and
// consecutive GFM table lines (header + separator + body) into <table>
// elements. Must run after inline transforms (bold/code/links) have already
// been applied to the text, and before the \n → <br> conversion, so that
// list/table content already contains the right inline HTML and none of the
// internal newlines survive to be turned into stray <br>s.
function processBlockElements(text: string): string {
	const lines = text.split('\n');
	const output: string[] = [];
	let i = 0;

	while (i < lines.length) {
		const line = lines[i];

		// GFM table: a row containing "|" followed immediately by a separator row.
		if (line.trim().includes('|') && i + 1 < lines.length && isTableSeparatorRow(lines[i + 1])) {
			const headerCells = splitTableRow(line);
			const aligns = splitTableRow(lines[i + 1]).map(columnAlign);
			let j = i + 2;
			const rows: string[][] = [];
			while (j < lines.length && lines[j].trim().includes('|')) {
				rows.push(splitTableRow(lines[j]));
				j++;
			}
			output.push(renderTable(headerCells, aligns, rows));
			i = j;
			continue;
		}

		// Unordered list
		if (/^- (.*)$/.test(line)) {
			const items: string[] = [];
			while (i < lines.length) {
				const m = lines[i].match(/^- (.*)$/);
				if (!m) break;
				items.push(m[1]);
				i++;
			}
			output.push(
				`<ul class="list-disc pl-5 my-2 space-y-0.5">${items.map((it) => `<li>${it}</li>`).join('')}</ul>`
			);
			continue;
		}

		// Ordered list
		const olStart = line.match(/^(\d+)\. (.*)$/);
		if (olStart) {
			const items: string[] = [];
			const firstNum = parseInt(olStart[1], 10);
			while (i < lines.length) {
				const m = lines[i].match(/^\d+\. (.*)$/);
				if (!m) break;
				items.push(m[1]);
				i++;
			}
			const startAttr = firstNum !== 1 ? ` start="${firstNum}"` : '';
			output.push(
				`<ol class="list-decimal pl-5 my-2 space-y-0.5"${startAttr}>${items.map((it) => `<li>${it}</li>`).join('')}</ol>`
			);
			continue;
		}

		output.push(line);
		i++;
	}

	return output.join('\n');
}

/** Schemes a link may use. Anything else — `javascript:`, `data:`, `vbscript:` — is dropped. */
const SAFE_URL_SCHEMES = ['http:', 'https:', 'mailto:'];

/**
 * Make a markdown link target safe to place inside an href attribute.
 *
 * Returns null when the URL must not be linked at all. Two distinct dangers:
 *  - a scheme that executes (`javascript:alert(1)`),
 *  - a quote that closes the attribute and opens another (`" onmouseover="alert(1)`),
 *    which the HTML-entity pass does not escape because it only handles `< > &`.
 */
function sanitizeUrl(raw: string): string | null {
	const url = raw.trim();
	if (!url) return null;

	// Relative, root-relative and anchor links never carry a scheme.
	const isRelative = url.startsWith('/') || url.startsWith('#') || url.startsWith('./');
	if (!isRelative) {
		const scheme = url.slice(0, url.indexOf(':') + 1).toLowerCase();
		// A colon before the first slash means a scheme was supplied; require a safe one.
		const hasScheme = scheme.length > 1 && !url.slice(0, url.indexOf(':')).includes('/');
		if (hasScheme && !SAFE_URL_SCHEMES.includes(scheme)) return null;
	}

	// `& < >` are already entity-escaped by the time links are processed; re-escaping
	// would double-encode them. Quotes and whitespace are what remain dangerous.
	return url
		.replace(/"/g, '&quot;')
		.replace(/'/g, '&#39;')
		.replace(/\s/g, '%20');
}

/** Escape a string for safe interpolation into a double-quoted HTML attribute. */
function escapeHtmlAttr(value: string): string {
	return value
		.replace(/&/g, '&amp;')
		.replace(/</g, '&lt;')
		.replace(/>/g, '&gt;')
		.replace(/"/g, '&quot;')
		.replace(/'/g, '&#39;');
}


// Matches, in priority order: (1) a whole inline `<code>…</code>` element,
// (2) any single HTML tag, or (3) a `${name}` variable reference. Named groups
// 1 and 2 are structural spans we must NOT decorate (code content stays
// literal; tag attributes like href must not be corrupted); only group 3 —
// `${name}` sitting in visible text — is a decoration candidate.
const VARIABLE_DECORATION_RE = /(<code\b[^>]*>[\s\S]*?<\/code>)|(<[^>]+>)|\$\{([A-Za-z0-9_.-]+)\}/g;

/**
 * Replace `${name}` in already-rendered message HTML with a small read-only
 * variable chip, but only for names present in `tooltips` (known variables);
 * unknown names are left as literal `${name}` text. Pure and exported for
 * direct testing.
 *
 * Safety: it runs on HTML whose surrounding literal text is already
 * entity-escaped, and it never decorates inside `<code>…</code>` or inside an
 * HTML tag, so code fences/spans stay literal and attributes stay intact. The
 * `name` charset (`[A-Za-z0-9_.-]`) contains no HTML metacharacters, and the
 * untrusted `tooltip` text is attribute-escaped — so no user text is injected
 * as raw markup.
 */
export function decorateVariableChips(html: string, tooltips: Record<string, string>): string {
	if (!html || !tooltips || Object.keys(tooltips).length === 0) return html;
	return html.replace(VARIABLE_DECORATION_RE, (match, codeEl, tag, name) => {
		if (codeEl !== undefined || tag !== undefined) return match; // structural — leave untouched
		if (!(name in tooltips)) return match; // unknown variable — literal text
		const title = escapeHtmlAttr(tooltips[name]);
		return (
			`<span class="inline-flex items-center align-middle mx-0.5 rounded border border-accent/30 bg-accent/10 px-1 leading-none" title="${title}">` +
			`<span class="font-mono text-xs text-fg-subtle">$</span>` +
			`<span class="font-mono text-xs text-fg">${name}</span>` +
			`</span>`
		);
	});
}

export function processMarkdown(text: string, options: MarkdownOptions = {}): string {
	if (!text) return '';

	// Step 1: Extract fenced code blocks into placeholders before any other transforms
	const codeBlocks: string[] = [];
	let processed = text.replace(/```[\s\S]*?```/g, (match) => {
		// Strip the opening/closing fences and optional language tag
		const inner = match
			.replace(/^```[^\n]*\n?/, '')
			.replace(/\n?```$/, '');
		const placeholder = `\x00CODEBLOCK${codeBlocks.length}\x00`;
		codeBlocks.push(inner);
		return placeholder;
	});

	// Step 2: Escape HTML entities (but NOT inside code block placeholders)
	// We escape & first to avoid double-escaping, then < and >
	processed = processed
		.replace(/&(?!amp;|lt;|gt;|quot;|#\d+;|#x[\da-fA-F]+;)/g, '&amp;')
		.replace(/</g, '&lt;')
		.replace(/>/g, '&gt;');

	// Step 3: Normalize excessive blank lines (3+ newlines → 2)
	processed = processed.replace(/\n{3,}/g, '\n\n');

	// Step 4: Apply all markdown transforms
	processed = processed
		// Headers
		.replace(/^### (.*$)/gim, '<h3 class="text-lg font-semibold mb-1 mt-3">$1</h3>')
		.replace(/^## (.*$)/gim, '<h2 class="text-xl font-semibold mb-1.5 mt-3">$1</h2>')
		.replace(/^# (.*$)/gim, '<h1 class="text-2xl font-bold mb-2 mt-4">$1</h1>')
		// Bold
		.replace(/\*\*(.*?)\*\*/g, '<strong class="font-semibold">$1</strong>')
		// Italic
		.replace(/\*(.*?)\*/g, '<em class="italic">$1</em>')
		// Inline code
		.replace(
			/`(.*?)`/g,
			'<code class="bg-surface-2 px-1 py-0.5 rounded text-sm font-mono text-fg-muted">$1</code>'
		)
		// Links. The href comes from untrusted text, so it is sanitized rather than
		// interpolated: step 2 escapes < > &, but NOT the double quote that would
		// otherwise close the attribute and let `[x](" onmouseover="…)` inject one.
		.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_match, label: string, href: string) => {
			const safe = sanitizeUrl(href);
			if (!safe) return label;
			return `<a href="${safe}" class="text-signal hover:underline" target="_blank" rel="noopener noreferrer">${label}</a>`;
		});

	// Lists and GFM tables — must run before line break conversion
	processed = processBlockElements(processed);

	// Blockquotes
	processed = processed.replace(
		/^&gt; (.*$)/gim,
		'<blockquote class="border-l-4 border-line-strong pl-4 italic text-fg-muted my-1">$1</blockquote>'
	);

	// Paragraph breaks get a compact gap. Single newlines historically render as
	// hard breaks, but documentation can opt into normal Markdown-style flowing
	// prose so source-code wrapping does not dictate the visible column width.
	const softLineBreak = options.softLineBreaks === 'space' ? ' ' : '<br>';
	processed = processed
		.replace(/\n\n/g, '<span class="block mt-2"></span>')
		.replace(/\n/g, softLineBreak);

	// Clean up breaks adjacent to block elements that already have margins
	processed = processed
		.replace(/(<\/h[1-3]>)(<br>|<span class="block mt-2"><\/span>)+/gi, '$1')
		.replace(/(<br>|<span class="block mt-2"><\/span>)+(<h[1-3]\s)/gi, '$2')
		.replace(/(<\/li>)(<br>)/gi, '$1')
		.replace(/(<\/blockquote>)(<br>|<span class="block mt-2"><\/span>)+/gi, '$1')
		.replace(/(<\/(?:ul|ol)>)(<br>|<span class="block mt-2"><\/span>)+/gi, '$1')
		.replace(/(<br>|<span class="block mt-2"><\/span>)+(<(?:ul|ol)[\s>])/gi, '$2')
		.replace(/(<\/div>)(<br>|<span class="block mt-2"><\/span>)+/gi, '$1')
		.replace(/(<br>|<span class="block mt-2"><\/span>)+(<div class="overflow-x-auto")/gi, '$2');

	// Decorate `${name}` variable references as read-only chips. Runs after all
	// inline/block HTML is built (so injected chip markup is never reprocessed)
	// and before fenced code is restored (so fenced content — still a
	// placeholder here — stays literal). Skips inline `<code>` and HTML tags.
	if (options.variableChips) {
		processed = decorateVariableChips(processed, options.variableChips);
	}

	// Step 5: Restore fenced code blocks as styled <pre><code> elements
	processed = processed.replace(/\x00CODEBLOCK(\d+)\x00/g, (_, index) => {
		const code = codeBlocks[parseInt(index, 10)]
			.replace(/&/g, '&amp;')
			.replace(/</g, '&lt;')
			.replace(/>/g, '&gt;');
		return `<pre><code class="bg-surface-2 block p-3 rounded-lg text-sm font-mono text-fg-muted my-2 overflow-x-auto">${code}</code></pre>`;
	});

	return processed;
}

export interface ToolCallSpan {
	status: 'complete' | 'unclosed';
	toolName: string | null;
}

// Matches a complete `<tool_call>{...}</tool_call>` block. Some LLMs (see
// src/features/llm/tools/executor.py's _TOOL_CALL_XML_RE) emit tool calls this
// way instead of using the provider's structured tool-call field; the backend
// strips these at source, but older persisted transcripts, older backends, or
// a mid-stream partial tag can still land in message content.
const TOOL_CALL_RE = /<tool_call>\s*([\s\S]*?)\s*<\/tool_call>/g;

// Payload JSON may contain mangled-quote artifacts (`<|"|>`, see
// tool_call_rescue.py) or bare-identifier keys — both fail JSON.parse, which
// is fine here: display only falls back to a generic label, it never decodes.
function parseToolCallName(raw: string): string | null {
	try {
		const data = JSON.parse(raw.trim());
		if (data && typeof data === 'object') {
			if (typeof data.name === 'string') return data.name;
			if (typeof data.function?.name === 'string') return data.function.name;
		}
	} catch {
		// malformed/mangled payload — caller falls back to a generic label
	}
	return null;
}

/**
 * Extract `<tool_call>` spans from raw assistant text before markdown
 * rendering, leaving a `\x00TOOLCALL{n}\x00` placeholder in their place (same
 * convention as the fenced-code-block placeholders in processMarkdown) so a
 * chip can later be substituted in at the exact position the tag occupied. A
 * trailing `<tool_call>` with no closing tag — a mid-stream partial write —
 * is captured too, as an 'unclosed' span running to the end of the text.
 */
export function parseToolCalls(text: string): { cleanedText: string; calls: ToolCallSpan[] } {
	const calls: ToolCallSpan[] = [];
	let cleanedText = text.replace(TOOL_CALL_RE, (_match, inner: string) => {
		const index = calls.length;
		calls.push({ status: 'complete', toolName: parseToolCallName(inner) });
		return `\x00TOOLCALL${index}\x00`;
	});

	// Every complete pair is already gone, so any `<tool_call>` still present
	// has no matching close; take the last one as the streaming tail.
	const lastOpen = cleanedText.lastIndexOf('<tool_call>');
	if (lastOpen !== -1) {
		const index = calls.length;
		calls.push({ status: 'unclosed', toolName: null });
		cleanedText = cleanedText.slice(0, lastOpen) + `\x00TOOLCALL${index}\x00`;
	}

	return { cleanedText, calls };
}

function toolCallLabel(name: string | null): string {
	if (!name) return 'Tool call';
	return name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

// Same visual language as ChatToolChip (dot + mono label, h-6 rounded border
// chip) but plain markup: this renders inline inside {@html} message content,
// where a Svelte component can't be mounted.
function toolCallChipHtml(call: ToolCallSpan, isStreaming: boolean): string {
	if (call.status === 'unclosed') {
		return isStreaming
			? '<span class="inline-flex items-center gap-1.5 h-6 px-1.5 rounded border border-line text-fg-muted font-mono text-xs align-middle mx-0.5">' +
					'<span class="w-1.5 h-1.5 rounded-full bg-signal motion-safe:animate-pulse flex-shrink-0" role="status" aria-label="Calling tool"></span>' +
					'<span>Calling tool…</span>' +
					'</span>'
			: '<span class="inline-flex items-center gap-1.5 h-6 px-1.5 rounded border border-line text-fg-disabled font-mono text-xs align-middle mx-0.5">' +
					'<span class="w-1.5 h-1.5 rounded-full bg-fg-disabled flex-shrink-0"></span>' +
					'<span>Tool call was cut off</span>' +
					'</span>';
	}
	const label = escapeHtmlAttr(toolCallLabel(call.toolName));
	return (
		'<span class="inline-flex items-center gap-1.5 h-6 px-1.5 rounded border border-line text-fg-subtle font-mono text-xs align-middle mx-0.5">' +
		'<span class="w-1.5 h-1.5 rounded-full bg-fg-subtle/50 flex-shrink-0"></span>' +
		`<span class="truncate max-w-[160px]">${label}</span>` +
		'</span>'
	);
}

/**
 * Replace `\x00TOOLCALL{n}\x00` placeholders in already-rendered message HTML
 * with the chip markup for each extracted span. `isStreaming` decides the
 * unclosed-span state: a pulsing "Calling tool…" chip mid-stream, or a muted
 * "cut off" chip once the message (and therefore the tag) has settled.
 */
export function injectToolCallChips(html: string, calls: ToolCallSpan[], isStreaming: boolean): string {
	if (calls.length === 0) return html;
	return html.replace(/\x00TOOLCALL(\d+)\x00/g, (match, index: string) => {
		const call = calls[parseInt(index, 10)];
		return call ? toolCallChipHtml(call, isStreaming) : match;
	});
}

export function parseToolActions(text: string): { cleanedText: string; actions: ToolAction[] } {
	const actions: ToolAction[] = [];
	const regex =
		/<tool_action\s+type="([^"]+)"\s+segment_index="(\d+)"\s+segment_id="([^"]+)">([\s\S]*?)<\/tool_action>/g;

	const cleanedText = text.replace(regex, (_match, type, segmentIndex, segmentId, content) => {
		actions.push({
			type,
			segmentIndex: parseInt(segmentIndex, 10),
			segmentId,
			content: content.trim()
		});
		return '';
	});

	return { cleanedText: cleanedText.trim(), actions };
}

export function processMarkdownWithActions(
	text: string,
	options: MarkdownOptions = {}
): { html: string; actions: ToolAction[]; toolCalls: ToolCallSpan[] } {
	const { cleanedText: withoutToolCalls, calls: toolCalls } = parseToolCalls(text);
	const { cleanedText, actions } = parseToolActions(withoutToolCalls);
	const html = processMarkdown(cleanedText, options);
	return { html, actions, toolCalls };
}

const REPLY_CONTRACT_MARKER_RE = /^##\s*(?:improved|questions)\b/im;

/**
 * The backend only strips `## improved` / `## questions` reply-contract
 * section markers from `content` once a turn finishes (see
 * `parsed_content.reply_contract`); mid-stream, an accumulated `token`
 * delta can still contain the raw marker. Cut the displayed prose at the
 * first marker so the markup never flashes on screen before the backend's
 * final clean copy replaces it.
 */
export function truncateAtReplyContractMarker(text: string): string {
	const match = text.match(REPLY_CONTRACT_MARKER_RE);
	if (!match || match.index === undefined) return text;
	return text.slice(0, match.index).trimEnd();
}
