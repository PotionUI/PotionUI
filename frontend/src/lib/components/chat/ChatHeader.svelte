<script lang="ts">
	// Header: rail toggle · title (+ inline rename) · mode chip · context meter ·
	// model button · more menu · close. Markup ported verbatim from the mock's
	// .chat-header (chat-rework BRIEF2); the three dropdowns are plain
	// position:absolute children (mock's .floating-menu, positioned against
	// .conversation) — no portal, no computed fixed coordinates.
	import { onMount, tick } from 'svelte';
	import { chatModes } from '$lib/stores/chatModes';
	import { chatSession, modeLocked } from '$lib/stores/chatSession';
	import ChatModeSelector from '$lib/components/chat/ChatModeSelector.svelte';
	import ConfirmModal from '$lib/components/modals/ConfirmModal.svelte';
	import { formatTokenCount } from '$lib/utils/chat';
	import { themeStore, resolvedTheme } from '$lib/stores/theme';

	// Provider+Model (LLM config) selection
	export let llmConfigs: any[] = [];
	export let selectedConfigId = '';
	export let onSelectConfig: (id: string) => void;

	// Mode selection (locks once the conversation has messages)
	export let onSelectMode: (id: string) => void;
	/** The scope the current page resolves to (route or a declared override) —
	 * passed through to the mode chip so a locked conversation whose scope no
	 * longer matches the page can say so. Null while unresolved. */
	export let pageModeId: string | null = null;
	export let onNewChat: (() => void) | undefined = undefined;

	// Token usage: the last request's prompt-token count. No context-window
	// field exists on an LLM config today, so the ring has nothing to size an
	// arc against — it renders unfilled and the title carries the number.
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

	let showMoreMenu = false;
	let moreTriggerEl: HTMLButtonElement;
	let moreMenuEl: HTMLDivElement;

	let renaming = false;
	let renameDraft = '';
	let renameInputEl: HTMLInputElement | undefined;

	let showDeleteConfirm = false;

	$: mode = $chatSession.mode;

	$: selectedModelName = llmConfigs.find((c) => c.id === selectedConfigId)?.name || 'Model';

	function toggleModelDropdown() {
		showModelDropdown = !showModelDropdown;
		if (showModelDropdown) showMoreMenu = false;
	}

	function toggleMoreMenu() {
		showMoreMenu = !showMoreMenu;
		if (showMoreMenu) showModelDropdown = false;
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

<header class="chat-header">
	<button
		type="button"
		aria-pressed={!railCollapsed}
		aria-label={railCollapsed ? 'Show history' : 'Hide history'}
		title={railCollapsed ? 'Show history' : 'Hide history'}
		class="icon-button"
		on:click={onToggleRail}
	>
		<svg class="icon"><use href="#i-menu" /></svg>
	</button>

	<div class="header-title">
		{#if renaming}
			<input
				bind:this={renameInputEl}
				bind:value={renameDraft}
				type="text"
				on:blur={commitRename}
				on:keydown={handleRenameKeydown}
			/>
		{:else}
			<h1>{title}</h1>
		{/if}
		{#if subtitle && !renaming}
			<p>{subtitle}</p>
		{/if}
	</div>

	<div class="header-actions">
		{#if $chatModes.modes.length > 0}
			<ChatModeSelector
				modes={$chatModes.modes}
				selected={mode}
				locked={$modeLocked}
				onSelect={onSelectMode}
				{pageModeId}
				{onNewChat}
			/>
		{/if}

		{#if currentContextSize > 0}
			<button
				type="button"
				class="context-meter"
				title={`Context: ${currentContextSize.toLocaleString()} tokens in the last request`}
			>
				<span class="context-meter-ring"></span><span>{formatTokenCount(currentContextSize)}</span>
			</button>
		{/if}

		{#if llmConfigs.length > 0}
			<button
				bind:this={modelTriggerEl}
				type="button"
				title="LLM Model"
				class="model-button"
				class:open={showModelDropdown}
				aria-expanded={showModelDropdown}
				data-testid="chat-header-model-trigger"
				on:click={toggleModelDropdown}
			>
				<span class="model-dot"></span>
				<span>{selectedModelName || 'Model'}</span>
				<svg class="icon chevron"><use href="#i-chevron" /></svg>
			</button>
		{/if}

		<button
			bind:this={moreTriggerEl}
			type="button"
			aria-label="Conversation options"
			title="Conversation options"
			class="icon-button"
			aria-expanded={showMoreMenu}
			on:click={toggleMoreMenu}
		>
			<svg class="icon"><use href="#i-more" /></svg>
		</button>

		{#if onClose}
			<div class="header-divider"></div>
			<button type="button" title="Close (Esc)" aria-label="Close chat" class="icon-button" on:click={onClose}>
				<svg class="icon"><use href="#i-close" /></svg>
			</button>
		{/if}
	</div>

	{#if showModelDropdown && llmConfigs.length > 0}
		<div class="floating-menu" bind:this={modelMenuEl} data-testid="chat-header-model-menu">
			<div class="menu-label">Choose a model</div>
			{#each llmConfigs as config}
				<button
					type="button"
					class="model-option"
					class:selected={config.id === selectedConfigId}
					on:click={() => {
						onSelectConfig(config.id);
						showModelDropdown = false;
					}}
				>
					<span class="model-dot"></span>
					<span class="model-option-copy">
						<strong>{config.name}</strong>
						<small>{config.model}</small>
					</span>
					{#if config.id === selectedConfigId}
						<svg class="icon check"><use href="#i-check" /></svg>
					{/if}
				</button>
			{/each}
		</div>
	{/if}

	{#if showMoreMenu}
		<div class="floating-menu conversation-menu" bind:this={moreMenuEl}>
			<button type="button" disabled={!hasSession} on:click={startRename}>
				<svg class="icon"><use href="#i-edit" /></svg>Rename conversation
			</button>
			<button type="button" on:click={toggleTheme}>
				<svg class="icon"><use href="#i-sun" /></svg>Toggle light theme
			</button>
			<button type="button" disabled={!hasSession} on:click={handleExport}>
				<svg class="icon"><use href="#i-file" /></svg>Export transcript
			</button>
			<button type="button" class="danger-option" disabled={!hasSession} on:click={openDeleteConfirm}>
				<svg class="icon"><use href="#i-trash" /></svg>Delete conversation
			</button>
		</div>
	{/if}
</header>

<ConfirmModal
	isOpen={showDeleteConfirm}
	title="Delete this conversation?"
	message="This permanently deletes the conversation and its messages. This can't be undone."
	variant="danger"
	on:confirm={handleDeleteConfirmed}
	on:cancel={() => (showDeleteConfirm = false)}
/>
