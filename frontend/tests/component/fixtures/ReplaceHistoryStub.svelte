<script lang="ts">
	let {
		isOpen = false,
		multiple = false,
		title = '',
		mediaType = undefined,
		onSelect = () => {},
		onClose = () => {}
	}: {
		isOpen?: boolean;
		multiple?: boolean;
		title?: string;
		mediaType?: string;
		onSelect?: (generation: unknown, file: unknown) => void;
		onClose?: () => void;
	} = $props();

	const generation = { id: 'gen-new', prompt: 'a new one' };
	const file = {
		id: 91,
		file_path: 'generations/gen-new/new.png',
		file_type: 'image',
		width: 640,
		height: 480,
		file_size: 1000
	};
</script>

{#if isOpen}
	<div data-stub="history" data-multiple={String(multiple)} data-title={title} data-media-type={mediaType ?? ''}>
		<button type="button" data-stub-pick onclick={() => onSelect(generation, file)}>pick</button>
		<button type="button" data-stub-close onclick={() => onClose()}>close</button>
	</div>
{/if}
