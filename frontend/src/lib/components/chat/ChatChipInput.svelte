<script lang="ts">
	// Chat input with inline @resource chips.
	//
	// Fork of InlineChipEditor.svelte with the '#' phrasebook-taxonomy trigger
	// replaced by an '@' resource trigger backed by /api/chat/resources/suggest.
	// The contenteditable mechanics (caret walk-back trigger detection, debounced
	// fetch + cache, keyboard nav, dot-navigation, backspace-deletes-chip,
	// DOM<->value sync) are kept from the original; the ChipData value-cycling
	// machinery is dropped — a resource chip is just a label with a remove button.
	import { logger } from '$lib/utils/logger';
	import { createEventDispatcher, onMount, onDestroy, tick, mount, unmount } from 'svelte';
	import { api, type PhrasebookValue } from '$lib/services/api/index';
	import type { ResourceChipData } from '$lib/types/chat';
	import { encodeResourceToken } from '$lib/utils/resourceTokens';
	import { resolveMentionRowAction } from '$lib/utils/mentionRowAction';
	import AutocompleteDropdown from '$lib/components/AutocompleteDropdown.svelte';
	import ResourceChip from './ResourceChip.svelte';
	import { parseValueToSegments } from './resourceChipSegments';
	import {
		type MentionCategory,
		mapSuggestions,
		buildFormSuggestions
	} from './chatResourceSuggestions';

	// Track mounted chip components for cleanup
	let mountedChipComponents: Map<string, ReturnType<typeof mount>> = new Map();

	// Props
	export let value: string;
	export let resources: Record<string, ResourceChipData> = {};
	export let mode: string = '';
	// Live values of the generate form the chat sits next to. Powers @form.<field>
	// autocomplete client-side (the suggest endpoint has no form state); the
	// values themselves are resolved server-side from the send-time snapshot.
	export let formData: Record<string, any> = {};
	// Selected LoRAs per lora_picker field (published by the mounted field
	// components, so this is keyed by field TYPE, not name). Non-empty entries
	// make their field browsable in the @form dropdown: descending lists one
	// row per selected LoRA; picking one attaches form.<field>.<model_id>.
	export let loraSelections: Record<string, { id: string | null; name: string; strength: number }[]> = {};
	export let placeholder: string = 'Ask the AI anything... (@ to attach a resource)';
	export let disabled: boolean = false;

	const dispatch = createEventDispatcher();

	// DOM references
	let editorRef: HTMLDivElement;
	let containerRef: HTMLDivElement;

	// Suggest state (variable names kept from the fork source for easy diffing)
	let isAutocompleteOpen = false;
	let autocompleteCategories: MentionCategory[] = [];
	let autocompleteSuggestions: PhrasebookValue[] = [];
	let autocompleteSelectedIndex = 0;
	let autocompleteLoading = false;
	let autocompletePath = '';
	let autocompleteTriggerNode: Text | null = null;
	let autocompleteTriggerOffset = -1; // Position of @ in that text node
	let debounceTimer: ReturnType<typeof setTimeout> | null = null;
	let autocompleteCache = new Map<string, any>();

	// Track if we're programmatically updating
	let isInternalUpdate = false;

	export function focus() {
		editorRef?.focus();
	}

	// Parse value into segments (text and chips) — see resourceChipSegments.ts.
	$: contentSegments = parseValueToSegments(value, resources);

	// =====================
	// Extract content from DOM
	// =====================

	function extractContentFromDOM(): {
		value: string;
		resources: Record<string, ResourceChipData>;
	} {
		if (!editorRef) return { value: '', resources: {} };

		let textContent = '';
		const extractedResources: Record<string, ResourceChipData> = {};

		function walkNode(node: Node) {
			if (node.nodeType === Node.TEXT_NODE) {
				textContent += node.textContent || '';
			} else if (node.nodeType === Node.ELEMENT_NODE) {
				const el = node as HTMLElement;

				// Check for chip container
				if (el.dataset.chipId) {
					const chipId = el.dataset.chipId;
					if (resources[chipId]) {
						textContent += encodeResourceToken(resources[chipId].uri);
						extractedResources[chipId] = resources[chipId];
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

		return { value: textContent, resources: extractedResources };
	}

	// =====================
	// Input Handling
	// =====================

	function handleInput() {
		if (isInternalUpdate) return;

		const { value: newValue, resources: newResources } = extractContentFromDOM();

		// Detect @ trigger
		detectAutocompleteTrigger();

		// Emit change
		dispatch('change', { value: newValue, resources: newResources });
	}

	function handleKeyDown(e: KeyboardEvent) {
		// Handle suggest-dropdown navigation
		if (isAutocompleteOpen) {
			const totalItems = autocompleteCategories.length + autocompleteSuggestions.length;

			if (e.key === 'ArrowDown') {
				e.preventDefault();
				autocompleteSelectedIndex = (autocompleteSelectedIndex + 1) % totalItems;
				return;
			} else if (e.key === 'ArrowUp') {
				e.preventDefault();
				autocompleteSelectedIndex =
					autocompleteSelectedIndex === 0 ? totalItems - 1 : autocompleteSelectedIndex - 1;
				return;
			} else if (e.key === 'Enter' && !e.ctrlKey) {
				e.preventDefault();
				selectAutocompleteItem();
				return;
			} else if (e.key === 'Escape') {
				e.preventDefault();
				closeAutocomplete();
				return;
			}
		}

		// Chat semantics: Enter sends, Shift+Enter inserts a newline
		if (e.key === 'Enter' && !e.shiftKey) {
			e.preventDefault();
			dispatch('submit');
			return;
		}

		// Handle backspace near chips
		if (e.key === 'Backspace') {
			const selection = window.getSelection();
			if (selection && selection.rangeCount > 0) {
				const range = selection.getRangeAt(0);

				if (range.collapsed) {
					// Check if cursor is at start of a text node after a chip
					const node = range.startContainer;
					const offset = range.startOffset;

					if (node.nodeType === Node.TEXT_NODE && offset === 0) {
						// Look for previous sibling chip
						const prevEl = node.previousSibling as HTMLElement;
						if (prevEl && prevEl.dataset?.chipId) {
							e.preventDefault();
							handleChipRemove(prevEl.dataset.chipId);
							return;
						}
					}

					// Check if we're in the editor directly
					if (node === editorRef && offset > 0) {
						const prevChild = editorRef.childNodes[offset - 1] as HTMLElement;
						if (prevChild && prevChild.dataset?.chipId) {
							e.preventDefault();
							handleChipRemove(prevChild.dataset.chipId);
							return;
						}
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
	// Resource suggest
	// =====================

	function detectAutocompleteTrigger() {
		const selection = window.getSelection();
		if (!selection || !selection.rangeCount || !editorRef) return;

		const range = selection.getRangeAt(0);

		// Only detect in text nodes
		if (range.startContainer.nodeType !== Node.TEXT_NODE) {
			closeAutocomplete();
			return;
		}

		const textNode = range.startContainer as Text;
		const text = textNode.textContent || '';
		const cursorOffset = range.startOffset;

		// Look backwards for @ trigger (stop at newline or another @)
		let start = cursorOffset - 1;

		while (start >= 0 && text[start] !== '@' && text[start] !== '\n') {
			start--;
		}

		// Check if we found an @ trigger at a word boundary (not user@host.com)
		if (start >= 0 && text[start] === '@') {
			const boundaryOk = start === 0 || !/[\w@]/.test(text[start - 1]);
			const path = text.substring(start + 1, cursorOffset);

			// Validate path format - word chars, dots, dashes, and spaces
			if (boundaryOk && (/^[\w][\w.\s-]*$/.test(path) || /^[\w]*$/.test(path))) {
				autocompletePath = path;
				autocompleteTriggerNode = textNode;
				autocompleteTriggerOffset = start;
				isAutocompleteOpen = true;

				// Debounce API call
				if (debounceTimer) clearTimeout(debounceTimer);

				debounceTimer = setTimeout(() => {
					fetchResourceSuggestions(path);
				}, 200);
				return;
			}
		}

		closeAutocomplete();
	}

	// The @form namespace lists the live form's fields, which the suggest
	// endpoint can't see. Serve those completions locally from `formData`; every
	// other namespace goes to the backend. Resolution stays server-side (the
	// backend reads the same form snapshot the message ships). See
	// chatResourceSuggestions.ts for mapSuggestions/buildFormSuggestions/etc.

	function applyLocalFormSuggestions(partial: string) {
		const mapped = mapSuggestions(buildFormSuggestions(partial, formData, loraSelections));
		autocompleteCategories = mapped.child_categories;
		autocompleteSuggestions = mapped.values;
		autocompleteSelectedIndex = 0;
		autocompleteLoading = false;
	}

	async function fetchResourceSuggestions(path: string) {
		// Navigated into @form.<...>: serve field completions from live form state.
		if (path === 'form.' || path.startsWith('form.')) {
			const partial = path.slice('form.'.length);
			if (debounceTimer) {
				clearTimeout(debounceTimer);
				debounceTimer = null;
			}
			applyLocalFormSuggestions(partial);
			return;
		}

		const cacheKey = `${mode}|${path}`;

		// Check cache first
		if (autocompleteCache.has(cacheKey)) {
			const cached = autocompleteCache.get(cacheKey);
			autocompleteCategories = cached.child_categories || [];
			autocompleteSuggestions = cached.values || [];
			autocompleteSelectedIndex = 0;
			autocompleteLoading = false;
			return;
		}

		autocompleteLoading = true;

		try {
			const response = await api.suggestChatResources(path, mode);
			if (response.success && response.data) {
				const mapped = mapSuggestions(response.data.suggestions || []);
				autocompleteCache.set(cacheKey, mapped);
				if (autocompleteCache.size > 50) {
					const firstKey = autocompleteCache.keys().next().value;
					if (firstKey) autocompleteCache.delete(firstKey);
				}
				autocompleteCategories = mapped.child_categories;
				autocompleteSuggestions = mapped.values;
				autocompleteSelectedIndex = 0;
			}
		} catch (error) {
			logger.error('Failed to fetch resource suggestions:', error);
			autocompleteCategories = [];
			autocompleteSuggestions = [];
		} finally {
			autocompleteLoading = false;
		}
	}

	function selectAutocompleteItem() {
		const totalCategories = autocompleteCategories.length;

		if (autocompleteSelectedIndex < totalCategories) {
			const category = autocompleteCategories[autocompleteSelectedIndex];
			const action = resolveMentionRowAction({ hasChildren: true, attachable: category.attachable });
			if (action === 'attach-category') {
				handleAttachCategory(category);
			} else {
				handleSelectCategory(category);
			}
		} else {
			const valueIndex = autocompleteSelectedIndex - totalCategories;
			handleSelectValue(autocompleteSuggestions[valueIndex]);
		}
	}

	// Structural param types (path/name, value/label are all that's used) so the
	// handlers satisfy AutocompleteDropdown's locally-declared prop interfaces.
	function handleSelectCategory(category: { path?: string; name?: string }) {
		const selection = window.getSelection();
		if (!selection || !selection.rangeCount) return;

		const range = selection.getRangeAt(0);
		if (range.startContainer.nodeType !== Node.TEXT_NODE) return;

		const textNode = range.startContainer as Text;
		const text = textNode.textContent || '';
		const cursorOffset = range.startOffset;

		// Find the @ trigger position
		let triggerStart = cursorOffset - 1;
		while (triggerStart >= 0 && text[triggerStart] !== '@') {
			triggerStart--;
		}

		if (triggerStart < 0) return;

		// Build the full path for navigation
		const categoryName = category.path || category.name || '';
		if (!categoryName) return;

		let fullPath: string;

		// Navigating INTO a namespace (path ends with '.') vs selecting a
		// top-level match for a partial query
		const isInsideCategory = autocompletePath.endsWith('.');

		if (isInsideCategory) {
			const parentPath = autocompletePath.slice(0, -1);

			if (categoryName.startsWith(parentPath + '.')) {
				fullPath = categoryName;
			} else if (categoryName.includes('.')) {
				fullPath = categoryName;
			} else {
				fullPath = parentPath + '.' + categoryName;
			}
		} else {
			fullPath = categoryName;
		}

		// Navigate into namespace by appending a dot
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
				const safePos = Math.min(newCursorPos, textNode.textContent?.length || 0);
				newRange.setStart(textNode, safePos);
				newRange.collapse(true);
				newSelection.removeAllRanges();
				newSelection.addRange(newRange);
			}
		});

		// Update suggest state
		autocompletePath = newPath;
		fetchResourceSuggestions(newPath);

		// Don't call handleInput here - it can trigger DOM sync and lose cursor
		const { value: newValue, resources: newResources } = extractContentFromDOM();
		dispatch('change', { value: newValue, resources: newResources });
	}

	function handleSelectValue(valueItem: { value: string; label: string }) {
		attachResource(valueItem.value, valueItem.label);
	}

	// A category the provider marked `attachable` (resolvable at its own
	// path, e.g. an autocomplete category) is attached the same way a leaf
	// value is — the row itself attaches; browsing into it happens via the
	// dropdown's separate chevron button (see AutocompleteDropdown).
	function handleAttachCategory(category: { path?: string; name?: string }) {
		const uri = category.path || category.name || '';
		if (!uri) return;
		attachResource(uri, category.name || uri.split('.').pop() || uri);
	}

	function attachResource(uri: string, label: string) {
		if (!editorRef || !autocompleteTriggerNode || autocompleteTriggerOffset < 0) return;

		// Calculate the trigger index in the full extracted text
		let triggerIndex = 0;
		let foundTrigger = false;

		function countCharsUntilTrigger(node: Node): boolean {
			if (foundTrigger) return true;

			if (node === autocompleteTriggerNode) {
				triggerIndex += autocompleteTriggerOffset;
				foundTrigger = true;
				return true;
			}

			if (node.nodeType === Node.TEXT_NODE) {
				triggerIndex += (node.textContent || '').length;
			} else if (node.nodeType === Node.ELEMENT_NODE) {
				const el = node as HTMLElement;
				if (el.dataset.chipId && resources[el.dataset.chipId]) {
					// Chip contributes its encoded token length
					triggerIndex += encodeResourceToken(resources[el.dataset.chipId].uri).length;
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
		const { value: fullText, resources: existingResources } = extractContentFromDOM();

		// Create new resource chip
		const chipId = `res-${Date.now()}-${Math.random().toString(36).substring(2, 11)}`;

		const chipData: ResourceChipData = {
			uri,
			label: label || uri.split('.').pop() || uri
		};

		// Replace @typedPath with the encoded token
		const beforeTrigger = fullText.substring(0, triggerIndex);
		const typedPathLength = autocompletePath.length;
		const afterTrigger = fullText.substring(triggerIndex + 1 + typedPathLength);
		const marker = encodeResourceToken(uri);
		const newValue = beforeTrigger + marker + afterTrigger;

		// Merge existing resources with new chip
		const newResources = { ...existingResources, [chipId]: chipData };
		resources = newResources;

		closeAutocomplete();

		// Rebuild the entire editor content with the new chip
		isInternalUpdate = true;
		unmountAllChips();
		editorRef.innerHTML = '';

		const newSegments = parseValueToSegments(newValue, newResources);
		newSegments.forEach((segment) => {
			if (segment.type === 'text') {
				const textNode = document.createTextNode(segment.content);
				editorRef.appendChild(textNode);
			} else if (segment.type === 'chip' && segment.chipId && segment.chipData) {
				const chipContainer = document.createElement('span');
				chipContainer.dataset.chipId = segment.chipId;
				chipContainer.contentEditable = 'false';
				chipContainer.className = 'inline-chip-container';
				chipContainer.style.cssText = 'user-select: none; display: inline;';
				editorRef.appendChild(chipContainer);
			}
		});

		// Mount all chips
		mountChips();
		isInternalUpdate = false;

		// Position cursor right after the newly inserted chip
		tick().then(() => {
			const selection = window.getSelection();
			if (selection && editorRef) {
				const chipContainer = editorRef.querySelector(`[data-chip-id="${chipId}"]`);
				if (chipContainer) {
					const range = document.createRange();
					range.setStartAfter(chipContainer);
					range.collapse(true);
					selection.removeAllRanges();
					selection.addRange(range);
				} else {
					const range = document.createRange();
					range.selectNodeContents(editorRef);
					range.collapse(false);
					selection.removeAllRanges();
					selection.addRange(range);
				}
			}
		});

		// Emit change to parent
		dispatch('change', { value: newValue, resources: newResources });
	}

	function handleNavigateUp() {
		if (!autocompletePath) return;

		const cleanPath = autocompletePath.endsWith('.')
			? autocompletePath.slice(0, -1)
			: autocompletePath;

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

		// Find @ trigger
		let triggerStart = cursorOffset - 1;
		while (triggerStart >= 0 && text[triggerStart] !== '@') {
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

		autocompletePath = newPath;
		fetchResourceSuggestions(newPath);

		// Don't call handleInput - extract and dispatch manually
		const { value: newValue, resources: newResources } = extractContentFromDOM();
		dispatch('change', { value: newValue, resources: newResources });
	}

	function closeAutocomplete() {
		isAutocompleteOpen = false;
		autocompleteCategories = [];
		autocompleteSuggestions = [];
		autocompleteSelectedIndex = 0;
		autocompletePath = '';
		autocompleteTriggerNode = null;
		autocompleteTriggerOffset = -1;

		if (debounceTimer) {
			clearTimeout(debounceTimer);
			debounceTimer = null;
		}
	}

	// Every DOM rebuild below (`editorRef.innerHTML = ''`) destroys chip host
	// nodes directly; removing a node does not run its mounted component's
	// teardown, so callers must unmount everything first or the instances leak.
	function unmountAllChips() {
		for (const [, component] of mountedChipComponents) {
			unmount(component);
		}
		mountedChipComponents.clear();
	}

	// =====================
	// Chip Handlers
	// =====================

	function handleChipRemove(chipId: string) {
		if (!editorRef) return;

		// Find and remove the chip container
		const chipContainer = editorRef.querySelector(`[data-chip-id="${chipId}"]`);
		if (chipContainer) {
			const component = mountedChipComponents.get(chipId);
			if (component) {
				unmount(component);
				mountedChipComponents.delete(chipId);
			}
			chipContainer.remove();
		}

		// Update resources
		const newResources = { ...resources };
		delete newResources[chipId];
		resources = newResources;

		// Extract content
		const { value: newValue } = extractContentFromDOM();

		dispatch('change', { value: newValue, resources: newResources });
	}

	// =====================
	// Sync DOM with props
	// =====================

	function syncDOMWithValue() {
		if (!editorRef || isInternalUpdate) return;

		// Only sync if the DOM content doesn't match the value
		const { value: domValue } = extractContentFromDOM();
		if (domValue === value) return;

		isInternalUpdate = true;

		// Rebuild DOM
		unmountAllChips();
		editorRef.innerHTML = '';

		contentSegments.forEach((segment) => {
			if (segment.type === 'text') {
				const textNode = document.createTextNode(segment.content);
				editorRef.appendChild(textNode);
			} else if (segment.type === 'chip' && segment.chipId && segment.chipData) {
				const chipContainer = document.createElement('span');
				chipContainer.dataset.chipId = segment.chipId;
				chipContainer.contentEditable = 'false';
				chipContainer.className = 'inline-chip-container';
				chipContainer.style.cssText = 'user-select: none; display: inline;';
				editorRef.appendChild(chipContainer);
			}
		});

		// Mount Svelte chips
		tick().then(() => {
			mountChips();
			isInternalUpdate = false;
		});
	}

	function mountChips() {
		if (!editorRef) return;

		const currentChipIds = new Set<string>();
		const containers = editorRef.querySelectorAll('.inline-chip-container');

		containers.forEach((container) => {
			const chipId = (container as HTMLElement).dataset.chipId;
			if (!chipId || !resources[chipId]) return;

			currentChipIds.add(chipId);

			// Check if chip is already mounted
			if (container.querySelector('.inline-chip')) return;

			const chipData = resources[chipId];

			const chipComponent = mount(ResourceChip, {
				target: container,
				props: {
					uri: chipData.uri,
					label: chipData.label,
					disabled,
					onremove: () => {
						handleChipRemove(chipId);
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
				if (segment.type === 'text') {
					const textNode = document.createTextNode(segment.content);
					editorRef.appendChild(textNode);
				} else if (segment.type === 'chip' && segment.chipId && segment.chipData) {
					const chipContainer = document.createElement('span');
					chipContainer.dataset.chipId = segment.chipId;
					chipContainer.contentEditable = 'false';
					chipContainer.className = 'inline-chip-container';
					chipContainer.style.cssText = 'user-select: none; display: inline;';
					editorRef.appendChild(chipContainer);
				}
			});

			mountChips();
			isInternalUpdate = false;
		}
	});

	onDestroy(unmountAllChips);

	// React to external value changes (not from our own edits)
	let lastSyncedValue = '';
	$: if (editorRef && value !== undefined && value !== lastSyncedValue && !isInternalUpdate) {
		lastSyncedValue = value;
		tick().then(syncDOMWithValue);
	}
</script>

<div class="relative w-full" bind:this={containerRef}>
	<!-- Contenteditable Editor -->
	<div
		bind:this={editorRef}
		contenteditable={!disabled}
		dir="ltr"
		role="textbox"
		tabindex="0"
		aria-multiline="true"
		aria-placeholder={placeholder}
		on:input={handleInput}
		on:keydown={handleKeyDown}
		on:paste={handlePaste}
		class="chat-chip-input w-full min-h-[2.25rem] px-3 pt-2.5 pb-1 bg-transparent text-fg
			{disabled ? 'opacity-60' : ''}
			focus:outline-none transition-colors duration-100 text-sm leading-relaxed"
		style="white-space: pre-wrap; word-break: break-word;"
	></div>

	<!-- Placeholder (shown when empty) -->
	{#if !value && !disabled}
		<div
			class="absolute top-2.5 left-3 text-fg-subtle text-sm pointer-events-none"
			aria-hidden="true"
		>
			{placeholder}
		</div>
	{/if}

	<!-- Suggest Dropdown -->
	{#if isAutocompleteOpen}
		<AutocompleteDropdown
			categories={autocompleteCategories}
			suggestions={autocompleteSuggestions}
			selectedIndex={autocompleteSelectedIndex}
			onSelectCategory={handleSelectCategory}
			onSelectValue={handleSelectValue}
			onAttachCategory={handleAttachCategory}
			isLoading={autocompleteLoading}
			currentPath={autocompletePath}
			onClose={closeAutocomplete}
			onNavigateUp={handleNavigateUp}
			parentRef={containerRef}
			triggerChar="@"
			emptyHint="Resources — type @ + resource path"
			contextLabel="Resources"
		/>
	{/if}
</div>

<style>
	.chat-chip-input {
		font-family: inherit;
		font-size: 0.875rem;
		line-height: 1.625;
		position: relative;
	}

	.chat-chip-input:focus {
		outline: none;
	}

	/* Chip containers */
	.chat-chip-input :global(.inline-chip-container) {
		display: inline;
		user-select: none;
	}

	.chat-chip-input :global(.inline-chip) {
		display: inline-flex;
		vertical-align: baseline;
		white-space: nowrap;
	}
</style>
