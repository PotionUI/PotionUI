/**
 * Process template strings for rich display - returns HTML string
 * Supports template markers like:
 * - <<PIPE:name:icon>> - pipeline step names
 * - <<PROGRESS:value:icon>> - progress indicators
 * - <<MODEL:name:icon>> / <<GPU:...>> / <<RESOLUTION:...>> / <<NUMBER:...>> / <<EFFECT:...>> / <<TIME:...>> - metadata values
 *
 * Markers render as segments of a single mono HUD readout line
 * (KSAMPLER · 12% · 1024×1024) — no pills, no icons. Data is mono,
 * progress is signal-colored, everything else sits in the neutral text
 * tiers. Plain text between markers keeps the surrounding font.
 */

const HUD_SEGMENT_BASE =
	'font-mono text-xs font-medium uppercase tracking-[0.07em] tabular-nums whitespace-nowrap';

const HUD_SEPARATOR = '<span class="text-fg-subtle select-none">&nbsp;·&nbsp;</span>';

function markerColor(type: string): string | null {
	switch (type) {
		case 'PIPE':
			return 'text-fg'; // where we are in the pipeline — primary
		case 'PROGRESS':
			return 'text-signal'; // progress = state
		case 'MODEL':
		case 'GPU':
		case 'RESOLUTION':
		case 'NUMBER':
		case 'EFFECT':
		case 'TIME':
			return 'text-fg-muted';
		default:
			return null;
	}
}

export function processTemplateString(text: string): string {
	if (!text) return text;

	// Split by << >> markers
	const parts = text.split(/(<<[^>]+>>)/g);

	const rendered: { html: string; isMarker: boolean; isBlank: boolean }[] = parts.map((part) => {
		if (part.startsWith('<<') && part.endsWith('>>')) {
			const content = part.slice(2, -2);
			const params = content.split(':');
			const type = params[0]?.toUpperCase();
			const value = params[1];

			// If no type or value, display as plain text
			if (!type || !value) {
				return { html: `<span class="text-xs text-fg-subtle">${part}</span>`, isMarker: false, isBlank: false };
			}

			const color = markerColor(type);
			if (!color) {
				// Unrecognized type, display as plain text
				return { html: `<span class="text-xs text-fg-subtle">${part}</span>`, isMarker: false, isBlank: false };
			}

			return {
				html: `<span class="${HUD_SEGMENT_BASE} ${color}">${value}</span>`,
				isMarker: true,
				isBlank: false
			};
		}
		return { html: part, isMarker: false, isBlank: part.trim() === '' };
	});

	// Join segments; consecutive markers separated only by whitespace get a
	// middot separator so adjacent values read as one HUD line.
	let result = '';
	for (let i = 0; i < rendered.length; i++) {
		const seg = rendered[i];
		if (seg.isBlank) {
			const prev = rendered[i - 1];
			const next = rendered[i + 1];
			if (prev?.isMarker && next?.isMarker) {
				result += HUD_SEPARATOR;
				continue;
			}
		}
		result += seg.html;
	}
	return result;
}

export interface StatusMarker {
	type: string;
	value: string;
}

/**
 * Split a status string into its data markers and the remaining plain prose.
 * Lets consumers lay the two out separately (HUD data line vs message line)
 * instead of one flowing mixed line.
 */
export function parseTemplateMarkers(text: string): { markers: StatusMarker[]; plain: string } {
	const markers: StatusMarker[] = [];
	if (!text) return { markers, plain: '' };

	const plain = text
		.replace(/<<([^:>]+):([^:>]*)(?::[^>]*)?>>/g, (_match, type: string, value: string) => {
			if (type && value) markers.push({ type: type.toUpperCase(), value });
			return '';
		})
		.replace(/\s+/g, ' ')
		.trim();

	return { markers, plain };
}

/**
 * Helper to remove pipe template markers from message
 */
export function removePipeFromMessage(message: string): string {
	if (!message) return '';
	let result = message.replace(/<<PIPE:[^>]*>>/g, '');
	result = result.replace(/\s+/g, ' ').trim();
	return result;
}

/**
 * Extract pipe name from template string
 */
export function extractPipeName(message: string): string | undefined {
	const pipeMatch = message.match(/<<PIPE:([^:>]+)(?::[^>]*)?>>/);
	return pipeMatch ? pipeMatch[1] : undefined;
}
