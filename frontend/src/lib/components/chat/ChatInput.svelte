<script lang="ts">
	// Composer: context chips (pin + attached image) · auto-grow @resource
	// editor · a tools row (Add / Auto-attach / Tools / Memory) · send/stop.
	// Extracted from UnifiedAIChat's textarea block.
	import { onMount } from 'svelte';
	import ChatChipInput from './ChatChipInput.svelte';
	import ChatAttachPopover from './ChatAttachPopover.svelte';
	import { fade } from 'svelte/transition';
	import type { ResourceChipData, ChatToolInfo } from '$lib/types/chat';
	import type { UserToolPreference } from '$lib/types/llm';
	import type { LoraSelectionRow } from '$lib/stores/loraPickerSelections';
	import type { FormImageEntry } from '$lib/chat/formMedia';
	import { filterVisibleToolsByPreferences } from '$lib/chat/toolPreferences';
	import portal from '$lib/actions/portal';
	import { computeAnchoredMenuPosition, MENU_GAP } from '$lib/utils/menuPosition';

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

	// The Tools/Attach popovers open upward from a trigger near the bottom of
	// the composer, so each is portaled to <body> (position: fixed) and its
	// place computed from the trigger's rect rather than left-0/bottom-full,
	// which only anchor correctly inside an untransformed ancestor.
	let toolsTriggerEl: HTMLButtonElement;
	let toolsMenuEl: HTMLDivElement;
	let toolsMenuStyle = '';
	let attachTriggerEl: HTMLButtonElement;
	let attachMenuStyle = '';

	$: if (!showToolsDropdown) drillGroup = null;

	function computeMenuStyle(trigger: HTMLElement | undefined, width: number): string {
		if (!trigger) return '';
		const { left } = computeAnchoredMenuPosition(trigger, { width, align: 'left' });
		const bottom = window.innerHeight - trigger.getBoundingClientRect().top + MENU_GAP;
		return `left: ${left}px; bottom: ${bottom}px;`;
	}

	function toggleToolsDropdown() {
		showToolsDropdown = !showToolsDropdown;
		if (showToolsDropdown) {
			showAttachPopover = false;
			toolsMenuStyle = computeMenuStyle(toolsTriggerEl, 320);
		}
	}

	function toggleAttachPopover() {
		showAttachPopover = !showAttachPopover;
		if (showAttachPopover) {
			showToolsDropdown = false;
			attachMenuStyle = computeMenuStyle(attachTriggerEl, 420);
		}
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

<div class="flex-shrink-0 px-5 pt-2 pb-[17px]">
	<div
		class="rounded-xl border border-line-strong bg-surface-2 shadow-raised transition-colors duration-100 focus-within:border-signal {approvalsPending
			? 'opacity-60'
			: ''}"
	>
		{#if $$slots.context || attachedImage}
			<div class="flex items-center gap-1.5 px-2.5 pt-2 overflow-x-auto" style="scrollbar-width: none;">
				<slot name="context" />
				{#if attachedImage}
					<span
						class="inline-flex items-center gap-1.5 h-6 pl-1 pr-1 flex-shrink-0 rounded border border-line bg-surface-1 text-fg-muted"
						data-testid="composer-image-chip"
					>
						{#if attachedImage.url}
							<img src={attachedImage.url} alt="" class="w-4 h-4 rounded-sm object-cover flex-shrink-0" />
						{/if}
						<strong class="max-w-[110px] truncate text-2xs font-medium text-fg">{attachedImage.name || 'image'}</strong>
						<span class="font-mono text-2xs uppercase tracking-[0.06em] text-fg-subtle">image</span>
						<button
							type="button"
							aria-label="Remove attached image"
							class="w-4 h-4 grid place-items-center rounded text-fg-subtle hover:text-fg hover:bg-surface-2 transition-colors duration-100"
							on:click={() => onRemoveImage?.()}
						>
							<svg class="w-2.5 h-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
								<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12" />
							</svg>
						</button>
					</span>
				{/if}
			</div>
		{/if}
		<div class="min-h-[55px] max-h-[140px] overflow-y-auto">
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
		</div>
		<!-- Tools row -->
		<div class="flex items-center justify-between gap-2 px-2 pb-2 pt-0.5">
			<div class="flex items-center gap-0.5 flex-wrap min-w-0">
				{#if supportsVision}
					<button
						bind:this={attachTriggerEl}
						type="button"
						title="Attach image"
						aria-expanded={showAttachPopover}
						class="flex items-center gap-1.5 h-7 px-2 rounded transition-colors duration-100 {showAttachPopover
							? 'bg-signal/10 text-signal'
							: 'text-fg-subtle hover:text-fg hover:bg-surface-3'}"
						on:click={toggleAttachPopover}
					>
						<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4.5v15m7.5-7.5h-15" />
						</svg>
						<span class="text-xs">Add</span>
					</button>
					<button
						type="button"
						title="Auto-attach last generated image: {alwaysAttachLastImage ? 'ON' : 'OFF'}"
						aria-pressed={alwaysAttachLastImage}
						class="flex items-center gap-1.5 h-7 px-2 rounded border transition-colors duration-100 {alwaysAttachLastImage
							? 'border-signal/25 bg-signal/10 text-signal'
							: 'border-transparent text-fg-subtle hover:text-fg hover:bg-surface-3'}"
						on:click={onToggleAttachImage}
					>
						<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
						</svg>
						<span class="hidden md:inline text-xs">Auto-attach last image</span>
						<span
							class="font-mono text-2xs px-1 rounded {alwaysAttachLastImage
								? 'text-signal bg-signal/10'
								: 'text-fg-subtle bg-surface-3'}"
						>
							{alwaysAttachLastImage ? 'ON' : 'OFF'}
						</span>
					</button>
					<div class="w-px h-4 bg-line mx-0.5"></div>
				{/if}
				<!-- Tools -->
				<div class="relative">
					<button
						bind:this={toolsTriggerEl}
						type="button"
						title="Tools {enableTools ? 'ON' : 'OFF'}"
						aria-expanded={showToolsDropdown}
						class="flex items-center gap-1.5 h-7 px-2 rounded transition-colors duration-100 {showToolsDropdown ||
						enableTools
							? 'text-fg hover:bg-surface-3'
							: 'text-fg-subtle hover:text-fg hover:bg-surface-3'}"
						on:click={toggleToolsDropdown}
					>
						<svg class="w-3.5 h-3.5 {enableTools ? 'text-signal' : ''}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
						</svg>
						<span class="text-xs">Tools</span>
						{#if totalToolCount > 0}
							<span class="font-mono text-2xs tabular-nums {enableTools ? 'text-signal' : 'text-fg-subtle'}">{enabledToolCount}</span>
						{/if}
					</button>
					{#if showToolsDropdown}
						<div
							use:portal
							bind:this={toolsMenuEl}
							class="fixed z-[9999] w-80 bg-surface-1 border border-line rounded-xl shadow-floating max-h-[min(24rem,calc(100vh-8rem))] overflow-y-auto"
							style={toolsMenuStyle}
						>
							<div class="px-3 py-2.5 border-b border-line">
								<div class="text-xs font-semibold text-fg">Tools for this conversation</div>
								<div class="font-mono text-2xs text-fg-subtle mt-0.5 tabular-nums">
									{enabledToolCount} of {totalToolCount} enabled
								</div>
								<label class="flex items-center gap-2 cursor-pointer mt-2">
									<input
										type="checkbox"
										checked={enableTools}
										on:change={(e) => onToggleEnableTools?.(e.currentTarget.checked)}
										class="w-4 h-4 rounded border-line bg-canvas text-signal focus:ring-signal focus:ring-offset-0"
									/>
									<span class="text-xs text-fg-muted">Enable tools</span>
								</label>
							</div>
							{#if enableTools && effectiveVisibleTools.length > 0}
								{#if drillGroup === null}
									<div class="py-2 divide-y divide-line" data-testid="tool-group-list" transition:fade={{ duration: popoverTransitionDuration }}>
										{#each groupInfo as { group, tools, total, enabled } (group)}
											<div class="flex items-center gap-2 px-3 py-2 hover:bg-surface-2 transition-colors" data-testid="tool-group-row" data-group={group}>
												<input
													type="checkbox"
													checked={enabled === total}
													use:indeterminate={enabled > 0 && enabled < total}
													on:change={() => toggleGroup(tools, enabled, total)}
													class="w-4 h-4 rounded border-line bg-canvas text-signal focus:ring-signal focus:ring-offset-0 flex-shrink-0"
												/>
												<button
													type="button"
													class="flex-1 min-w-0 flex items-center justify-between gap-2 text-left"
													data-testid="tool-group-drill"
													on:click={() => (drillGroup = group)}
												>
													<span class="text-xs font-medium text-fg-muted truncate">{group}</span>
													<span class="flex items-center gap-1.5 flex-shrink-0">
														<span class="font-mono text-2xs text-fg-subtle tabular-nums" data-testid="tool-group-count">{enabled}/{total}</span>
														<svg class="w-3.5 h-3.5 text-fg-subtle" fill="none" stroke="currentColor" viewBox="0 0 24 24">
															<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
														</svg>
													</span>
												</button>
											</div>
										{/each}
									</div>
								{:else}
									<div class="py-2" data-testid="tool-group-detail" transition:fade={{ duration: popoverTransitionDuration }}>
										<button
											type="button"
											class="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-surface-2 transition-colors border-b border-line"
											data-testid="tool-group-back"
											on:click={() => (drillGroup = null)}
										>
											<svg class="w-3.5 h-3.5 text-fg-subtle flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
												<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7" />
											</svg>
											<span class="text-xs font-medium text-fg-muted truncate">{drillGroup}</span>
										</button>
										{#each activeGroupTools as tool (tool.name)}
											<label class="flex items-start gap-2 px-3 py-1.5 hover:bg-surface-2 cursor-pointer transition-colors" data-testid="tool-row" data-tool={tool.name}>
												<input
													type="checkbox"
													checked={!disabledTools.includes(tool.name)}
													on:change={() => onToggleTool?.(tool.name)}
													class="w-4 h-4 mt-px rounded border-line bg-canvas text-signal focus:ring-signal focus:ring-offset-0 flex-shrink-0"
												/>
												<div class="flex-1 min-w-0">
													<div class="flex items-center gap-1.5 min-w-0">
														<span class="text-xs font-medium text-fg-muted truncate">{toolLabel(tool)}</span>
														{#if !tool.mode}
															<span class="font-mono text-2xs uppercase tracking-[0.05em] px-1 py-0 bg-surface-2 text-fg-subtle rounded flex-shrink-0" title="Available in every mode">global</span>
														{/if}
													</div>
													{#if tool.user_description}
														<div class="text-[10px] text-fg-subtle mt-0.5 leading-snug">{tool.user_description}</div>
													{/if}
												</div>
											</label>
										{/each}
									</div>
								{/if}
							{:else if enableTools}
								<div class="px-3 py-4 text-xs text-fg-subtle text-center">No tools available</div>
							{/if}
							{#if onOpenToolPreferences}
								<button
									type="button"
									class="w-full flex items-center gap-2 px-3 py-2 text-left border-t border-line hover:bg-surface-2 transition-colors"
									on:click={() => { showToolsDropdown = false; onOpenToolPreferences?.(); }}
								>
									<svg class="w-3.5 h-3.5 text-fg-subtle flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
										<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" />
									</svg>
									<span class="text-xs font-medium text-fg-muted">Manage my tools&hellip;</span>
								</button>
							{/if}
						</div>
					{/if}
				</div>
				<!-- Memory -->
				{#if onOpenMemory}
					<button
						type="button"
						title="Memory"
						aria-pressed={memoryOpen}
						class="flex items-center gap-1.5 h-7 px-2 rounded transition-colors duration-100 {memoryOpen ? 'bg-signal/10 text-signal' : 'text-fg-subtle hover:text-fg hover:bg-surface-3'}"
						on:click={onOpenMemory}
					>
						<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.5 2A2.5 2.5 0 0112 4.5v15a2.5 2.5 0 01-4.9.7A2.5 2.5 0 013.5 17a2.5 2.5 0 01-.5-4.9A2.5 2.5 0 013 7.5 2.5 2.5 0 015.6 3.4 2.5 2.5 0 019.5 2zM14.5 2A2.5 2.5 0 0012 4.5v15a2.5 2.5 0 004.9.7A2.5 2.5 0 0020.5 17a2.5 2.5 0 00.5-4.9A2.5 2.5 0 0021 7.5a2.5 2.5 0 00-2.6-4.1A2.5 2.5 0 0014.5 2z" />
						</svg>
						<span class="text-xs">Memory</span>
						{#if memoryNoteCount != null}
							<span class="font-mono text-2xs tabular-nums text-signal">{memoryNoteCount}</span>
						{/if}
					</button>
				{/if}
			</div>
			<div class="flex items-center gap-2 flex-shrink-0">
				<span class="hidden lg:inline font-mono text-2xs text-fg-subtle whitespace-nowrap">ENTER TO SEND · SHIFT+ENTER FOR LINE</span>
				{#if isGenerating && onStop}
					<button
						type="button"
						title="Stop generating"
						class="w-8 h-8 grid place-items-center rounded bg-surface-3 text-fg-muted hover:text-fg hover:bg-surface-2 transition-colors duration-100"
						on:click={() => onStop?.()}
					>
						<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<rect x="6" y="6" width="12" height="12" rx="1.5" fill="currentColor" stroke="none" />
						</svg>
					</button>
				{:else}
					<button
						type="button"
						title="Send (Enter)"
						class="w-8 h-8 grid place-items-center rounded bg-accent text-accent-contrast hover:bg-accent-hover disabled:bg-surface-3 disabled:text-fg-subtle disabled:cursor-not-allowed transition-colors duration-100"
						on:click={handleSubmit}
						disabled={isGenerating || !value.trim() || disabled}
					>
						<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
						</svg>
					</button>
				{/if}
			</div>
		</div>
	</div>
</div>

{#if showAttachPopover}
	<ChatAttachPopover
		style={attachMenuStyle}
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
