<script lang="ts">
	import { Kbd } from '$lib/components/ui';

	export let message: string = 'Select an item to view details';
	export let icon: 'document' | 'folder' | 'command' | 'style' = 'document';
</script>

<div class="dot-grid flex items-center justify-center h-full">
	<div class="text-center max-w-sm">
		<!-- Icon -->
		<div class="w-16 h-16 mx-auto mb-4 rounded-full bg-surface-3/30 flex items-center justify-center">
			{#if icon === 'document'}
				<svg class="w-8 h-8 text-fg-subtle" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path
						stroke-linecap="round"
						stroke-linejoin="round"
						stroke-width="1.5"
						d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
					/>
				</svg>
			{:else if icon === 'folder'}
				<svg class="w-8 h-8 text-fg-subtle" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path
						stroke-linecap="round"
						stroke-linejoin="round"
						stroke-width="1.5"
						d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z"
					/>
				</svg>
			{:else if icon === 'command'}
				<svg class="w-8 h-8 text-fg-subtle" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path
						stroke-linecap="round"
						stroke-linejoin="round"
						stroke-width="1.5"
						d="M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"
					/>
				</svg>
			{:else if icon === 'style'}
				<svg class="w-8 h-8 text-fg-subtle" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path
						stroke-linecap="round"
						stroke-linejoin="round"
						stroke-width="1.5"
						d="M4 5a1 1 0 011-1h14a1 1 0 011 1v2a1 1 0 01-1 1H5a1 1 0 01-1-1V5zM4 13a1 1 0 011-1h6a1 1 0 011 1v6a1 1 0 01-1 1H5a1 1 0 01-1-1v-6zM16 13a1 1 0 011-1h2a1 1 0 011 1v6a1 1 0 01-1 1h-2a1 1 0 01-1-1v-6z"
					/>
				</svg>
			{/if}
		</div>

		<!-- Message -->
		<p class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">{message}</p>

		<!-- Hint -->
		<p class="text-fg-subtle text-xs mt-2 inline-flex items-center gap-1">
			Use <Kbd keys="arrow keys" /> to navigate
		</p>
	</div>
</div>
