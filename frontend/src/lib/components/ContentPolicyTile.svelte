<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';

	let {
		kind,
		blockedCount = 0,
		total = 0,
		class: className = ''
	}: {
		kind: 'blocked' | 'preview' | 'checking';
		blockedCount?: number;
		total?: number;
		class?: string;
	} = $props();

	const copy = $derived(
		kind === 'blocked'
			? {
					icon: 'shield',
					title: 'Blocked by content policy',
					detail: total > 0 ? `${blockedCount} of ${total} blocked` : ''
				}
			: kind === 'preview'
				? {
						icon: 'shield',
						title: 'Rendering',
						detail: 'Previews are hidden by the content policy'
					}
				: { icon: 'clock', title: 'Being checked', detail: 'Shown once the content check finishes' }
	);
</script>

<div
	class="flex h-full min-h-[8rem] w-full flex-col items-center justify-center gap-1.5 bg-surface-2 px-4 text-center {className}"
	role="status"
	data-content-policy-tile={kind}
>
	<Icon name={copy.icon} className="h-8 w-8 text-fg-subtle" strokeWidth={1.5} />
	<p class="text-sm font-medium text-fg">{copy.title}</p>
	{#if copy.detail}
		<p class="text-sm text-fg-muted {kind === 'blocked' ? 'font-mono tabular-nums' : ''}">{copy.detail}</p>
	{/if}
</div>
