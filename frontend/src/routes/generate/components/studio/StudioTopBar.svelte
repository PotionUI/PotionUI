<script lang="ts">
	export let tabName: string;
	export let presetName: string | undefined = undefined;
	export let modeLabel: string | undefined = undefined;
	export let onOpenPresetSheet: () => void;
	export let onOpenChatSheet: () => void;
</script>

<div
	class="studio-topbar pointer-events-none absolute inset-x-0 top-0 flex items-center gap-2 p-3"
	style="background: linear-gradient(rgb(var(--canvas) / 0.9), rgb(var(--canvas) / 0));"
>
	<button
		type="button"
		class="pointer-events-auto flex flex-shrink-0 items-center gap-1.5 rounded-full border border-line-strong bg-surface-1/85 px-2.5 py-1.5 text-2xs font-medium text-fg backdrop-blur-sm"
		aria-label="Open tabs"
		on:click={onOpenPresetSheet}
	>
		{tabName}
		<svg class="h-2.5 w-2.5 text-fg-subtle" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
			<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
		</svg>
	</button>

	<button
		type="button"
		class="pointer-events-auto flex min-w-0 flex-1 items-center justify-center gap-1.5 rounded-full border border-line-strong bg-surface-1/85 px-3 py-1.5 text-sm font-medium text-fg backdrop-blur-sm"
		aria-label="Open preset and session"
		on:click={onOpenPresetSheet}
	>
		<span class="min-w-0 truncate">
			{presetName ?? 'Select preset'}
			{#if modeLabel}<span class="font-normal text-fg-subtle">· {modeLabel}</span>{/if}
		</span>
		<svg class="h-2.5 w-2.5 flex-shrink-0 text-fg-subtle" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
			<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
		</svg>
	</button>

	<button
		type="button"
		class="studio-ai-trigger pointer-events-auto flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full"
		aria-label="Open AI chat"
		on:click={onOpenChatSheet}
	>
		<span class="flex h-full w-full items-center justify-center rounded-full bg-surface-1 text-fg">
			<svg class="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
				<path
					stroke-linecap="round"
					stroke-linejoin="round"
					stroke-width="1.75"
					d="M8 10h8m-8 4h5m-9.5 6.5v-3.4A8.5 8.5 0 1112 20.5H5.5z"
				/>
			</svg>
		</span>
	</button>
</div>

<style>
	/* Same gradient-ring idiom as Sidebar.svelte's .ai-chat-trigger. */
	.studio-ai-trigger {
		border: 1px solid transparent;
		padding: 1px;
		background:
			linear-gradient(rgb(var(--canvas)), rgb(var(--canvas))) padding-box,
			linear-gradient(135deg, rgb(var(--ai-1)), rgb(var(--ai-2)), rgb(var(--ai-3))) border-box;
	}
</style>
