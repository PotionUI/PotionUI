<script lang="ts">
	let {
		isOpen = false,
		onConfirm = undefined
	}: {
		isOpen?: boolean;
		onConfirm?: (keys: string[], items: unknown[]) => void;
	} = $props();

	const picked = [
		{ key: 'g1:7', mediaType: 'image', file: { id: 7, file_path: 'out/g1/face.png', file_type: 'image' } },
		{ key: 'g1:8', mediaType: 'audio', file: { id: 8, file_path: 'out/g1/voice.wav', file_type: 'audio' } }
	];
</script>

{#if isOpen}
	<button type="button" data-stub-history-confirm onclick={() => onConfirm?.(['g1:7', 'g1:8'], picked)}>confirm</button>
{/if}
