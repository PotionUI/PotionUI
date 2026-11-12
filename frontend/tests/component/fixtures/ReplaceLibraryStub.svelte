<script lang="ts">
	let {
		isOpen = false,
		title = '',
		mediaType = undefined,
		onSelect = () => {},
		onClose = () => {}
	}: {
		isOpen?: boolean;
		title?: string;
		mediaType?: string;
		onSelect?: (upload: unknown) => void;
		onClose?: () => void;
	} = $props();

	const upload = {
		filename: 'lib-new.png',
		original_filename: 'lib-new.png',
		url: '/api/media/uploads/lib-new.png',
		media_type: 'image',
		width: 320,
		height: 200,
		size: 500
	};
</script>

{#if isOpen}
	<div data-stub="library" data-title={title} data-media-type={mediaType ?? ''}>
		<button type="button" data-stub-pick onclick={() => onSelect(upload)}>pick</button>
		<button type="button" data-stub-close onclick={() => onClose()}>close</button>
	</div>
{/if}
