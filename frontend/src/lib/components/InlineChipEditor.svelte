<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { createEventDispatcher, onMount, onDestroy, tick, mount, unmount } from 'svelte';
	import { api, type PhrasebookCategory, type PhrasebookValue } from '$lib/services/api/index';
	import type { ChipData } from '$lib/types/segments';
	import AutocompleteDropdown from './AutocompleteDropdown.svelte';
	import type {
		AutocompleteCategory as DropdownAutocompleteCategory,
		AutocompleteValue as DropdownAutocompleteValue
	} from './AutocompleteDropdown.svelte';
	import InlineChip from './InlineChip.svelte';
	import ChoiceGroupChip from './ChoiceGroupChip.svelte';
	import VariableUsageChip from './VariableUsageChip.svelte';
	import { countChoiceGroups } from '$lib/utils/choiceGroups';
	import {
		detectVariableTrigger,
		filterVariableNames,
		variableUsageSyntax,
		countVariableUsages
	} from '$lib/utils/promptVariables';
	import { findTriggerWordMatches } from '$lib/utils/triggerWords';
	import {
		clearOwnerTriggerHighlightRanges,
		setOwnerTriggerHighlightRanges
	} from '$lib/utils/triggerWordHighlight';
	import {
		normalizeVariableDef,
		createTextVariable,
		hashVariablesMap,
		stepVariablesHash,
		type VariablesMap,
		type VariableDef,
		type VariableRoll,
		type ChoiceVariableMode
	} from '$lib/utils/variableDefs';
	import { encodePathForText, parseValueToSegments } from './chipSegments';
	import {
		buildSegmentNode,
		extractContentFromDOM,
		collectTextNodeSpans,
		atomicDeletionTarget
	} from './chipEditorDom';
	import { getCaretCharOffset, placeCaretAtCharOffset } from './chipEditorCaret';
	import { variablePreview, getChipValueMap, getChipsHash, hashVariableRolls } from './chipEditorHelpers';

	// Track mounted chip components for cleanup
	let mountedChipComponents: Map<string, ReturnType<typeof mount>> = new Map();

	export let value: string;
	export let chips: Record<string, ChipData> = {};
	export let placeholder: string = 'Enter prompt content... (Ctrl+Enter for AI, # for phrasebook)';
	export let disabled: boolean = false;
	export let isRegenerating: boolean = false;
	export let segmentDisabled: boolean = false;
	export let borderless: boolean = false;
	export let density: 'default' | 'compact' = 'default';
	// Flow view: render this segment's text as one inline run inside
	// a shared wrapping paragraph rather than its own block — no border,
	// padding, min-height, or placeholder ghost text. Chip/group/variable
	// mechanics below are completely unaffected; only the outer box's own
	// display mode changes.
	export let flow: boolean = false;
	// Prompt variables defined on the tab (see stores/tabs.ts `Tab.variables`),
	// offered by the `$` picker below. Not persisted by this component — purely
	// a lookup source for the picker's suggestion list. Each entry is a
	// typed def (text | choice) or a legacy bare string.
	export let variables: VariablesMap = {};
	// RUN state (this tab's last roll per shuffle-mode choice variable — see
	// Tab.variableRolls), deliberately separate from `variables` itself.
	// Usage chips read it to show "$name · value".
	export let variableRolls: Record<string, VariableRoll> = {};
	// A `${name}` usage renders as a chip whose popover edits the variable's
	// DEFINITION (mode/pin, or create when undefined) — that edit has to bubble
	// up to wherever `Tab.variables` actually lives (tabsStore, via
	// PromptSection.svelte), several components above this one, so it's a
	// callback prop threaded down rather than local state.
	export let onVariableDefChange: ((name: string, def: VariableDef) => void) | undefined = undefined;
	export let onOpenVariableManager: (() => void) | undefined = undefined;
	// Trigger words of the active LoRA(s) for this tab (see
	// $lib/stores/activeLoraTriggers.ts) — occurrences inside this editor's text
	// are highlighted via the CSS Custom Highlight API, mirroring the "already
	// in prompt" indicator LoraPickerField.svelte shows on the trigger chip.
	export let activeTriggerWords: string[] = [];

	const dispatch = createEventDispatcher();

	// DOM references
	let editorRef: HTMLDivElement;
	let containerRef: HTMLDivElement;

	// Owns this instance's subset of ranges in the shared trigger-word Highlight.
	const triggerHighlightOwner = Symbol('inline-chip-editor-trigger-highlight');

	// Phrasebook state
	let isPhrasebookOpen = false;
	let phrasebookCategories: PhrasebookCategory[] = [];
	let phrasebookSuggestions: PhrasebookValue[] = [];
	let phrasebookSelectedIndex = 0;
	let phrasebookLoading = false;
	let phrasebookPath = '';
	let phrasebookTriggerNode: Text | null = null; // The text node containing the # trigger
	let phrasebookTriggerOffset = -1; // Position of # in that text node
	let debounceTimer: ReturnType<typeof setTimeout> | null = null;
	let phrasebookCache = new Map<string, any>();

	// `$variable` picker state — mirrors the # phrasebook state above, but the
	// suggestion list is a synchronous filter over `variables` rather than an
	// API call.
	let isVariablePickerOpen = false;
	let variableQuery = '';
	let variableTriggerNode: Text | null = null;
	let variableTriggerOffset = -1;
	let variableSelectedIndex = 0;

	// Deliberately untyped against the imported (services/api) PhrasebookValue —
	// AutocompleteDropdown.svelte declares its own narrower structural type for
	// this prop, and the two don't line up (pre-existing across the file: the #
	// phrasebook dropdown below has the same mismatch). Only `id`/`label`/
	// `value` are read by the selection handler below.
	$: variableSuggestions = isVariablePickerOpen
		? filterVariableNames(Object.keys(variables), variableQuery).map((name) => ({
				id: name,
				category_id: '',
				label: name,
				value: variablePreview(name, variables),
				sort_order: 0,
				created_at: '',
				updated_at: ''
			}))
		: [];

	// Choice-group chip state. Groups have no persistent id/map
	// of their own — `{a|b|c}` is already the source of truth in `value`, a
	// group container's `data-group-raw` attribute just carries the current
	// text for the mounted chip. `groupSlotId` is a purely-local DOM/Svelte
	// mount key (like `chipId` below), never written into `value` or dispatched.
	let mountedGroupComponents: Map<string, ReturnType<typeof mount>> = new Map();
	let groupSlotCounter = 0;
	// Count of balanced groups in `value` as of the last input, used to notice
	// "the user just typed a `}` that closed a new group" without diffing text.
	let lastGroupCount = 0;

	// Variable-usage chip state — mirrors the group state above exactly.
	// `${name}` has no persistent id/map of its own either: the container's
	// `data-variable-raw` carries the exact `${name}` text.
	let mountedVariableComponents: Map<string, ReturnType<typeof mount>> = new Map();
	let variableSlotCounter = 0;
	// Count of `${name}` usages in `value` as of the last input — the variable
	// analog of lastGroupCount, for noticing a manually-typed `${name}` closing.
	let lastVariableUsageCount = 0;

	// Track if we're programmatically updating
	let isInternalUpdate = false;

	$: isDisabled = disabled || isRegenerating || segmentDisabled;

	// Parse value into segments (text, #chips, {a|b|c} choice groups, and
	// ${name} variable usages) — see chipSegments.ts.
	$: contentSegments = parseValueToSegments(value, chips);

	// =====================
	// Trigger-word highlighting
	// =====================

	/** Recomputes this editor's trigger-word `Range`s against `text` and hands
	 *  them to the shared Highlight (see triggerWordHighlight.ts). Pure
	 *  decoration — never touches the DOM tree, so it's safe to call on every
	 *  keystroke without disturbing the caret. A match that straddles an atomic
	 *  chip/group/variable container (i.e. doesn't fall entirely inside one text
	 *  node) is skipped rather than partially highlighted. */
	function refreshTriggerHighlights(text: string) {
		if (!editorRef) return;
		const matches = findTriggerWordMatches(text, activeTriggerWords);
		if (matches.length === 0) {
			clearOwnerTriggerHighlightRanges(triggerHighlightOwner);
			return;
		}

		const spans = collectTextNodeSpans(editorRef, chips);
		const ranges: Range[] = [];
		for (const m of matches) {
			const span = spans.find((s) => m.start >= s.start && m.end <= s.end);
			if (!span) continue;
			const range = document.createRange();
			range.setStart(span.node, m.start - span.start);
			range.setEnd(span.node, m.end - span.start);
			ranges.push(range);
		}
		setOwnerTriggerHighlightRanges(triggerHighlightOwner, ranges);
	}

	// =====================
	// Input Handling
	// =====================

	function handleInput() {
		if (isInternalUpdate) return;

		const { value: newValue, chips: newChips } = extractContentFromDOM(editorRef, chips);

		// Detect phrasebook/variable-picker triggers (mutually exclusive)
		detectPhrasebookTrigger();
		detectVariablePickerTrigger();

		// A group/variable container is contentEditable=false, so the user can
		// only ever be typing in plain text here — a newly-*higher* count means
		// a `}` was just typed that closed a brand-new `{a|b|c}` or `${name}`.
		// Rebuild to chip-ify it — close the brace, create a chip, for both
		// token kinds.
		const groupsNow = countChoiceGroups(newValue);
		const variablesNow = countVariableUsages(newValue);
		const justClosedGroup = groupsNow > lastGroupCount;
		const justClosedVariable = variablesNow > lastVariableUsageCount;
		lastGroupCount = groupsNow;
		lastVariableUsageCount = variablesNow;

		refreshTriggerHighlights(newValue);

		// Emit change
		dispatch('change', { value: newValue, chips: newChips });

		if (justClosedGroup || justClosedVariable) {
			chipifyNewTokens(newValue, newChips);
		}
	}

	function handleKeyDown(e: KeyboardEvent) {
		// Handle $variable picker navigation
		if (isVariablePickerOpen) {
			if (e.key === 'ArrowDown') {
				e.preventDefault();
				variableSelectedIndex = variableSuggestions.length
					? (variableSelectedIndex + 1) % variableSuggestions.length
					: 0;
				return;
			} else if (e.key === 'ArrowUp') {
				e.preventDefault();
				variableSelectedIndex = variableSelectedIndex === 0
					? Math.max(variableSuggestions.length - 1, 0)
					: variableSelectedIndex - 1;
				return;
			} else if (e.key === 'Enter' && !e.ctrlKey) {
				e.preventDefault();
				if (variableSuggestions[variableSelectedIndex]) {
					handleSelectVariable(variableSuggestions[variableSelectedIndex]);
				}
				return;
			} else if (e.key === 'Escape') {
				e.preventDefault();
				closeVariablePicker();
				return;
			}
		}

		// Handle phrasebook navigation
		if (isPhrasebookOpen) {
			const totalItems = phrasebookCategories.length + phrasebookSuggestions.length;

			if (e.key === 'ArrowDown') {
				e.preventDefault();
				phrasebookSelectedIndex = (phrasebookSelectedIndex + 1) % totalItems;
				return;
			} else if (e.key === 'ArrowUp') {
				e.preventDefault();
				phrasebookSelectedIndex =
					phrasebookSelectedIndex === 0 ? totalItems - 1 : phrasebookSelectedIndex - 1;
				return;
			} else if (e.key === 'Enter' && !e.ctrlKey) {
				e.preventDefault();
				selectPhrasebookItem();
				return;
			} else if (e.key === 'Escape') {
				e.preventDefault();
				closePhrasebook();
				return;
			}
		}

		// Delete a chip/group/variable whole when the caret sits against it.
		// Both directions: a chip container is contentEditable=false, so the
		// browser removes it for neither Backspace nor Delete on its own.
		if (e.key === 'Backspace' || e.key === 'Delete') {
			const selection = window.getSelection();
			if (selection && selection.rangeCount > 0) {
				const range = selection.getRangeAt(0);

				if (range.collapsed) {
					const target = atomicDeletionTarget(
						editorRef,
						range.startContainer,
						range.startOffset,
						e.key === 'Backspace' ? 'backward' : 'forward'
					);

					if (target) {
						e.preventDefault();
						if (target.kind === 'chip' && target.chipId) {
							handleChipRemove(target.chipId);
						} else if (target.kind === 'group') {
							handleGroupRemove(target.el);
						} else {
							handleVariableUsageRemove(target.el);
						}
						return;
					}
				}
			}
		}

		// Pass through other key events
		dispatch('keydown', e);
	}

	function handlePaste(e: ClipboardEvent) {
		e.preventDefault();

		const text = e.clipboardData?.getData('text/plain') || '';

		// Insert text at cursor
		const selection = window.getSelection();
		if (!selection || !selection.rangeCount) return;

		const range = selection.getRangeAt(0);
		range.deleteContents();

		const textNode = document.createTextNode(text);
		range.insertNode(textNode);

		// Move cursor to end of inserted text
		range.setStartAfter(textNode);
		range.collapse(true);
		selection.removeAllRanges();
		selection.addRange(range);

		handleInput();
	}

	// =====================
	// Phrasebook
	// =====================

	function detectPhrasebookTrigger() {
		const selection = window.getSelection();
		if (!selection || !selection.rangeCount || !editorRef) return;

		const range = selection.getRangeAt(0);

		// Only detect in text nodes
		if (range.startContainer.nodeType !== Node.TEXT_NODE) {
			closePhrasebook();
			return;
		}

		const textNode = range.startContainer as Text;
		const text = textNode.textContent || '';
		const cursorOffset = range.startOffset;

		// Look backwards for # trigger (stop at newline or another #)
		let start = cursorOffset - 1;

		while (start >= 0 && text[start] !== '#' && text[start] !== '\n') {
			start--;
		}

		// Check if we found a # trigger
		if (start >= 0 && text[start] === '#') {
			const path = text.substring(start + 1, cursorOffset);

			// Validate path format - allow word chars, dots, and spaces
			// Path should start with a word char and can contain dots/spaces
			if (/^[\w][\w.\s]*$/.test(path) || /^[\w]*$/.test(path)) {
				closeVariablePicker(); // mutually exclusive with the $ picker
				phrasebookPath = path;
				phrasebookTriggerNode = textNode; // Store the text node
				phrasebookTriggerOffset = start; // Store the position of #
				isPhrasebookOpen = true;

				// Debounce API call
				if (debounceTimer) clearTimeout(debounceTimer);

				debounceTimer = setTimeout(() => {
					fetchPhrasebookSuggestions(path);
				}, 200);
				return;
			}
		}

		closePhrasebook();
	}

	async function fetchPhrasebookSuggestions(path: string) {
		// Check cache first
		if (phrasebookCache.has(path)) {
			const cached = phrasebookCache.get(path);
			phrasebookCategories = cached.child_categories || [];
			phrasebookSuggestions = cached.values || [];
			phrasebookSelectedIndex = 0;
			phrasebookLoading = false;
			return;
		}

		phrasebookLoading = true;

		try {
			const response = await api.searchPhrasebook(path);
			if (response.success && response.data) {
				phrasebookCache.set(path, response.data);
				if (phrasebookCache.size > 50) {
					const firstKey = phrasebookCache.keys().next().value;
					if (firstKey) phrasebookCache.delete(firstKey);
				}
				phrasebookCategories = response.data.child_categories || [];
				phrasebookSuggestions = response.data.values || [];
				phrasebookSelectedIndex = 0;
			}
		} catch (error) {
			logger.error('Failed to fetch phrasebook suggestions:', error);
			phrasebookCategories = [];
			phrasebookSuggestions = [];
		} finally {
			phrasebookLoading = false;
		}
	}

	function selectPhrasebookItem() {
		const totalCategories = phrasebookCategories.length;

		if (phrasebookSelectedIndex < totalCategories) {
			handleSelectCategory(phrasebookCategories[phrasebookSelectedIndex]);
		} else {
			const valueIndex = phrasebookSelectedIndex - totalCategories;
			handleSelectValue(phrasebookSuggestions[valueIndex]);
		}
	}

	function handleSelectCategory(category: DropdownAutocompleteCategory) {
		const selection = window.getSelection();
		if (!selection || !selection.rangeCount) return;

		const range = selection.getRangeAt(0);
		if (range.startContainer.nodeType !== Node.TEXT_NODE) return;

		const textNode = range.startContainer as Text;
		const text = textNode.textContent || '';
		const cursorOffset = range.startOffset;

		// Find the # trigger position
		let triggerStart = cursorOffset - 1;
		while (triggerStart >= 0 && text[triggerStart] !== '#') {
			triggerStart--;
		}

		if (triggerStart < 0) return;

		// Build the full path for navigation
		// category.path might be just the name (e.g., "distress") or full path (e.g., "emotions.distress")
		const categoryName = category.path || category.name || '';
		if (!categoryName) return;

		let fullPath: string;

		// Check if we're navigating INTO a subcategory (phrasebookPath ends with '.')
		// vs selecting a top-level category (user typed partial match like "emo" for "emotions")
		const isInsideCategory = phrasebookPath.endsWith('.');

		if (isInsideCategory) {
			// We're inside a category looking at its children
			const parentPath = phrasebookPath.slice(0, -1); // Remove trailing dot

			// Check if categoryName already includes the full path
			if (categoryName.startsWith(parentPath + '.')) {
				// Already a full path
				fullPath = categoryName;
			} else if (categoryName.includes('.')) {
				// Has dots, might be a full path from root
				fullPath = categoryName;
			} else {
				// Just the subcategory name, prepend parent path
				fullPath = parentPath + '.' + categoryName;
			}
		} else {
			// Top-level category selection - use category path directly
			fullPath = categoryName;
		}

		// Navigate into category by appending a dot
		const newPath = fullPath + '.';
		const newText = text.substring(0, triggerStart + 1) + newPath + text.substring(cursorOffset);

		// Calculate new cursor position before modifying DOM
		const newCursorPos = triggerStart + 1 + newPath.length;

		// Update the text node
		textNode.textContent = newText;

		// Restore cursor position (use tick to ensure DOM is updated)
		tick().then(() => {
			const newSelection = window.getSelection();
			if (newSelection && textNode.parentNode) {
				const newRange = document.createRange();
				// Make sure cursor position is within bounds
				const safePos = Math.min(newCursorPos, textNode.textContent?.length || 0);
				newRange.setStart(textNode, safePos);
				newRange.collapse(true);
				newSelection.removeAllRanges();
				newSelection.addRange(newRange);
			}
		});

		// Update phrasebook state
		phrasebookPath = newPath;
		fetchPhrasebookSuggestions(newPath);

		// Don't call handleInput here - it can trigger DOM sync and lose cursor
		// Instead, extract and dispatch manually
		const { value: newValue, chips: newChips } = extractContentFromDOM(editorRef, chips);
		dispatch('change', { value: newValue, chips: newChips });
	}

	function handleSelectValue(valueItem: DropdownAutocompleteValue) {
		if (!editorRef || !phrasebookTriggerNode || phrasebookTriggerOffset < 0) return;

		// Calculate the trigger index in the full extracted text
		// by counting characters up to the trigger node + offset
		let triggerIndex = 0;
		let foundTrigger = false;

		function countCharsUntilTrigger(node: Node): boolean {
			if (foundTrigger) return true;

			if (node === phrasebookTriggerNode) {
				triggerIndex += phrasebookTriggerOffset;
				foundTrigger = true;
				return true;
			}

			if (node.nodeType === Node.TEXT_NODE) {
				triggerIndex += (node.textContent || '').length;
			} else if (node.nodeType === Node.ELEMENT_NODE) {
				const el = node as HTMLElement;
				if (el.dataset.groupRaw !== undefined) {
					triggerIndex += el.dataset.groupRaw.length;
				} else if (el.dataset.variableRaw !== undefined) {
					triggerIndex += el.dataset.variableRaw.length;
				} else if (el.dataset.chipId && chips[el.dataset.chipId]) {
					// Chip contributes its encoded path length
					triggerIndex += encodePathForText(chips[el.dataset.chipId].categoryPath).length;
				} else if (el.tagName === 'BR') {
					triggerIndex += 1; // newline
				} else {
					for (const child of Array.from(node.childNodes)) {
						if (countCharsUntilTrigger(child)) return true;
					}
				}
			}
			return false;
		}

		for (const child of Array.from(editorRef.childNodes)) {
			if (countCharsUntilTrigger(child)) break;
		}

		if (!foundTrigger) return;

		// Extract current content preserving existing chip markers
		const { value: fullText, chips: existingChips } = extractContentFromDOM(editorRef, chips);

		// Create new chip
		const chipId = `chip-${Date.now()}-${Math.random().toString(36).substring(2, 11)}`;
		const categoryPath = valueItem.category_path || phrasebookPath.replace(/\.$/, '');

		const chipData: ChipData = {
			id: chipId,
			categoryPath,
			valueId: valueItem.id,
			label: valueItem.label,
			value: valueItem.value,
			allValues: phrasebookSuggestions.map((v) => ({
				id: v.id,
				label: v.label,
				value: v.value,
				preview_file_id: v.preview_file_id
			})),
			shuffle: false,
			autoRegen: false
		};

		// Calculate new value by replacing #path with encoded categoryPath
		const beforeTrigger = fullText.substring(0, triggerIndex);
		// Calculate the length of what we're replacing: # + the typed path
		// The typed path might have a trailing dot from navigation
		const typedPathLength = phrasebookPath.length;
		const afterTrigger = fullText.substring(triggerIndex + 1 + typedPathLength);
		// Use encoded format for the placeholder (brackets for paths with spaces)
		const placeholder = encodePathForText(categoryPath);
		const newValue = beforeTrigger + placeholder + afterTrigger;

		// Merge existing chips with new chip
		const newChips = { ...existingChips, [chipId]: chipData };
		chips = newChips;

		closePhrasebook();

		// Rebuild the entire editor content with the new chip
		isInternalUpdate = true;
		editorRef.innerHTML = '';

		const newSegments = parseValueToSegments(newValue, newChips);
		newSegments.forEach((segment) => {
			editorRef.appendChild(buildSegmentNode(segment));
		});

		// Mount all chips + groups + variable usages
		mountChips();
		mountGroups();
		mountVariables();
		isInternalUpdate = false;
		refreshTriggerHighlights(newValue);

		// Position cursor right after the newly inserted chip
		tick().then(() => {
			const selection = window.getSelection();
			if (selection && editorRef) {
				// Find the chip container we just inserted
				const chipContainer = editorRef.querySelector(`[data-chip-id="${chipId}"]`);
				if (chipContainer) {
					const range = document.createRange();
					// Position cursor right after the chip
					range.setStartAfter(chipContainer);
					range.collapse(true);
					selection.removeAllRanges();
					selection.addRange(range);
				} else {
					// Fallback: position at end
					const range = document.createRange();
					range.selectNodeContents(editorRef);
					range.collapse(false);
					selection.removeAllRanges();
					selection.addRange(range);
				}
			}
		});

		// Emit change to parent
		dispatch('change', { value: newValue, chips: newChips });
	}

	function handleNavigateUp() {
		if (!phrasebookPath) return;

		const cleanPath = phrasebookPath.endsWith('.')
			? phrasebookPath.slice(0, -1)
			: phrasebookPath;

		if (!cleanPath) return;

		const pathParts = cleanPath.split('.');
		pathParts.pop();
		const parentPath = pathParts.join('.');

		const selection = window.getSelection();
		if (!selection || !selection.rangeCount) return;

		const range = selection.getRangeAt(0);
		if (range.startContainer.nodeType !== Node.TEXT_NODE) return;

		const textNode = range.startContainer as Text;
		const text = textNode.textContent || '';
		const cursorOffset = range.startOffset;

		// Find # trigger
		let triggerStart = cursorOffset - 1;
		while (triggerStart >= 0 && text[triggerStart] !== '#') {
			triggerStart--;
		}

		if (triggerStart < 0) return;

		const newPath = parentPath ? parentPath + '.' : '';
		const newText = text.substring(0, triggerStart + 1) + newPath + text.substring(cursorOffset);

		// Calculate new cursor position before modifying DOM
		const newCursorPos = triggerStart + 1 + newPath.length;

		// Update the text node
		textNode.textContent = newText;

		// Restore cursor position
		tick().then(() => {
			const newSelection = window.getSelection();
			if (newSelection && textNode.parentNode) {
				const newRange = document.createRange();
				const safePos = Math.min(newCursorPos, textNode.textContent?.length || 0);
				newRange.setStart(textNode, safePos);
				newRange.collapse(true);
				newSelection.removeAllRanges();
				newSelection.addRange(newRange);
			}
		});

		phrasebookPath = newPath;
		fetchPhrasebookSuggestions(parentPath);

		// Don't call handleInput - extract and dispatch manually
		const { value: newValue, chips: newChips } = extractContentFromDOM(editorRef, chips);
		dispatch('change', { value: newValue, chips: newChips });
	}

	function closePhrasebook() {
		isPhrasebookOpen = false;
		phrasebookCategories = [];
		phrasebookSuggestions = [];
		phrasebookSelectedIndex = 0;
		phrasebookPath = '';
		phrasebookTriggerNode = null;
		phrasebookTriggerOffset = -1;

		if (debounceTimer) {
			clearTimeout(debounceTimer);
			debounceTimer = null;
		}
	}

	// =====================
	// $variable picker
	// =====================

	function detectVariablePickerTrigger() {
		const selection = window.getSelection();
		if (!selection || !selection.rangeCount || !editorRef) return;

		const range = selection.getRangeAt(0);
		if (range.startContainer.nodeType !== Node.TEXT_NODE) {
			closeVariablePicker();
			return;
		}

		const textNode = range.startContainer as Text;
		const text = textNode.textContent || '';
		const cursorOffset = range.startOffset;

		const match = detectVariableTrigger(text, cursorOffset);
		if (match) {
			closePhrasebook(); // mutually exclusive with the # picker
			variableQuery = match.query;
			variableTriggerNode = textNode;
			variableTriggerOffset = match.start;
			variableSelectedIndex = 0;
			isVariablePickerOpen = true;
			return;
		}

		closeVariablePicker();
	}

	function closeVariablePicker() {
		isVariablePickerOpen = false;
		variableQuery = '';
		variableTriggerNode = null;
		variableTriggerOffset = -1;
		variableSelectedIndex = 0;
	}

	/**
	 * Insert `${name}` at the trigger span. Plain text, not a chip — usages
	 * don't need their own editable widget, just the correct syntax.
	 * Untyped param for the same reason as `variableSuggestions` above.
	 *
	 * Resolve the trigger to an ABSOLUTE offset in the full extracted text by
	 * walking from editorRef (correct even when the $ trigger isn't in the
	 * editor's only/last text node), then rebuild synchronously. The same-tick
	 * DOM rebuild + dispatch keeps the DOM, the emitted value, and the restored
	 * caret in lockstep instead of racing Svelte's own reactivity.
	 */
	function handleSelectVariable(item: { label: string }) {
		if (!editorRef || !variableTriggerNode || variableTriggerOffset < 0) {
			closeVariablePicker();
			return;
		}

		const triggerNode = variableTriggerNode;
		const triggerNodeOffset = variableTriggerOffset;
		const typedLength = 1 + variableQuery.length; // "$" + whatever was typed after it

		let triggerIndex = 0;
		let foundTrigger = false;

		function countCharsUntilTrigger(node: Node): boolean {
			if (foundTrigger) return true;
			if (node === triggerNode) {
				triggerIndex += triggerNodeOffset;
				foundTrigger = true;
				return true;
			}
			if (node.nodeType === Node.TEXT_NODE) {
				triggerIndex += (node.textContent || '').length;
			} else if (node.nodeType === Node.ELEMENT_NODE) {
				const el = node as HTMLElement;
				if (el.dataset.groupRaw !== undefined) {
					triggerIndex += el.dataset.groupRaw.length;
				} else if (el.dataset.chipId && chips[el.dataset.chipId]) {
					triggerIndex += encodePathForText(chips[el.dataset.chipId].categoryPath).length;
				} else if (el.tagName === 'BR') {
					triggerIndex += 1;
				} else {
					for (const child of Array.from(node.childNodes)) {
						if (countCharsUntilTrigger(child)) return true;
					}
				}
			}
			return false;
		}

		for (const child of Array.from(editorRef.childNodes)) {
			if (countCharsUntilTrigger(child)) break;
		}

		closeVariablePicker();
		if (!foundTrigger) return;

		const { value: fullText, chips: existingChips } = extractContentFromDOM(editorRef, chips);
		const usage = variableUsageSyntax(item.label);
		const newValue = fullText.substring(0, triggerIndex) + usage + fullText.substring(triggerIndex + typedLength);
		const newCursorOffset = triggerIndex + usage.length;

		isInternalUpdate = true;
		editorRef.innerHTML = '';
		const segments = parseValueToSegments(newValue, existingChips);
		segments.forEach((segment) => {
			editorRef.appendChild(buildSegmentNode(segment));
		});
		mountChips();
		mountGroups();
		mountVariables();
		isInternalUpdate = false;
		lastSyncedValue = newValue;
		lastGroupCount = countChoiceGroups(newValue);
		lastVariableUsageCount = countVariableUsages(newValue);
		refreshTriggerHighlights(newValue);

		tick().then(() => placeCaretAtCharOffset(editorRef, chips, newCursorOffset));

		dispatch('change', { value: newValue, chips: existingChips });
	}

	// =====================
	// Chip Handlers
	// =====================

	function handleChipChange(chipId: string, updatedData: ChipData) {
		const newChips = { ...chips, [chipId]: updatedData };
		chips = newChips;

		// Remount the specific chip with updated data
		remountChip(chipId, updatedData);

		dispatch('change', { value, chips: newChips });
	}

	function remountChip(chipId: string, chipData: ChipData, animate: boolean = false) {
		if (!editorRef) return;

		const container = editorRef.querySelector(`[data-chip-id="${chipId}"]`);
		if (!container) return;

		// Unmount existing component
		const existingComponent = mountedChipComponents.get(chipId);
		if (existingComponent) {
			unmount(existingComponent);
			mountedChipComponents.delete(chipId);
		}

		// Clear container
		container.innerHTML = '';

		// Get color index based on position
		const allContainers = editorRef.querySelectorAll('.inline-chip-container');
		let colorIndex = 0;
		allContainers.forEach((c, idx) => {
			if ((c as HTMLElement).dataset.chipId === chipId) {
				colorIndex = idx;
			}
		});

		// Mount new component with updated data
		const chipComponent = mount(InlineChip, {
			target: container,
			props: {
				data: chipData,
				colorIndex,
				disabled: isDisabled,
				animate: animate ? 'shuffle' : 'none',
				onchange: (updated: ChipData) => {
					handleChipChange(chipId, updated);
				},
				onremove: () => {
					handleChipRemove(chipId);
				},
				ondeactivate: (data: ChipData) => {
					handleChipDeactivate(chipId, data);
				}
			}
		});

		mountedChipComponents.set(chipId, chipComponent);
	}

	function handleChipRemove(chipId: string) {
		if (!editorRef) return;

		// Find and remove the chip container
		const chipContainer = editorRef.querySelector(`[data-chip-id="${chipId}"]`);
		if (chipContainer) {
			chipContainer.remove();
		}

		// Update chips
		const newChips = { ...chips };
		delete newChips[chipId];

		// Extract content
		const { value: newValue } = extractContentFromDOM(editorRef, chips);

		dispatch('change', { value: newValue, chips: newChips });
	}

	async function handleChipDeactivate(chipId: string, chipData: ChipData) {
		if (!editorRef) return;

		// Deactivate the current value via API
		try {
			const response = await api.toggleValueActive(chipData.valueId, false);
			if (!response.success) {
				logger.error('Failed to deactivate value:', response.error);
				return;
			}

			// Remove the deactivated value from the chip's allValues
			const remainingValues = chipData.allValues.filter(v => v.id !== chipData.valueId);

			if (remainingValues.length === 0) {
				// No values left, remove the chip entirely
				handleChipRemove(chipId);
				return;
			}

			// Pick a random new value from remaining values
			const randomValue = remainingValues[Math.floor(Math.random() * remainingValues.length)];

			// Update chip data with new value and filtered allValues
			const updatedChipData: ChipData = {
				...chipData,
				valueId: randomValue.id,
				label: randomValue.label,
				value: randomValue.value,
				allValues: remainingValues
			};

			// Update the chip
			handleChipChange(chipId, updatedChipData);

		} catch (error) {
			logger.error('Error deactivating value:', error);
		}
	}

	// =====================
	// Choice-group handlers
	// =====================

	/** Re-serialize + remount a group container's chip after an edit in its popover. */
	function handleGroupChange(container: HTMLElement, newRaw: string) {
		if (!editorRef) return;
		container.dataset.groupRaw = newRaw;

		const slotId = container.dataset.slotId;
		if (slotId) {
			const existing = mountedGroupComponents.get(slotId);
			if (existing) {
				unmount(existing);
				mountedGroupComponents.delete(slotId);
			}
		}
		container.innerHTML = '';
		mountGroups();

		const { value: newValue, chips: newChips } = extractContentFromDOM(editorRef, chips);
		lastGroupCount = countChoiceGroups(newValue);
		dispatch('change', { value: newValue, chips: newChips });
	}

	function handleGroupRemove(container: HTMLElement) {
		if (!editorRef) return;
		const slotId = container.dataset.slotId;
		if (slotId) {
			const existing = mountedGroupComponents.get(slotId);
			if (existing) {
				unmount(existing);
				mountedGroupComponents.delete(slotId);
			}
		}
		container.remove();

		const { value: newValue, chips: newChips } = extractContentFromDOM(editorRef, chips);
		lastGroupCount = countChoiceGroups(newValue);
		dispatch('change', { value: newValue, chips: newChips });
	}

	function mountGroups() {
		if (!editorRef) return;

		const currentSlotIds = new Set<string>();
		const containers = editorRef.querySelectorAll('.choice-group-container');

		containers.forEach((container, index) => {
			const el = container as HTMLElement;
			if (!el.dataset.slotId) el.dataset.slotId = `grp-${groupSlotCounter++}`;
			const slotId = el.dataset.slotId;
			currentSlotIds.add(slotId);

			if (container.querySelector('.choice-group-chip')) return; // already mounted

			const raw = el.dataset.groupRaw || '';
			const component = mount(ChoiceGroupChip, {
				target: container,
				props: {
					raw,
					colorIndex: index,
					disabled: isDisabled,
					onchange: (newRaw: string) => handleGroupChange(el, newRaw),
					onremove: () => handleGroupRemove(el)
				}
			});
			mountedGroupComponents.set(slotId, component);
		});

		for (const [slotId, component] of mountedGroupComponents) {
			if (!currentSlotIds.has(slotId)) {
				unmount(component);
				mountedGroupComponents.delete(slotId);
			}
		}
	}

	// =====================
	// Variable-usage handlers
	// =====================

	/** The chip's mode control (shuffle/pin/per-image) edits the shared
	 *  DEFINITION (see VariableUsageChip's header comment) — bubble it up;
	 *  the definitions themselves live several components above (tabsStore
	 *  via PromptSection.svelte), not in this editor. */
	function handleVariableModeChange(name: string, mode: ChoiceVariableMode, pinnedIndex: number | null) {
		const def = normalizeVariableDef(variables[name]);
		if (def.type !== 'choice') return;
		onVariableDefChange?.(name, { ...def, mode, pinnedIndex });
	}

	/** "Create this variable" on an undefined usage's chip: define it as an
	 *  empty text variable and open the manager so the user can fill it in
	 *  (mirrors how "Add variable" in the manager itself defaults to text). */
	function handleVariableCreate(name: string) {
		onVariableDefChange?.(name, createTextVariable());
		onOpenVariableManager?.();
	}

	function handleVariableUsageRemove(container: HTMLElement) {
		if (!editorRef) return;
		const slotId = container.dataset.slotId;
		if (slotId) {
			const existing = mountedVariableComponents.get(slotId);
			if (existing) {
				unmount(existing);
				mountedVariableComponents.delete(slotId);
			}
		}
		container.remove();

		const { value: newValue, chips: newChips } = extractContentFromDOM(editorRef, chips);
		lastVariableUsageCount = countVariableUsages(newValue);
		dispatch('change', { value: newValue, chips: newChips });
	}

	function mountVariables() {
		if (!editorRef) return;

		const currentSlotIds = new Set<string>();
		const containers = editorRef.querySelectorAll('.variable-usage-container');

		containers.forEach((container) => {
			const el = container as HTMLElement;
			if (!el.dataset.slotId) el.dataset.slotId = `var-${variableSlotCounter++}`;
			const slotId = el.dataset.slotId;
			currentSlotIds.add(slotId);

			if (container.querySelector('.variable-usage-chip')) return; // already mounted

			const name = el.dataset.variableName || '';
			const component = mount(VariableUsageChip, {
				target: container,
				props: {
					name,
					definition: name in variables ? variables[name] : undefined,
					roll: variableRolls[name],
					disabled: isDisabled,
					onModeChange: (mode: ChoiceVariableMode, pinnedIndex: number | null) =>
						handleVariableModeChange(name, mode, pinnedIndex),
					onCreate: () => handleVariableCreate(name),
					onOpenManager: () => onOpenVariableManager?.(),
					onRemove: () => handleVariableUsageRemove(el)
				}
			});
			mountedVariableComponents.set(slotId, component);
		});

		for (const [slotId, component] of mountedVariableComponents) {
			if (!currentSlotIds.has(slotId)) {
				unmount(component);
				mountedVariableComponents.delete(slotId);
			}
		}
	}

	/** Force every mounted variable-usage chip to re-read `variables` — used
	 *  when the map changes externally (e.g. a pin made from THIS chip's own
	 *  popover round-trips through tabsStore and comes back down as a new
	 *  `variables` prop, or the Variable Manager modal is edited directly).
	 *  `mount()` props aren't reactive, so a stale chip would otherwise keep
	 *  showing the definition it had at mount time. */
	function remountAllVariableChips() {
		if (!editorRef) return;
		const containers = editorRef.querySelectorAll('.variable-usage-container');
		containers.forEach((container) => {
			const el = container as HTMLElement;
			const slotId = el.dataset.slotId;
			if (slotId) {
				const existing = mountedVariableComponents.get(slotId);
				if (existing) {
					unmount(existing);
					mountedVariableComponents.delete(slotId);
				}
			}
			el.innerHTML = '';
		});
		mountVariables();
	}

	// =====================
	// Turning a just-closed {a|b|c} into a chip mid-typing
	// =====================
	// getCaretCharOffset / placeCaretAtCharOffset now live in chipEditorCaret.ts.

	/** Full DOM rebuild that turns a newly-completed `{a|b|c}` or `${name}` (still plain text) into a mounted chip, preserving the caret position. */
	function chipifyNewTokens(newValue: string, newChips: Record<string, ChipData>) {
		if (!editorRef) return;

		const caretOffset = getCaretCharOffset(editorRef, chips);

		isInternalUpdate = true;
		editorRef.innerHTML = '';
		const segments = parseValueToSegments(newValue, newChips);
		segments.forEach((segment) => {
			editorRef.appendChild(buildSegmentNode(segment));
		});
		mountChips();
		mountGroups();
		mountVariables();
		isInternalUpdate = false;
		lastSyncedValue = newValue;
		refreshTriggerHighlights(newValue);

		if (caretOffset !== null) {
			tick().then(() => placeCaretAtCharOffset(editorRef, chips, caretOffset));
		}
	}

	// =====================
	// Sync DOM with props
	// =====================

	function syncDOMWithValue() {
		if (!editorRef || isInternalUpdate) return;

		// Only sync if the DOM content doesn't match the value
		const { value: domValue } = extractContentFromDOM(editorRef, chips);
		if (domValue === value) return;

		isInternalUpdate = true;

		// Save selection
		const selection = window.getSelection();
		const savedRange = selection?.rangeCount ? selection.getRangeAt(0).cloneRange() : null;

		// Rebuild DOM
		editorRef.innerHTML = '';

		contentSegments.forEach((segment) => {
			editorRef.appendChild(buildSegmentNode(segment));
		});

		lastGroupCount = countChoiceGroups(value);
		lastVariableUsageCount = countVariableUsages(value);

		// Mount Svelte chips + groups + variable usages
		tick().then(() => {
			mountChips();
			mountGroups();
			mountVariables();
			isInternalUpdate = false;
			refreshTriggerHighlights(value);
		});
	}

	function mountChips() {
		if (!editorRef) return;

		// Clean up any previously mounted components that are no longer in DOM
		const currentChipIds = new Set<string>();
		const containers = editorRef.querySelectorAll('.inline-chip-container');

		containers.forEach((container, index) => {
			const chipId = (container as HTMLElement).dataset.chipId;
			if (!chipId || !chips[chipId]) return;

			currentChipIds.add(chipId);

			// Check if chip is already mounted
			if (container.querySelector('.inline-chip')) return;

			const chipData = chips[chipId];

			// Create chip component using Svelte 5 mount()
			const chipComponent = mount(InlineChip, {
				target: container,
				props: {
					data: chipData,
					colorIndex: index,
					disabled: isDisabled,
					onchange: (updatedData: ChipData) => {
						handleChipChange(chipId, updatedData);
					},
					onremove: () => {
						handleChipRemove(chipId);
					},
					ondeactivate: (data: ChipData) => {
						handleChipDeactivate(chipId, data);
					}
				}
			});

			mountedChipComponents.set(chipId, chipComponent);
		});

		// Cleanup components that are no longer in DOM
		for (const [chipId, component] of mountedChipComponents) {
			if (!currentChipIds.has(chipId)) {
				unmount(component);
				mountedChipComponents.delete(chipId);
			}
		}
	}

	onMount(() => {
		// Force initial render
		if (editorRef) {
			isInternalUpdate = true;
			editorRef.innerHTML = '';

			contentSegments.forEach((segment) => {
				editorRef.appendChild(buildSegmentNode(segment));
			});

			lastGroupCount = countChoiceGroups(value);
			lastVariableUsageCount = countVariableUsages(value);

			mountChips();
			mountGroups();
			mountVariables();
			isInternalUpdate = false;
			refreshTriggerHighlights(value);
		}
	});

	onDestroy(() => {
		// Clean up all mounted chip components
		for (const [chipId, component] of mountedChipComponents) {
			unmount(component);
		}
		mountedChipComponents.clear();
		for (const [slotId, component] of mountedGroupComponents) {
			unmount(component);
		}
		mountedGroupComponents.clear();
		for (const [slotId, component] of mountedVariableComponents) {
			unmount(component);
		}
		mountedVariableComponents.clear();
		clearOwnerTriggerHighlightRanges(triggerHighlightOwner);
	});

	// React to the active-trigger-word list changing (LoRA added/removed/edited)
	// independently of the text itself.
	$: if (editorRef && activeTriggerWords) refreshTriggerHighlights(value);

	// React to external value/chips changes (not from our own edits)
	let lastSyncedValue = '';
	$: if (editorRef && value !== undefined && value !== lastSyncedValue && !isInternalUpdate) {
		lastSyncedValue = value;
		tick().then(syncDOMWithValue);
	}

	// React to external chip data changes (e.g., shuffle on generation)
	// Track previous chip valueIds to detect which chips actually changed
	let previousChipValues: Record<string, string> = {};

	let lastChipsHash = '';
	$: {
		const currentHash = getChipsHash(chips);
		if (editorRef && lastChipsHash && currentHash !== lastChipsHash && !isInternalUpdate) {
			const currentValues = getChipValueMap(chips);
			const prevValues = previousChipValues;

			// Update for next comparison
			lastChipsHash = currentHash;
			previousChipValues = currentValues;

			// Remount only chips that actually changed their valueId
			tick().then(() => {
				if (editorRef) {
					for (const [chipId, chipData] of Object.entries(chips)) {
						// Only animate if this specific chip's value changed
						const valueChanged = prevValues[chipId] !== undefined && prevValues[chipId] !== chipData.valueId;
						remountChip(chipId, chipData, valueChanged);
					}
				}
			});
		} else if (!lastChipsHash) {
			// Initialize on first run
			lastChipsHash = currentHash;
			previousChipValues = getChipValueMap(chips);
		}
	}

	// React to external `variables` changes (e.g. a pin/unpin made from one
	// usage chip's own popover, which round-trips through tabsStore and comes
	// back down here as a new prop, or a session load defining a variable
	// that a usage chip was showing as undefined — see variableDefs.ts
	// `stepVariablesHash`'s doc comment for the sentinel bug this fixes) —
	// every chip needs to refresh, not just the one that was clicked.
	let lastVariablesHash: string | null = null;
	$: {
		const currentVariablesHash = hashVariablesMap(variables);
		if (editorRef && !isInternalUpdate) {
			const step = stepVariablesHash(currentVariablesHash, lastVariablesHash);
			lastVariablesHash = step.nextHash;
			if (step.shouldRemount) {
				tick().then(remountAllVariableChips);
			}
		}
	}

	// Same idea, for `variableRolls`: a Generate click rolls a
	// fresh value for a shuffle-mode choice variable and persists it onto the
	// tab — every chip for that name needs to pick up the new roll, not just
	// the segment that happened to also change. Reuses the same `null`-sentinel
	// step function; the hash itself doesn't need to be variables-specific.
	let lastVariableRollsHash: string | null = null;
	$: {
		const currentRollsHash = hashVariableRolls(variableRolls);
		if (editorRef && !isInternalUpdate) {
			const step = stepVariablesHash(currentRollsHash, lastVariableRollsHash);
			lastVariableRollsHash = step.nextHash;
			if (step.shouldRemount) {
				tick().then(remountAllVariableChips);
			}
		}
	}
