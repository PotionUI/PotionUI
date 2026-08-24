<script lang="ts">
	import { chatModes, resolveModeName } from '$lib/stores/chatModes';
	import { chatSession, modeLocked } from '$lib/stores/chatSession';
	import ChatModeSelector from '$lib/components/chat/ChatModeSelector.svelte';
	import Badge from '$lib/components/ui/Badge.svelte';
	import { formatTokenCount } from '$lib/utils/chat';

	// Provider+Model (LLM config) selection
	export let llmConfigs: any[] = [];
	export let selectedConfigId = '';
	export let onSelectConfig: (id: string) => void;

	// Mode selection (locks once the conversation has messages)
	export let onSelectMode: (id: string) => void;

	// Vision status
	export let supportsVision = false;
	export let hasImageAttached = false;

	// Token usage
	export let totalTokensUsed = 0;
	export let currentContextSize = 0;

	export let onClose: (() => void) | undefined = undefined;

	// History view hides everything except the leading controls and Close.
	export let selectorsHidden = false;

	let showModelDropdown = false;

	$: mode = $chatSession.mode;

	$: llmConfigOptions = llmConfigs.map((config) => ({
		value: config.id,
		label: config.name
	}));
	$: selectedModelName = llmConfigs.find((c) => c.id === selectedConfigId)?.name || 'Model';
</script>

<div class="flex items-center gap-1.5 px-2 py-1.5 border-b border-line bg-surface-1 flex-shrink-0">
	<!-- Leading area (session selector / history controls, provided by parent) -->
	<slot name="leading" />

	<!-- Model selector -->
	{#if !selectorsHidden && llmConfigs.length > 0}
		<div class="relative flex-shrink-0">
			<button
				type="button"
				title="LLM Model"
				class="flex items-center gap-1 px-1.5 py-1.5 text-xs rounded transition-colors {showModelDropdown ? 'bg-surface-2 text-fg-muted' : 'text-fg-muted hover:text-fg-muted hover:bg-surface-2'}"
				on:click={() => (showModelDropdown = !showModelDropdown)}
			>
				<svg class="w-3.5 h-3.5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
				</svg>
				<span class="max-w-[120px] truncate">{selectedModelName || 'Model'}</span>
				<svg class="w-2.5 h-2.5 flex-shrink-0 text-fg-subtle" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
				</svg>
			</button>
			{#if showModelDropdown}
				<div class="fixed inset-0 z-30" role="button" tabindex="-1" aria-label="Close model selector" on:click={() => (showModelDropdown = false)} on:keydown={(e) => { if (e.key === 'Escape') showModelDropdown = false; }}></div>
				<div class="absolute z-40 top-full left-0 mt-1 w-64 bg-surface-1 border border-line rounded-lg shadow-floating max-h-60 overflow-y-auto">
					{#each llmConfigOptions as option}
						<button
							type="button"
							class="w-full px-3 py-2 text-left text-xs hover:bg-surface-2 transition-colors {option.value === selectedConfigId ? 'text-fg-muted bg-surface-2' : 'text-fg-muted'}"
							on:click={() => { onSelectConfig(option.value); showModelDropdown = false; }}
						>
							{option.label}
						</button>
					{/each}
				</div>
			{/if}
		</div>
	{/if}

	<!-- Mode selector (hidden once locked — the Badge below takes over) -->
	{#if !selectorsHidden && !$modeLocked && $chatModes.modes.length > 0}
		<ChatModeSelector
			modes={$chatModes.modes}
			selected={mode}
			locked={$modeLocked}
			onSelect={onSelectMode}
		/>
	{/if}

	<!-- Scope badge: this chat's fixed mode, once the session can no longer change it -->
	{#if !selectorsHidden && $modeLocked}
		<span title="Mode is fixed for this conversation — start a new chat to change it" class="flex-shrink-0">
			<Badge variant="signal" size="sm" class="font-mono uppercase tracking-[0.07em]">
				{resolveModeName(mode, $chatModes.modes)}
			</Badge>
		</span>
	{/if}

	<!-- Icon toggles -->
	<div class="flex items-center gap-0.5 flex-shrink-0 ml-auto">
		{#if !selectorsHidden}
		<!-- Badges -->
		{#if supportsVision}
			<span class="font-mono text-2xs px-1 py-0.5 bg-surface-2 text-fg-muted rounded font-medium" title="Vision capable">V</span>
		{/if}
		{#if hasImageAttached}
			<span class="font-mono text-2xs px-1 py-0.5 bg-surface-2 text-success rounded font-medium" title="Image attached">IMG</span>
		{/if}

		<!-- Token usage -->
		{#if totalTokensUsed > 0 || currentContextSize > 0}
			<span class="text-[10px] text-fg-subtle font-mono hidden md:inline" title="ctx: context/prompt tokens | total: cumulative tokens used">
				{#if currentContextSize > 0}ctx:{formatTokenCount(currentContextSize)}{/if}{#if currentContextSize > 0 && totalTokensUsed > 0}|{/if}{#if totalTokensUsed > 0}{formatTokenCount(totalTokensUsed)}{/if}
			</span>
		{/if}
		{/if}

		<!-- Close button -->
		{#if onClose}
			<div class="w-px h-4 bg-surface-2"></div>
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
