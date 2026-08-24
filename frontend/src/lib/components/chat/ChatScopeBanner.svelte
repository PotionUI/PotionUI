<script lang="ts">
	/**
	 * Docked above the composer whenever the active session's mode doesn't
	 * match the current route's resolved mode (same placement idiom as
	 * ApprovalDock — never an inline message card).
	 */
	import { slide } from 'svelte/transition';

	export let sessionModeName: string;
	export let routeModeName: string;
	export let onStartNew: () => void;
	export let onDismiss: () => void;
</script>

<div
	class="flex-shrink-0 border-t border-line bg-surface-1 px-3 py-2 flex items-center gap-2"
	transition:slide={{ duration: 150 }}
>
	<svg class="w-3.5 h-3.5 text-fg-subtle flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
		<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
	</svg>
	<span class="text-xs text-fg-muted min-w-0">
		This chat runs in the <span class="font-mono text-2xs uppercase tracking-[0.07em] text-signal">{sessionModeName}</span> scope and keeps its tools there.
	</span>
	<div class="ml-auto flex items-center gap-1.5 flex-shrink-0">
		<button
			type="button"
			class="px-2.5 py-1 text-xs font-medium text-fg-muted border border-line-strong rounded hover:bg-surface-2 transition-colors whitespace-nowrap"
			on:click={onStartNew}
		>
			New {routeModeName} chat
		</button>
		<button
			type="button"
			title="Dismiss"
			class="p-1 text-fg-subtle hover:text-fg-muted hover:bg-surface-2 rounded transition-colors"
			on:click={onDismiss}
		>
			<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
			</svg>
		</button>
	</div>
</div>
