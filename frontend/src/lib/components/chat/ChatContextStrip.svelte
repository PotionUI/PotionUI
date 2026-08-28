<script lang="ts">
	/**
	 * Docked above the composer, always visible while it renders — same idiom
	 * and slot family as ChatScopeBanner / ApprovalDock. States which tab the
	 * chat is currently reading (see $lib/chat/contextStrip.ts for the state
	 * machine): following the active tab, pinned and in agreement with it, or
	 * pinned and mismatched with it. The tab name is itself the trigger for the
	 * pin picker (portaled dropdown, same mechanism ChatInput's Tools menu
	 * uses — portal + computed fixed position + document-level close
	 * listeners — just anchored here instead; see computeFlippedMenuPosition).
	 */
	import { onMount } from 'svelte';
	import type { ContextStripModel } from '$lib/chat/contextStrip';
	import portal from '$lib/actions/portal';
	import { computeFlippedMenuPosition } from '$lib/utils/menuPosition';

	export let model: ContextStripModel;
	/** Transient signal-blue highlight right after a followed tab switch (~1.2s, caller owns the timer). */
	export let flash: boolean = false;
	/** All open tabs, for the picker's tab list. */
	export let allTabs: Array<{ id: string; name: string }> = [];
	export let activeTabId: string | null = null;
	export let pinnedTabId: string | null = null;
	/** tab.id -> resolved preset display name, shown as each picker row's subtitle. */
	export let tabPresetNames: Record<string, string | null> = {};
	/** null pins to "follow active tab"; a tab id pins to that tab. */
	export let onPinTab: ((id: string | null) => void) | undefined = undefined;
	export let onSwitchToPinned: (() => void) | undefined = undefined;

	$: dimsText = model.dims
		? model.steps
			? `${model.dims} · ${model.steps} STEPS`
			: model.dims
		: null;

	let triggerEl: HTMLButtonElement;
	let menuEl: HTMLDivElement;
	let showPicker = false;
	let menuStyle = '';

	function togglePicker() {
		showPicker = !showPicker;
		if (showPicker) menuStyle = computeMenuStyle();
	}

	function computeMenuStyle(): string {
		if (!triggerEl) return '';
		const pos = computeFlippedMenuPosition(triggerEl, { width: 280 });
		const vertical = pos.top !== undefined ? `top: ${pos.top}px;` : `bottom: ${pos.bottom}px;`;
		return `left: ${pos.left}px; ${vertical}`;
	}

	function selectTab(id: string | null) {
		onPinTab?.(id);
		showPicker = false;
	}

	function handleOutsidePointerDown(e: PointerEvent) {
		if (!showPicker) return;
		const target = e.target as Node;
		if (!menuEl?.contains(target) && !triggerEl?.contains(target)) {
			showPicker = false;
		}
	}

	function handleOutsideKeydown(e: KeyboardEvent) {
		if (e.key !== 'Escape' || !showPicker) return;
		showPicker = false;
		// A document-level bubble listener runs before window's (capture goes
		// window->document->target, bubble reverses that), so stopping here
		// keeps GlobalChatPanel's <svelte:window on:keydown> from also closing
		// the whole chat panel on this same Escape press (same reasoning as
		// ChatInput's Tools/pin dropdowns).
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

{#if model.state === 'pinned-mismatch'}
	<div class="flex-shrink-0 border-t border-warning/35 bg-warning/[0.06] px-3 py-2" data-testid="chat-context-strip" data-strip-state="pinned-mismatch">
		<div class="flex items-center gap-2">
			<svg class="w-3.5 h-3.5 text-warning flex-shrink-0" fill="currentColor" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z" />
			</svg>
			<div class="min-w-0 flex-1 text-xs text-fg overflow-hidden text-ellipsis whitespace-nowrap">
				Pinned to
				<button type="button" bind:this={triggerEl} class="font-semibold hover:underline underline-offset-2" on:click={togglePicker}>
					{model.tabName}
				</button>
				{#if model.presetLabel}
					— <span class="text-fg-muted">{model.presetLabel}</span>
				{/if}
				{#if dimsText}
					<span class="font-mono text-2xs tabular-nums text-fg-muted">· {dimsText}</span>
				{/if}
			</div>
		</div>
		<div class="mt-1 pl-[22px] text-2xs text-fg-muted">
			Generate shows <span class="text-fg">{model.activeTabName}</span> — this chat isn't reading it.
		</div>
		<div class="mt-1.5 pl-[22px] flex items-center gap-2 flex-wrap">
			<button
				type="button"
				class="flex items-center gap-1.5 px-2.5 py-1 text-xs font-medium text-fg-muted border border-line-strong rounded hover:bg-surface-2 transition-colors whitespace-nowrap"
				on:click={() => onSwitchToPinned?.()}
			>
				<svg class="w-3 h-3" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" d="M13 7l5 5m0 0l-5 5m5-5H6" />
				</svg>
				Switch Generate to {model.tabName}
			</button>
			<button
				type="button"
				class="px-1.5 py-1 text-xs text-fg-subtle hover:text-fg-muted transition-colors whitespace-nowrap"
				on:click={() => selectTab(null)}
			>
				Unpin — follow {model.activeTabName}
			</button>
		</div>
	</div>
{:else if model.state === 'pinned-active'}
	<div class="flex-shrink-0 border-t border-line bg-surface-1 px-3 py-1.5 flex items-center gap-2" data-testid="chat-context-strip" data-strip-state="pinned-active">
		<svg class="w-3.5 h-3.5 text-warning flex-shrink-0" fill="currentColor" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24">
			<path stroke-linecap="round" stroke-linejoin="round" d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z" />
		</svg>
		<div class="min-w-0 flex-1 text-xs text-fg-muted overflow-hidden text-ellipsis whitespace-nowrap">
			Pinned to
			<button type="button" bind:this={triggerEl} class="font-medium text-fg hover:underline underline-offset-2" on:click={togglePicker}>
				{model.tabName}
			</button>
			{#if model.presetLabel}
				— <span class="text-fg-subtle">{model.presetLabel}</span>
			{/if}
			{#if dimsText}
				<span class="font-mono text-2xs tabular-nums text-fg-subtle">· {dimsText}</span>
			{/if}
		</div>
		<span class="font-mono text-2xs uppercase tracking-[0.08em] text-signal flex-shrink-0">active</span>
		<button
			type="button"
			class="flex-shrink-0 px-1.5 py-1 text-xs text-fg-subtle hover:text-fg-muted transition-colors"
			on:click={() => selectTab(null)}
		>
			Unpin
		</button>
	</div>
{:else}
	<div
		class="flex-shrink-0 border-t px-3 py-1.5 flex items-center gap-2 motion-safe:transition-colors motion-safe:duration-[1200ms] ease-out {flash
			? 'border-signal/35 bg-signal/[0.07]'
			: 'border-line bg-surface-1'}"
		data-testid="chat-context-strip"
		data-strip-state="following"
	>
		<svg
			class="w-3.5 h-3.5 flex-shrink-0 motion-safe:transition-colors motion-safe:duration-[1200ms] ease-out {flash
				? 'text-signal'
				: 'text-fg-subtle'}"
			fill="none"
			stroke="currentColor"
			stroke-width="2"
			viewBox="0 0 24 24"
		>
			<path stroke-linecap="round" stroke-linejoin="round" d="M2.036 12.322a1.012 1.012 0 010-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178z" />
			<path stroke-linecap="round" stroke-linejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
		</svg>
		<div
			class="min-w-0 flex-1 text-xs overflow-hidden text-ellipsis whitespace-nowrap motion-safe:transition-colors motion-safe:duration-[1200ms] ease-out {flash
				? 'text-fg'
				: 'text-fg-muted'}"
		>
			Reading
			<button type="button" bind:this={triggerEl} class="text-signal font-medium hover:underline underline-offset-2" on:click={togglePicker}>
				{model.tabName}
			</button>
			{#if model.presetLabel}
				— <span class="text-fg-subtle">{model.presetLabel}</span>
			{/if}
			{#if dimsText}
				<span class="font-mono text-2xs tabular-nums text-fg-subtle">· {dimsText}</span>
			{/if}
		</div>
	</div>
{/if}

{#if showPicker}
	<div
		use:portal
		bind:this={menuEl}
		class="fixed z-[9999] w-[280px] bg-surface-1 border border-line rounded-xl shadow-floating max-h-72 overflow-y-auto"
		style={menuStyle}
		data-testid="chat-context-strip-picker"
	>
		<div class="px-3 py-2 border-b border-line">
			<span class="font-mono text-2xs font-semibold text-fg-subtle uppercase tracking-[0.08em]">Pin to tab</span>
		</div>
		<button
			type="button"
			class="w-full flex items-start gap-2 px-3 py-2.5 text-left border-l-2 transition-colors {!pinnedTabId
				? 'bg-signal/[0.06] border-signal'
				: 'border-transparent hover:bg-surface-2'}"
			on:click={() => selectTab(null)}
		>
			<svg class="w-3.5 h-3.5 flex-shrink-0 mt-px {!pinnedTabId ? 'text-signal' : 'text-fg-subtle'}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16" />
			</svg>
			<div class="min-w-0 flex-1">
				<div class="text-xs font-medium text-fg">Follow active tab</div>
				<div class="text-2xs text-fg-subtle mt-0.5 leading-snug">Chat always reads whichever tab is open</div>
			</div>
			{#if !pinnedTabId}
				<svg class="w-3.5 h-3.5 flex-shrink-0 mt-px text-signal" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
				</svg>
			{/if}
		</button>
		<div class="h-px bg-line my-0.5"></div>
		{#each allTabs as tab (tab.id)}
			<button
				type="button"
				class="w-full flex items-center gap-2 px-3 py-2 text-left transition-colors {pinnedTabId === tab.id
					? 'bg-warning/[0.07]'
					: 'hover:bg-surface-2'}"
				on:click={() => selectTab(tab.id)}
			>
				<span class="w-1.5 h-1.5 rounded-full flex-shrink-0 {tab.id === activeTabId ? 'bg-signal' : 'bg-line-strong'}"></span>
				<div class="min-w-0 flex-1">
					<div class="text-xs text-fg truncate">{tab.name}</div>
					{#if tabPresetNames[tab.id]}
						<div class="text-2xs text-fg-subtle mt-0.5 truncate">{tabPresetNames[tab.id]}</div>
					{/if}
				</div>
				<span class="flex items-center gap-2 flex-shrink-0">
					{#if tab.id === activeTabId}
						<span class="font-mono text-2xs uppercase tracking-[0.06em] text-signal">active</span>
					{/if}
					{#if pinnedTabId === tab.id}
						<span class="flex items-center gap-1">
							<svg class="w-3 h-3 text-warning" fill="currentColor" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24">
								<path stroke-linecap="round" stroke-linejoin="round" d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z" />
							</svg>
							<span class="font-mono text-2xs uppercase tracking-[0.06em] text-warning">pinned</span>
						</span>
					{/if}
				</span>
			</button>
		{/each}
	</div>
{/if}
