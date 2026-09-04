<script lang="ts">
	import { onMount, tick } from 'svelte';
	import { chatModes } from '$lib/stores/chatModes';
	import { chatSession, modeLocked } from '$lib/stores/chatSession';
	import ChatModeSelector from '$lib/components/chat/ChatModeSelector.svelte';
	import ConfirmModal from '$lib/components/modals/ConfirmModal.svelte';
	import { formatTokenCount } from '$lib/utils/chat';
	import { themeStore, resolvedTheme } from '$lib/stores/theme';
	import portal from '$lib/actions/portal';
	import { computeFlippedMenuPosition } from '$lib/utils/menuPosition';
	import Tooltip from '$lib/components/Tooltip.svelte';

	// Provider+Model (LLM config) selection
	export let llmConfigs: any[] = [];
	export let selectedConfigId = '';
	export let onSelectConfig: (id: string) => void;

	// Mode selection (locks once the conversation has messages)
	export let onSelectMode: (id: string) => void;

	// Token usage: the last request's prompt-token count. No context-window
	// field exists on an LLM config today, so the ring has nothing to size an
	// arc against — it renders unfilled and the tooltip carries the number.
	export let currentContextSize = 0;

	// History rail toggle (leftmost header control)
	export let railCollapsed = false;
	export let onToggleRail: () => void;

	// Conversation identity
	export let title = 'New conversation';
	/** e.g. "Reading <tab> · N images in thread" — blank renders nothing. */
	export let subtitle = '';
	/** False for a conversation that has no session yet — rename/export/delete need one. */
	export let hasSession = false;
	export let onRename: (newTitle: string) => void;
	export let onExportTranscript: () => void;
	export let onDeleteConversation: () => void;

	export let onClose: (() => void) | undefined = undefined;

	let showModelDropdown = false;
	let modelTriggerEl: HTMLButtonElement;
	let modelMenuEl: HTMLDivElement;
	let modelMenuStyle = '';

	let showMoreMenu = false;
	let moreTriggerEl: HTMLButtonElement;
	let moreMenuEl: HTMLDivElement;
	let moreMenuStyle = '';

	let renaming = false;
	let renameDraft = '';
	let renameInputEl: HTMLInputElement | undefined;

	let showDeleteConfirm = false;

	$: mode = $chatSession.mode;

	$: llmConfigOptions = llmConfigs.map((config) => ({
		value: config.id,
		label: config.name
	}));
	$: selectedModelName = llmConfigs.find((c) => c.id === selectedConfigId)?.name || 'Model';

	function toggleModelDropdown() {
		showModelDropdown = !showModelDropdown;
		if (showModelDropdown) modelMenuStyle = computeModelMenuStyle();
		showMoreMenu = false;
	}

	function computeModelMenuStyle(): string {
		if (!modelTriggerEl) return '';
		const pos = computeFlippedMenuPosition(modelTriggerEl, { width: 256 });
		const vertical = pos.top !== undefined ? `top: ${pos.top}px;` : `bottom: ${pos.bottom}px;`;
		return `left: ${pos.left}px; ${vertical}`;
	}

	function toggleMoreMenu() {
		showMoreMenu = !showMoreMenu;
		if (showMoreMenu) moreMenuStyle = computeMoreMenuStyle();
		showModelDropdown = false;
	}

	function computeMoreMenuStyle(): string {
		if (!moreTriggerEl) return '';
		const pos = computeFlippedMenuPosition(moreTriggerEl, { width: 210 });
		const vertical = pos.top !== undefined ? `top: ${pos.top}px;` : `bottom: ${pos.bottom}px;`;
		return `left: ${pos.left}px; ${vertical}`;
	}

	async function startRename() {
		if (!hasSession) return;
		showMoreMenu = false;
		renameDraft = title;
		renaming = true;
		await tick();
		renameInputEl?.focus();
		renameInputEl?.select();
	}

	function commitRename() {
		if (!renaming) return;
		renaming = false;
		const next = renameDraft.trim();
		if (next && next !== title) onRename(next);
	}

	function cancelRename() {
		renaming = false;
	}

	function handleRenameKeydown(e: KeyboardEvent) {
		if (e.key === 'Enter') {
			e.preventDefault();
			commitRename();
		} else if (e.key === 'Escape') {
			e.preventDefault();
			cancelRename();
			// Same reasoning as the dropdown handlers below — don't let this
			// Escape also bubble up into GlobalChatPanel's close handler.
			e.stopPropagation();
		}
	}

	function toggleTheme() {
		showMoreMenu = false;
		themeStore.setPref($resolvedTheme === 'dark' ? 'light' : 'dark');
	}

	function handleExport() {
		showMoreMenu = false;
		onExportTranscript();
	}

	function openDeleteConfirm() {
		showMoreMenu = false;
		showDeleteConfirm = true;
	}

	function handleDeleteConfirmed() {
		showDeleteConfirm = false;
		onDeleteConversation();
	}

	function handleOutsidePointerDown(e: PointerEvent) {
		const target = e.target as Node;
		if (showModelDropdown && !modelMenuEl?.contains(target) && !modelTriggerEl?.contains(target)) {
			showModelDropdown = false;
		}
		if (showMoreMenu && !moreMenuEl?.contains(target) && !moreTriggerEl?.contains(target)) {
			showMoreMenu = false;
		}
	}

	function handleOutsideKeydown(e: KeyboardEvent) {
		if (e.key !== 'Escape') return;
		if (!showModelDropdown && !showMoreMenu) return;
		showModelDropdown = false;
		showMoreMenu = false;
		// A document-level bubble listener runs before window's, so stopping
		// here keeps GlobalChatPanel's <svelte:window on:keydown> from also
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
</script>

<div class="flex h-[68px] flex-shrink-0 items-center gap-2 border-b border-line bg-surface-1 px-3">
	<Tooltip text={railCollapsed ? 'Show history' : 'Hide history'} position="bottom" delay={150}>
		<button
			type="button"
			aria-pressed={!railCollapsed}
			aria-label={railCollapsed ? 'Show history' : 'Hide history'}
			class="p-1.5 rounded text-fg-muted transition-colors {railCollapsed ? 'hover:text-fg hover:bg-surface-2' : 'bg-signal/10 text-signal'}"
			on:click={onToggleRail}
		>
			<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 7h16M4 12h16M4 17h16" />
			</svg>
		</button>
	</Tooltip>

	<div class="min-w-0 flex-1">
		{#if renaming}
			<input
				bind:this={renameInputEl}
				bind:value={renameDraft}
				type="text"
				class="w-full bg-transparent text-sm font-semibold text-fg border-b border-signal focus:outline-none"
				on:blur={commitRename}
				on:keydown={handleRenameKeydown}
			/>
		{:else}
			<h1 class="truncate text-sm font-semibold text-fg leading-tight">{title}</h1>
		{/if}
		{#if subtitle && !renaming}
			<p class="mt-0.5 truncate font-mono text-2xs uppercase tracking-[0.04em] text-fg-subtle">
				{subtitle}
			</p>
		{/if}
	</div>

	<div class="flex flex-shrink-0 items-center gap-1.5">
		<!-- Mode chip: icon + name, lock once the conversation has messages -->
		{#if $chatModes.modes.length > 0}
			<ChatModeSelector modes={$chatModes.modes} selected={mode} locked={$modeLocked} onSelect={onSelectMode} />
		{/if}

		<!-- Context meter: last request's prompt-token count -->
		{#if currentContextSize > 0}
			<span
				class="flex h-8 items-center gap-1.5 rounded border border-line px-2 text-fg-subtle"
				title={`Context: ${currentContextSize.toLocaleString()} tokens in the last request`}
			>
				<span class="h-3 w-3 flex-shrink-0 rounded-full border-2 border-line-strong"></span>
				<span class="font-mono text-2xs tabular-nums">{formatTokenCount(currentContextSize)}</span>
			</span>
		{/if}

		<!-- Model selector -->
		{#if llmConfigs.length > 0}
			<div class="relative flex-shrink-0">
				<button
					bind:this={modelTriggerEl}
					type="button"
					title="LLM Model"
					aria-expanded={showModelDropdown}
					data-testid="chat-header-model-trigger"
					class="flex h-8 items-center gap-1.5 rounded border px-2.5 text-xs transition-colors {showModelDropdown
						? 'border-line-hover bg-surface-2 text-fg'
						: 'border-line text-fg-muted hover:border-line-hover hover:bg-surface-2 hover:text-fg'}"
					on:click={toggleModelDropdown}
				>
					<span class="h-1.5 w-1.5 flex-shrink-0 rounded-full bg-success"></span>
					<span class="max-w-[120px] truncate">{selectedModelName || 'Model'}</span>
					<svg class="w-2.5 h-2.5 flex-shrink-0 text-fg-subtle" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
					</svg>
				</button>
				{#if showModelDropdown}
					<div
						use:portal
						bind:this={modelMenuEl}
						data-testid="chat-header-model-menu"
						class="fixed z-[9999] w-64 bg-surface-2 border border-line-strong rounded-xl shadow-floating max-h-60 overflow-y-auto p-1"
						style={modelMenuStyle}
					>
						<div class="px-2 py-1.5 font-mono text-2xs uppercase tracking-[0.08em] text-fg-subtle">
							Choose a model
						</div>
						{#each llmConfigOptions as option}
							<button
								type="button"
								class="w-full rounded-lg px-2.5 py-2 text-left text-xs transition-colors {option.value === selectedConfigId
									? 'bg-signal/10 text-fg'
									: 'text-fg-muted hover:bg-surface-3'}"
								on:click={() => { onSelectConfig(option.value); showModelDropdown = false; }}
							>
								{option.label}
							</button>
						{/each}
					</div>
				{/if}
			</div>
		{/if}

		<!-- More menu: rename / theme / export / delete -->
		<div class="relative flex-shrink-0">
			<Tooltip text="Conversation options" position="bottom" delay={150}>
				<button
					bind:this={moreTriggerEl}
					type="button"
					aria-label="Conversation options"
					aria-expanded={showMoreMenu}
					class="p-1.5 rounded transition-colors {showMoreMenu ? 'bg-surface-2 text-fg' : 'text-fg-muted hover:text-fg hover:bg-surface-2'}"
					on:click={toggleMoreMenu}
				>
					<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<circle cx="5" cy="12" r="1.5" fill="currentColor" stroke="none" />
						<circle cx="12" cy="12" r="1.5" fill="currentColor" stroke="none" />
						<circle cx="19" cy="12" r="1.5" fill="currentColor" stroke="none" />
					</svg>
				</button>
			</Tooltip>
			{#if showMoreMenu}
				<div
					use:portal
					bind:this={moreMenuEl}
					class="fixed z-[9999] w-52 bg-surface-2 border border-line-strong rounded-xl shadow-floating p-1"
					style={moreMenuStyle}
				>
					<button
						type="button"
						disabled={!hasSession}
						class="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs text-fg-muted transition-colors hover:bg-surface-3 hover:text-fg disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-transparent"
						on:click={startRename}
					>
						<svg class="w-3.5 h-3.5 text-fg-subtle flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
						</svg>
						Rename conversation
					</button>
					<button
						type="button"
						class="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs text-fg-muted transition-colors hover:bg-surface-3 hover:text-fg"
						on:click={toggleTheme}
					>
						<svg class="w-3.5 h-3.5 text-fg-subtle flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<circle cx="12" cy="12" r="4" stroke-width="2" />
							<path stroke-linecap="round" stroke-width="2" d="M12 2v2M12 20v2M4.93 4.93l1.42 1.42M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.42-1.42M17.66 6.34l1.41-1.41" />
						</svg>
						Toggle light theme
					</button>
					<button
						type="button"
						disabled={!hasSession}
						class="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs text-fg-muted transition-colors hover:bg-surface-3 hover:text-fg disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-transparent"
						on:click={handleExport}
					>
						<svg class="w-3.5 h-3.5 text-fg-subtle flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 3h8l4 4v14H6Z" />
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 3v5h5" />
						</svg>
						Export transcript
					</button>
					<div class="my-1 h-px bg-line"></div>
					<button
						type="button"
						disabled={!hasSession}
						class="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs text-danger transition-colors hover:bg-danger/10 disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-transparent"
						on:click={openDeleteConfirm}
					>
						<svg class="w-3.5 h-3.5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
						</svg>
						Delete conversation
					</button>
				</div>
			{/if}
		</div>

		<!-- Close button: a plain native title (not the Tooltip+IconButton
		     idiom used elsewhere) so it keeps the exact
		     `button[title="Close (Esc)"]` handle chat-composer-draft.spec.ts
		     already depends on. -->
		{#if onClose}
			<div class="w-px h-5 bg-line mx-0.5"></div>
			<button
				type="button"
				title="Close (Esc)"
				class="p-1.5 text-fg-subtle hover:text-fg-muted hover:bg-surface-2 rounded transition-colors"
				on:click={onClose}
			>
				<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
				</svg>
			</button>
		{/if}
	</div>
</div>

<ConfirmModal
	isOpen={showDeleteConfirm}
	title="Delete this conversation?"
	message="This permanently deletes the conversation and its messages. This can't be undone."
	variant="danger"
	on:confirm={handleDeleteConfirmed}
	on:cancel={() => (showDeleteConfirm = false)}
/>
