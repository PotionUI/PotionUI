// DOM tree-walking for InlineChipEditor.svelte, extracted unchanged.
// Pure given a real (or hand-built, e.g. jsdom) DOM root — no Svelte/component
// state beyond the `chips` map, threaded in explicitly instead of closed over.
import type { ChipData } from '$lib/types/segments';
import { encodePathForText, type ContentSegment } from './chipSegments';

/** Build the (not-yet-mounted) DOM node for one content segment. Shared by
 *  every place that rebuilds the editor's DOM from `contentSegments`. */
export function buildSegmentNode(segment: ContentSegment): Node {
	if (segment.type === 'chip' && segment.chipId && segment.chipData) {
		const el = document.createElement('span');
		el.dataset.chipId = segment.chipId;
		el.contentEditable = 'false';
		el.className = 'inline-chip-container';
		el.style.cssText = 'user-select: none; display: inline;';
		return el;
	}
	if (segment.type === 'group' && segment.groupRaw) {
		const el = document.createElement('span');
		el.dataset.groupRaw = segment.groupRaw;
		el.contentEditable = 'false';
		el.className = 'choice-group-container';
		el.style.cssText = 'user-select: none; display: inline;';
		return el;
	}
	if (segment.type === 'variable' && segment.variableRaw) {
		const el = document.createElement('span');
		el.dataset.variableRaw = segment.variableRaw;
		el.dataset.variableName = segment.variableName || '';
		el.contentEditable = 'false';
		el.className = 'variable-usage-container';
		el.style.cssText = 'user-select: none; display: inline;';
		return el;
	}
	return document.createTextNode(segment.content);
}

/** The three container kinds that are atomic to the caret: contentEditable=false
 *  spans that a single Backspace/Delete must remove whole. */
export type AtomicKind = 'chip' | 'group' | 'variable';

export interface AtomicTarget {
	kind: AtomicKind;
	el: HTMLElement;
	/** Only set for `kind: 'chip'` - the key into the `chips` map. */
	chipId?: string;
}

/** Classify a node as one of the atomic containers, or null if it is anything else. */
export function atomicContainerAt(node: Node | null | undefined): AtomicTarget | null {
	if (!node || node.nodeType !== Node.ELEMENT_NODE) return null;
	const el = node as HTMLElement;
	if (el.dataset?.chipId) return { kind: 'chip', el, chipId: el.dataset.chipId };
	if (el.dataset?.groupRaw !== undefined) return { kind: 'group', el };
	if (el.dataset?.variableRaw !== undefined) return { kind: 'variable', el };
	return null;
}

/**
 * The atomic container a collapsed caret at (`container`, `offset`) would eat
 * whole, or null when the key press is an ordinary character delete.
 *
 * Both directions are answered here so Forward-Delete cannot drift from
 * Backspace: the browser will not delete a contentEditable=false span on its
 * own in either direction, and only Backspace used to be intercepted - so
 * Delete in front of a chip silently did nothing.
 *
 * The two caret shapes that matter mirror `getCaretCharOffset`: inside a text
 * node (offset is a character index), or directly on the editor element
 * (offset is a CHILD INDEX, which is what `Range.setStartAfter` on a chip
 * produces).
 */
export function atomicDeletionTarget(
	editorRef: HTMLElement | null | undefined,
	container: Node | null | undefined,
	offset: number,
	direction: 'backward' | 'forward'
): AtomicTarget | null {
	if (!editorRef || !container) return null;

	if (container.nodeType === Node.TEXT_NODE) {
		const length = (container.textContent || '').length;
		if (direction === 'backward') {
			return offset === 0 ? atomicContainerAt(container.previousSibling) : null;
		}
		return offset === length ? atomicContainerAt(container.nextSibling) : null;
	}

	if (container === editorRef) {
		const children = editorRef.childNodes;
		if (direction === 'backward') {
			return offset > 0 ? atomicContainerAt(children[offset - 1]) : null;
		}
		return offset < children.length ? atomicContainerAt(children[offset]) : null;
	}

	return null;
}

export function extractContentFromDOM(
	editorRef: HTMLElement | undefined | null,
	chips: Record<string, ChipData>
): { value: string; chips: Record<string, ChipData> } {
	if (!editorRef) return { value: '', chips: {} };

	let textContent = '';
	const extractedChips: Record<string, ChipData> = {};

	function walkNode(node: Node) {
		if (node.nodeType === Node.TEXT_NODE) {
			textContent += node.textContent || '';
		} else if (node.nodeType === Node.ELEMENT_NODE) {
			const el = node as HTMLElement;

			// Check for a choice-group container — its raw `{a|b|c}` text IS the
			// value, no separate map entry (unlike #chips, which reference an
			// external phrasebook value by id).
			if (el.dataset.groupRaw !== undefined) {
				textContent += el.dataset.groupRaw;
			} else if (el.dataset.variableRaw !== undefined) {
				// Same "raw is truth" rule as a group container — the chip is a
				// view over the exact `${name}` text, never a second source of it.
				textContent += el.dataset.variableRaw;
			} else if (el.dataset.chipId) {
				const chipId = el.dataset.chipId;
				if (chips[chipId]) {
					// Use encoded format for paths with spaces
					textContent += encodePathForText(chips[chipId].categoryPath);
					extractedChips[chipId] = chips[chipId];
				}
			} else if (el.tagName === 'BR') {
				textContent += '\n';
			} else {
				// Walk children
				for (const child of Array.from(node.childNodes)) {
					walkNode(child);
				}
				// Add newline after block elements
				if (el.tagName === 'DIV' && el.nextSibling) {
					textContent += '\n';
				}
			}
		}
	}

	for (const child of Array.from(editorRef.childNodes)) {
		walkNode(child);
	}

	return { value: textContent, chips: extractedChips };
}

/** Absolute-offset spans of every real Text node under `root`, in document
 *  order — the same walk as `extractContentFromDOM`/`getCaretCharOffset`,
 *  but collecting node references instead of concatenating characters, so a
 *  match's [start, end) can be turned into a `Range` on the actual DOM. */
export function collectTextNodeSpans(
	root: HTMLElement,
	chips: Record<string, ChipData>
): Array<{ node: Text; start: number; end: number }> {
	const spans: Array<{ node: Text; start: number; end: number }> = [];
	let offset = 0;

	function walk(node: Node) {
		if (node.nodeType === Node.TEXT_NODE) {
			const len = (node.textContent || '').length;
			spans.push({ node: node as Text, start: offset, end: offset + len });
			offset += len;
			return;
		}
		if (node.nodeType === Node.ELEMENT_NODE) {
			const el = node as HTMLElement;
			if (el.dataset.groupRaw !== undefined) {
				offset += el.dataset.groupRaw.length;
			} else if (el.dataset.variableRaw !== undefined) {
				offset += el.dataset.variableRaw.length;
			} else if (el.dataset.chipId && chips[el.dataset.chipId]) {
				offset += encodePathForText(chips[el.dataset.chipId].categoryPath).length;
			} else if (el.tagName === 'BR') {
				offset += 1;
			} else {
				for (const child of Array.from(node.childNodes)) walk(child);
			}
		}
	}

	for (const child of Array.from(root.childNodes)) walk(child);
	return spans;
}
