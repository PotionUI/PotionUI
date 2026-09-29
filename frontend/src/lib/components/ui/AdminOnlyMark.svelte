<script lang="ts">
	import Icon from '../Icon.svelte';
	import Tooltip from '../Tooltip.svelte';

	const TEXT = 'Only admins see this';
</script>

<Tooltip text={TEXT}>
	<span class="inline-flex shrink-0 text-fg-subtle" role="img" aria-label={TEXT} data-admin-only-mark>
		<Icon name="shield" className="w-3.5 h-3.5" />
	</span>
</Tooltip>
