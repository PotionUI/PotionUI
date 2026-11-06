<script lang="ts">
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Badge } from '$lib/components/ui';
	import type { ModelRoot } from '$lib/services/api/models';
	import { STATE_LABEL, agoText } from './derive';

	let { root, always = false }: { root: ModelRoot; always?: boolean } = $props();
	const variant = $derived(root.state === 'online' ? 'success' : root.state === 'offline' ? 'danger' : 'warning');
	const tip = $derived(
		root.state === 'online'
			? `Online. Checked ${agoText(root.state_checked_at)}.`
			: `${root.state_reason ?? STATE_LABEL[root.state]} Checked ${agoText(root.state_checked_at)}.`
	);
</script>

{#if root.state !== 'online' || always}
	<Tooltip text={tip} position="top">
		<Badge {variant} size="sm" dot>{STATE_LABEL[root.state]}</Badge>
	</Tooltip>
{/if}
