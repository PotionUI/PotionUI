<script lang="ts">
	import MediaEditors from '$lib/media/editors/MediaEditors.svelte';
	import { libraryStore } from '$lib/stores/library';
	import { toasts } from '$lib/stores/toast';
	import type { MediaToolModalProps } from '$lib/tools/tools';
	import { editImageRequest } from '../tools/editImage';

	let { context, onClose }: MediaToolModalProps = $props();

	let request = $derived(editImageRequest(context));

	async function handleResult() {
		if (context.scope === 'library') await libraryStore.showNewRows();
		toasts.success('Saved to your library as a new image');
	}
</script>

<MediaEditors {request} {onClose} onResult={handleResult} />
