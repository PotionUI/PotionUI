<script lang="ts">
	import { onMount } from 'svelte';
	import type { ChatMode } from '$lib/types/chat';
	import { deriveModeScopeMismatch } from '$lib/chat/modeScopeNotice';

	export let modes: ChatMode[] = [];
	export let selected: string;
	export let locked = false;
	export let onSelect: (id: string) => void;
	/** The scope the current page resolves to, so a locked chip that no longer
	 * agrees with it can say so. Null while unresolved. */
	export let pageModeId: string | null = null;
	export let onNewChat: (() => void) | undefined = undefined;

	let open = false;
	let triggerEl: HTMLButtonElement;
	let menuEl: HTMLDivElement;

	$: selectedMode = modes.find((m) => m.id === selected);
	$: scopeMismatch = deriveModeScopeMismatch(selected, pageModeId, modes, locked);

	function choose(id: string) {
		open = false;
		if (id !== selected) onSelect(id);
	}

	function toggleOpen() {
		open = !open;
	}

	function handleOutsidePointerDown(e: PointerEvent) {
		if (!open) return;
		const target = e.target as Node;
		if (!menuEl?.contains(target) && !triggerEl?.contains(target)) {
			open = false;
		}
	}

	function handleOutsideKeydown(e: KeyboardEvent) {
		if (e.key !== 'Escape' || !open) return;
		open = false;
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

<div class="relative">
	<button
		bind:this={triggerEl}
		type="button"
		class="mode-chip"
		title={locked ? 'Mode is fixed after the conversation starts' : 'Choose assistant mode'}
		disabled={locked}
		data-testid="chat-mode-selector-trigger"
		aria-expanded={open}
		on:click={toggleOpen}
	>
		<svg class="icon"><use href="#i-sparkles" /></svg>
		<span>{selectedMode?.name || selected}</span>
		{#if locked}
			<svg class="icon lock"><use href="#i-lock" /></svg>
		{/if}
	</button>
	{#if scopeMismatch}
		<p
			class="absolute right-0 top-full mt-1 whitespace-nowrap text-2xs text-fg-subtle"
			data-testid="chat-mode-scope-notice"
		>
			Started in {scopeMismatch.conversationModeName}. This chat stays in {scopeMismatch.conversationModeName};
			<button
				type="button"
				class="text-signal hover:underline underline-offset-2"
				data-testid="chat-mode-scope-new-chat"
				on:click={() => onNewChat?.()}
			>
				start a new chat
			</button>
			to use {scopeMismatch.pageModeName}.
		</p>
	{/if}
</div>
{#if open && !locked}
	<div class="floating-menu mode-menu" bind:this={menuEl} data-testid="chat-mode-selector-menu">
		<div class="menu-label">Assistant mode</div>
		{#each modes as mode}
			<button type="button" class="model-option" class:selected={mode.id === selected} on:click={() => choose(mode.id)}>
				<span class="model-option-copy">
					<strong>{mode.name}</strong>
					{#if mode.description}<small>{mode.description}</small>{/if}
				</span>
				{#if mode.id === selected}
					<svg class="icon check"><use href="#i-check" /></svg>
				{/if}
			</button>
		{/each}
		{#if modes.length === 0}
			<div class="tool-row-empty">No modes available</div>
		{/if}
	</div>
{/if}
