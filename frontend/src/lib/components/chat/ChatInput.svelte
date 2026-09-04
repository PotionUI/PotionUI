<script lang="ts">
	// Composer: context chips (pin + attached image) · auto-grow @resource
	// editor · a tools row (Add / Auto-attach / Tools / Memory) · send/stop.
	// Markup ported verbatim from the mock's .composer-wrap (chat-rework BRIEF2);
	// the tools popover's master toggle + grouped drill-down is real
	// functionality the prototype's flat tool list never shows (additions
	// idiom in chat-concept.css).
	import { onMount } from 'svelte';
	import ChatChipInput from './ChatChipInput.svelte';
	import ChatAttachPopover from './ChatAttachPopover.svelte';
	import { fade } from 'svelte/transition';
	import type { ResourceChipData, ChatToolInfo } from '$lib/types/chat';
	import type { UserToolPreference } from '$lib/types/llm';
	import type { LoraSelectionRow } from '$lib/stores/loraPickerSelections';
	import type { FormImageEntry } from '$lib/chat/formMedia';
	import { filterVisibleToolsByPreferences } from '$lib/chat/toolPreferences';

	export let value: string = '';
	export let resources: Record<string, ResourceChipData> = {};
	export let mode: string = '';
	/** Live generate-form values, forwarded to power @form autocomplete. */
	export let formData: Record<string, any> = {};
	/** Selected LoRAs per lora_picker field, forwarded for @form browse rows. */
	export let loraSelections: Record<string, LoraSelectionRow[]> = {};
	export let disabled: boolean = false;
	/** True while the ApprovalDock is showing — the composer stays visible but inert. */
	export let approvalsPending: boolean = false;
	export let isGenerating: boolean = false;
	export let supportsVision: boolean = false;
	export let onSend: (() => void) | undefined = undefined;
	export let onStop: (() => void) | undefined = undefined;
	export let onKeydown: ((e: KeyboardEvent) => void) | undefined = undefined;
	/** An image pasted into the composer text, forwarded here from ChatChipInput. */
	export let onPasteImage: ((file: File) => void) | undefined = undefined;

	// Vision attach popover (see ChatAttachPopover.svelte) — UnifiedAIChat owns
	// the actual image state; these are the seam it fills.
	export let formImageEntries: FormImageEntry[] = [];
	export let selectedDurablePath: string | null = null;
	export let onSelectFormImage: (entry: FormImageEntry) => void = () => {};
	export let selectedImageData: unknown = null;
	export let onMediaLoaderChange: (value: unknown) => void = () => {};
	export let lastGeneratedImage: { url: string; name: string; generatedAt?: string } | null = null;
	export let onAttachLastImage: () => void = () => {};
	export let onRemoveImage: () => void = () => {};

	// Auto-attach last generated image (vision only)
	export let alwaysAttachLastImage = false;
	export let onToggleAttachImage: (() => void) | undefined = undefined;

	// Tools (subtractive: all mode tools on by default, unticks disable)
	export let visibleTools: ChatToolInfo[] = [];
	export let disabledTools: string[] = [];
	export let enableTools = true;
	export let onToggleEnableTools: ((enabled: boolean) => void) | undefined = undefined;
	export let onToggleTool: ((name: string) => void) | undefined = undefined;
	// Persistent per-user tool governance (admin-enabled + this user's own
	// opt-out, see ChatToolPreferencesPanel) - null while still loading, in
	// which case every mode tool is shown same as before this existed.
	export let myToolPreferences: UserToolPreference[] | null = null;
	export let onOpenToolPreferences: (() => void) | undefined = undefined;

	// Memory panel (the panel itself is a self-positioned overlay; this is just its trigger)
	export let onOpenMemory: (() => void) | undefined = undefined;
	export let memoryOpen = false;
	/** Active note count from ChatMemoryPanel's onCountChange, badge only shown once known. */
	export let memoryNoteCount: number | null = null;

	let chipInputRef: ChatChipInput;
	let showToolsDropdown = false;
	let showAttachPopover = false;
	let drillGroup: string | null = null;

	let toolsTriggerEl: HTMLButtonElement;
	let toolsMenuEl: HTMLDivElement;
	let attachTriggerEl: HTMLButtonElement;

	$: if (!showToolsDropdown) drillGroup = null;

	function toggleToolsDropdown() {
		showToolsDropdown = !showToolsDropdown;
		if (showToolsDropdown) showAttachPopover = false;
	}

	function toggleAttachPopover() {
		showAttachPopover = !showAttachPopover;
		if (showAttachPopover) showToolsDropdown = false;
	}

	function handleOutsidePointerDown(e: PointerEvent) {
		if (!showToolsDropdown) return;
		const target = e.target as Node;
		if (!toolsMenuEl?.contains(target) && !toolsTriggerEl?.contains(target)) {
			showToolsDropdown = false;
		}
	}

	function handleOutsideKeydown(e: KeyboardEvent) {
		if (e.key !== 'Escape' || !showToolsDropdown) return;
		showToolsDropdown = false;
		// A document-level bubble listener runs before window's (capture goes
		// window->document->target, bubble reverses that), so stopping here
		// keeps GlobalChatPanel's <svelte:window on:keydown> from also
		// closing the whole chat panel on this same Escape press.
		e.stopPropagation();
	}

	onMount(() => {
		document.addEventListener('pointerdown', handleOutsidePointerDown, true);
		document.addEventListener('keydown', handleOutsideKeydown);
		return () => {
			document.removeEventListener('pointerdown', handleOutsidePointerDown, true);
			document.removeEventListener('keydown', handleOutsideKeydown);
		};
	});

	const prefersReducedMotion =
		typeof window !== 'undefined' &&
		!!window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches;
	const popoverTransitionDuration = prefersReducedMotion ? 0 : 140;

	function indeterminate(node: HTMLInputElement, isIndeterminate: boolean) {
		node.indeterminate = isIndeterminate;
		return {
			update(next: boolean) {
				node.indeterminate = next;
			}
		};
	}

	export function focus() {
		chipInputRef?.focus();
	}

	function handleChange(e: CustomEvent<{ value: string; resources: Record<string, ResourceChipData> }>) {
		value = e.detail.value;
		resources = e.detail.resources;
	}

	function handleSubmit() {
		if (!disabled && value.trim()) onSend?.();
	}

	function toolLabel(tool: { name: string; label?: string | null }): string {
		return tool.label || tool.name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
	}

	function groupTools(tools: ChatToolInfo[]): [string, ChatToolInfo[]][] {
		const map = new Map<string, ChatToolInfo[]>();
		for (const tool of tools) {
			const group = tool.group || 'Other';
			const bucket = map.get(group);
			if (bucket) bucket.push(tool);
			else map.set(group, [tool]);
		}
		const entries = [...map.entries()];
		return [...entries.filter(([g]) => g !== 'Other'), ...entries.filter(([g]) => g === 'Other')];
	}

	// Tools an admin turned off entirely don't belong in this popover at all -
	// they're not "unticked", they don't exist.
	$: effectiveVisibleTools = filterVisibleToolsByPreferences(visibleTools, myToolPreferences);
	$: groupedTools = groupTools(effectiveVisibleTools);
	$: groupInfo = groupedTools.map(([group, tools]) => ({
		group,
		tools,
		total: tools.length,
		enabled: tools.filter((t) => !disabledTools.includes(t.name)).length
	}));
	$: activeGroupEntry = drillGroup ? groupInfo.find((g) => g.group === drillGroup) : null;
	$: activeGroupTools = activeGroupEntry ? activeGroupEntry.tools : [];
	$: totalToolCount = effectiveVisibleTools.length;
	$: enabledToolCount = enableTools
		? effectiveVisibleTools.filter((t) => !disabledTools.includes(t.name)).length
		: 0;

	function toggleGroup(tools: ChatToolInfo[], enabled: number, total: number) {
		const shouldEnable = enabled < total;
		for (const tool of tools) {
			const isDisabled = disabledTools.includes(tool.name);
			if (shouldEnable && isDisabled) onToggleTool?.(tool.name);
			else if (!shouldEnable && !isDisabled) onToggleTool?.(tool.name);
		}
	}

	// selectedImageData's real shape (see UnifiedAIChat) has `url`/`name`, but
	// the seam contract types it `unknown` — narrow just enough to render a chip.
	$: attachedImage =
		selectedImageData && typeof selectedImageData === 'object'
			? (selectedImageData as { url?: string; name?: string })
			: null;
