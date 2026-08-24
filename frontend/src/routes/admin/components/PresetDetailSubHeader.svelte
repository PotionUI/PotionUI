<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';

	// Shared "icon-box + title + description" header used by PresetsTab's
	// Configuration/Form/Access detail sub-tabs — dedup only, no visual change.
	export let icon: string;
	export let title: string;
	export let description: string;
</script>

<div class="flex items-start gap-3 mb-5">
	<div class="w-9 h-9 rounded-lg bg-signal/10 text-signal flex items-center justify-center flex-shrink-0">
		<Icon name={icon} className="w-4 h-4" />
	</div>
	<div>
		<h2 class="text-base font-semibold text-fg">{title}</h2>
		<p class="text-sm text-fg-muted mt-1">{description}</p>
	</div>
</div>