</script>

<div class="relative {flow ? 'inline' : 'w-full'}" bind:this={containerRef}>
	<!-- Contenteditable Editor -->
	<div
		bind:this={editorRef}
		contenteditable={!isDisabled}
		dir="ltr"
		role="textbox"
		tabindex="0"
		aria-multiline="true"
		aria-placeholder={placeholder}
		on:input={handleInput}
		on:keydown={handleKeyDown}
		on:paste={handlePaste}
		class="inline-chip-editor {flow
			? 'inline'
			: `w-full ${borderless ? 'min-h-[2.5rem]' : density === 'compact' ? 'min-h-[4rem]' : 'min-h-[3rem]'}`}
			{flow ? '' : borderless ? (density === 'compact' ? 'px-2.5 py-2 bg-transparent' : 'px-3 py-2 bg-transparent') : 'px-3 py-2 border rounded-lg'}
			{segmentDisabled
				? 'text-fg-disabled' + (flow || borderless ? '' : ' bg-surface-2 border-line')
				: (flow || borderless ? 'text-fg' : 'bg-surface-1 border-line-strong hover:border-line-hover focus-within:border-signal text-fg')}
			{isRegenerating ? 'opacity-60' : ''}
			focus:outline-none transition-colors duration-100 text-sm leading-relaxed"
		style="white-space: pre-wrap; word-break: break-word;"
	></div>

	<!-- Placeholder (shown when empty) — Flow has no room for a ghost overlay
		 on a zero-width inline run, so it's skipped there. -->
	{#if !value && !isDisabled && !flow}
		<div
			class="absolute top-2 {density === 'compact' ? 'left-2.5' : 'left-3'} text-fg-subtle text-sm pointer-events-none"
			aria-hidden="true"
		>
			{placeholder}
		</div>
	{/if}

	<!-- Phrasebook Dropdown -->
	{#if isPhrasebookOpen}
		<AutocompleteDropdown
			categories={phrasebookCategories}
			suggestions={phrasebookSuggestions}
			selectedIndex={phrasebookSelectedIndex}
			onSelectCategory={handleSelectCategory}
			onSelectValue={handleSelectValue}
			isLoading={phrasebookLoading}
			currentPath={phrasebookPath}
			onClose={closePhrasebook}
			onNavigateUp={handleNavigateUp}
			parentRef={containerRef}
			getImageUrl={(fileId) => api.getFileURL(fileId, 'small')}
			contextLabel="Phrasebook"
		/>
	{/if}

	<!-- $variable Picker -->
	{#if isVariablePickerOpen}
		<AutocompleteDropdown
			categories={[]}
			suggestions={variableSuggestions}
			selectedIndex={variableSelectedIndex}
			onSelectCategory={() => {}}
			onSelectValue={handleSelectVariable}
			isLoading={false}
			currentPath={variableQuery}
			triggerChar="$"
			emptyHint="Variables — type $ + name, or open Variables above to add one"
			onClose={closeVariablePicker}
			parentRef={containerRef}
			contextLabel="Variables"
		/>
	{/if}
</div>

<style>
	.inline-chip-editor {
		font-family: inherit;
		font-size: 0.875rem;
		line-height: 1.625;
		position: relative;
	}

	.inline-chip-editor:focus {
		outline: none;
	}

	/* Chip containers */
	.inline-chip-editor :global(.inline-chip-container) {
		display: inline;
		user-select: none;
	}

	.inline-chip-editor :global(.inline-chip) {
		display: inline-flex;
		vertical-align: baseline;
		white-space: nowrap;
	}

	/* Choice-group containers */
	.inline-chip-editor :global(.choice-group-container) {
		display: inline;
		user-select: none;
	}

	.inline-chip-editor :global(.choice-group-chip) {
		display: inline-flex;
		vertical-align: baseline;
		white-space: nowrap;
	}

	/* Variable-usage containers */
	.inline-chip-editor :global(.variable-usage-container) {
		display: inline;
		user-select: none;
	}

	.inline-chip-editor :global(.variable-usage-chip) {
		display: inline-flex;
		vertical-align: baseline;
		white-space: nowrap;
	}

	/* Active LoRA trigger-word occurrences (see triggerWordHighlight.ts). The
	   CSS Custom Highlight API only accepts a handful of text-styling
	   properties — no font-weight, no box model — so this can never reflow or
	   shift the editor's text metrics. Document-scoped by nature (::highlight
	   isn't tied to an element), so this can't use Svelte's normal scoping. */
	:global(::highlight(potionui-trigger-word)) {
		background-color: rgb(var(--signal) / 0.22);
		color: rgb(var(--signal));
	}
</style>