</script>

<div class="composer-wrap">
	{#if showToolsDropdown}
		<div class="tools-popover" bind:this={toolsMenuEl}>
			<div class="popover-head">
				<strong>Tools for this conversation</strong>
				<span>{enabledToolCount} of {totalToolCount} enabled</span>
			</div>
			<label class="tool-row">
				<input
					type="checkbox"
					checked={enableTools}
					on:change={(e) => onToggleEnableTools?.(e.currentTarget.checked)}
				/>
				<span>Enable tools</span>
			</label>
			{#if enableTools && effectiveVisibleTools.length > 0}
				{#if drillGroup === null}
					<div data-testid="tool-group-list" transition:fade={{ duration: popoverTransitionDuration }}>
						{#each groupInfo as { group, tools, total, enabled } (group)}
							<div class="tool-row" data-testid="tool-group-row" data-group={group}>
								<input
									type="checkbox"
									checked={enabled === total}
									use:indeterminate={enabled > 0 && enabled < total}
									on:change={() => toggleGroup(tools, enabled, total)}
								/>
								<button
									type="button"
									class="tool-row-drill"
									data-testid="tool-group-drill"
									on:click={() => (drillGroup = group)}
								>
									<span>{group}</span>
									<span class="tool-row-count" data-testid="tool-group-count">{enabled}/{total}</span>
									<svg class="icon tool-row-chevron"><use href="#i-chevron" /></svg>
								</button>
							</div>
						{/each}
					</div>
				{:else}
					<div data-testid="tool-group-detail" transition:fade={{ duration: popoverTransitionDuration }}>
						<button
							type="button"
							class="tool-row-back"
							data-testid="tool-group-back"
							on:click={() => (drillGroup = null)}
						>
							<svg class="icon"><use href="#i-chevron" /></svg>
							<span>{drillGroup}</span>
						</button>
						{#each activeGroupTools as tool (tool.name)}
							<label
								class="tool-row"
								data-testid="tool-row"
								data-tool={tool.name}
								title={tool.user_description || undefined}
							>
								<input
									type="checkbox"
									checked={!disabledTools.includes(tool.name)}
									on:change={() => onToggleTool?.(tool.name)}
								/>
								<span>{toolLabel(tool)}</span>
								{#if !tool.mode}<small title="Available in every mode">GLOBAL</small>{/if}
							</label>
						{/each}
					</div>
				{/if}
			{:else if enableTools}
				<div class="tool-row-empty">No tools available</div>
			{/if}
			{#if onOpenToolPreferences}
				<button
					type="button"
					class="tool-row-manage"
					on:click={() => {
						showToolsDropdown = false;
						onOpenToolPreferences?.();
					}}
				>
					<svg class="icon"><use href="#i-info" /></svg>
					<span>Manage my tools&hellip;</span>
				</button>
			{/if}
		</div>
	{/if}

	{#if showAttachPopover}
		<ChatAttachPopover
			triggerEl={attachTriggerEl}
			onClose={() => (showAttachPopover = false)}
			{formImageEntries}
			{selectedDurablePath}
			{onSelectFormImage}
			{selectedImageData}
			{onMediaLoaderChange}
			{lastGeneratedImage}
			{onAttachLastImage}
			{alwaysAttachLastImage}
			{onToggleAttachImage}
		/>
	{/if}

	<div class="composer" class:approvals-pending={approvalsPending}>
		<div class="composer-context">
			<slot name="context" />
			{#if attachedImage}
				<span class="resource-chip image-resource-chip" data-testid="composer-image-chip">
					{#if attachedImage.url}
						<img src={attachedImage.url} alt="" />
					{/if}
					<strong>{attachedImage.name || 'image'}</strong>
					<span class="resource-state">image</span>
					<button
						type="button"
						class="chip-remove"
						aria-label="Remove attached image"
						on:click={() => onRemoveImage?.()}
					>
						<svg class="icon"><use href="#i-close" /></svg>
					</button>
				</span>
			{/if}
		</div>
		<ChatChipInput
			bind:this={chipInputRef}
			{value}
			{resources}
			{mode}
			{formData}
			{loraSelections}
			{disabled}
			placeholder={approvalsPending
				? 'Resolve approvals to continue…'
				: 'Ask the AI anything... (@ to attach a resource)'}
			on:change={handleChange}
			on:submit={handleSubmit}
			on:keydown={(e) => onKeydown?.(e.detail)}
			on:pasteimage={(e) => onPasteImage?.(e.detail.file)}
		/>
		<div class="composer-actions">
			<div class="composer-tools">
				{#if supportsVision}
					<button
						bind:this={attachTriggerEl}
						type="button"
						class="composer-tool"
						class:active={showAttachPopover}
						title="Add image or resource"
						aria-expanded={showAttachPopover}
						on:click={toggleAttachPopover}
					>
						<svg class="icon"><use href="#i-plus" /></svg><span>Add</span>
					</button>
					<button
						type="button"
						class="composer-tool auto-image-tool"
						class:active={alwaysAttachLastImage}
						aria-pressed={alwaysAttachLastImage}
						title="Auto-attach last generated image: {alwaysAttachLastImage
							? 'ON'
							: 'OFF'}"
						on:click={onToggleAttachImage}
					>
						<svg class="icon"><use href="#i-image" /></svg>
						<span class="auto-label-full">Auto-attach last image</span>
						<span class="auto-label-short">Auto image</span>
						<span class="auto-state">{alwaysAttachLastImage ? 'ON' : 'OFF'}</span>
					</button>
				{/if}
				<button
					bind:this={toolsTriggerEl}
					type="button"
					class="composer-tool"
					class:active={showToolsDropdown || enableTools}
					title="Tools {enableTools ? 'ON' : 'OFF'}"
					aria-expanded={showToolsDropdown}
					on:click={toggleToolsDropdown}
				>
					<svg class="icon"><use href="#i-tools" /></svg><span>Tools</span>
					{#if totalToolCount > 0}<span class="tool-count">{enabledToolCount}</span>{/if}
				</button>
				{#if onOpenMemory}
					<button
						type="button"
						class="composer-tool"
						class:active={memoryOpen}
						title="Memory"
						aria-pressed={memoryOpen}
						on:click={onOpenMemory}
					>
						<svg class="icon"><use href="#i-memory" /></svg><span>Memory</span>
						{#if memoryNoteCount != null}<span class="tool-count">{memoryNoteCount}</span>{/if}
					</button>
				{/if}
			</div>
			<div class="send-area">
				<span class="send-hint">ENTER TO SEND · SHIFT+ENTER FOR LINE</span>
				{#if isGenerating && onStop}
					<button type="button" class="send-button" title="Stop generating" on:click={() => onStop?.()}>
						<svg class="icon" fill="none" stroke="none" viewBox="0 0 24 24">
							<rect x="6" y="6" width="12" height="12" rx="1.5" fill="currentColor" />
						</svg>
					</button>
				{:else}
					<button
						type="button"
						class="send-button"
						title="Send (Enter)"
						aria-label="Send message"
						on:click={handleSubmit}
						disabled={isGenerating || !value.trim() || disabled}
					>
						<svg class="icon"><use href="#i-arrow-up" /></svg>
					</button>
				{/if}
			</div>
		</div>
	</div>
</div>
