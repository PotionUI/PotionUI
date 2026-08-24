<script lang="ts">
	import type { Snippet } from 'svelte';
	import { Button } from '$lib/components/ui';

	let {
		title,
		description,
		code,
		children
	}: { title: string; description?: string; code: string; children?: Snippet } = $props();

	let copied = $state(false);
	let copyError = $state(false);
	let copyTimer: ReturnType<typeof setTimeout> | undefined;

	async function copyCode() {
		try {
			if (!navigator.clipboard) throw new Error('Clipboard unavailable');
			await navigator.clipboard.writeText(code);
			copyError = false;
			copied = true;
			if (copyTimer) clearTimeout(copyTimer);
			copyTimer = setTimeout(() => (copied = false), 1600);
		} catch {
			copied = false;
			copyError = true;
		}
	}
</script>

<section class="border border-line rounded-lg overflow-hidden bg-surface-1 shadow-raised">
	<div class="px-4 py-3 border-b border-line">
		<h2 class="text-sm font-semibold text-fg">{title}</h2>
		{#if description}<p class="text-xs text-fg-muted mt-1">{description}</p>{/if}
	</div>
	<div class="p-4 sm:p-6 min-h-24 flex flex-wrap items-center gap-3 bg-canvas dot-grid">
		{@render children?.()}
	</div>
	<div class="border-t border-line bg-surface-2">
		<div class="flex items-center justify-between px-3 py-2 border-b border-line">
			<span class="label mb-0">Svelte</span>
			<Button variant="ghost" size="xs" icon={copied ? 'check' : 'copy'} onclick={copyCode}>
				{copied ? 'Copied' : 'Copy'}
			</Button>
			<span class="sr-only" aria-live="polite">{copied ? 'Code copied' : copyError ? 'Unable to copy code' : ''}</span>
		</div>
		<pre class="p-4 overflow-x-auto text-xs text-fg-muted"><code>{code}</code></pre>
	</div>
</section>
