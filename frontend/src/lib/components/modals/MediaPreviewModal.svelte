<script lang="ts">
	import { onDestroy } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';

	// Props
	export let isOpen: boolean = false;
	export let onClose: () => void;
	export let kind: 'image' | 'video' | 'audio' = 'image';
	export let url: string;
	export let label: string = 'Preview';

	// Handle ESC key to close
	function handleKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape' && isOpen) {
			onClose();
		}
	}

	// Add/remove keyboard listener based on modal state
	$: if (typeof window !== 'undefined') {
		if (isOpen) {
			document.addEventListener('keydown', handleKeydown);
			document.body.style.overflow = 'hidden';
		} else {
			document.removeEventListener('keydown', handleKeydown);
			document.body.style.overflow = '';
		}
	}

	onDestroy(() => {
		if (typeof window !== 'undefined') {
			document.removeEventListener('keydown', handleKeydown);
			document.body.style.overflow = '';
		}
	});
</script>

{#if isOpen}
	<div
		class="fixed inset-0 z-[100] flex items-center justify-center bg-black/90"
		on:click={onClose}
		on:keydown={(e) => { if (e.key === 'Escape') onClose(); }}
		role="dialog"
		aria-modal="true"
		aria-label="Media preview"
		tabindex="-1"
	>
		<!-- Close button -->
		<button
			type="button"
			class="absolute top-4 right-4 p-2 text-white/80 hover:text-white bg-black/50 hover:bg-black/70 rounded transition-colors z-10"
			on:click|stopPropagation={onClose}
			aria-label="Close preview"
		>
			<svg class="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
				<path
					stroke-linecap="round"
					stroke-linejoin="round"
					stroke-width="2"
					d="M6 18L18 6M6 6l12 12"
				/>
			</svg>
		</button>

		{#if label}
			<p class="absolute top-4 left-4 max-w-[70%] truncate text-sm text-white/80">{label}</p>
		{/if}

		<!-- Media container -->
		<div
			class="max-w-[95vw] max-h-[95vh] flex items-center justify-center"
			role="presentation"
			on:click|stopPropagation
			on:keydown|stopPropagation
		>
			{#if kind === 'video'}
				<!-- svelte-ignore a11y-media-has-caption -->
				<video src={url} class="max-w-full max-h-[95vh] object-contain" controls autoplay muted playsinline>
					<track kind="captions" />
				</video>
			{:else if kind === 'audio'}
				<div class="flex w-[min(90vw,28rem)] flex-col items-center gap-4 rounded-lg bg-surface-1 p-6">
					<Icon name="audio" className="h-10 w-10 text-fg-subtle" strokeWidth={1.5} />
					<!-- svelte-ignore a11y-media-has-caption -->
					<audio src={url} class="w-full" controls autoplay>
						Your browser does not support the audio element.
					</audio>
				</div>
			{:else}
				<img src={url} alt={label} class="max-w-full max-h-[95vh] object-contain" />
			{/if}
		</div>

		<!-- Hint text -->
		<p class="absolute bottom-4 left-1/2 -translate-x-1/2 text-white/60 text-sm">
			Click outside or press ESC to close
		</p>
	</div>
{/if}
